"""Historical upgrade provenance: no backdating current profile settings."""
from __future__ import annotations

import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..auth import get_current_user
from ..db import get_db
from ..models import User
from ..quality_models import QualityUpgradeEvent, QualityUpgradeJob
from ..subsonic_permissions import can_import_to_subsonic

router = APIRouter(prefix='/api/quality-upgrades', tags=['quality-upgrades'])


def history_payload(jobs, events):
    """Return historical first-original and most-recent-result, never today's target.

    A track may now have multiple profile-managed replacements. Earlier legacy
    versions survive in 'upgrade_version_rebased' events even when the mutable
    job.original_* fields are advanced to permit another verified replacement.
    """
    grouped = {}
    for event in sorted(events, key=lambda e: (e.created_at, e.id)):
        if event.event not in {'quality_policy_snapshot', 'upgrade_version_rebased'}:
            continue
        try:
            data = json.loads(event.data_json or '{}')
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            grouped.setdefault(event.job_id, []).append((event, data))
    records = {}
    for job in jobs:
        snapshots = [(event, data) for event, data in grouped.get(job.id, [])
                     if event.event == 'quality_policy_snapshot' and data.get('policy_name')]
        rebases = [(event, data) for event, data in grouped.get(job.id, [])
                   if event.event == 'upgrade_version_rebased']
        first_before = (rebases[0][1].get('original') if rebases else None)
        if not first_before and snapshots:
            first_before = snapshots[0][1].get('before')
        if not first_before:
            first_before = {key: getattr(job, 'original_' + key) for key in
                            ('codec', 'bitrate', 'sample_rate', 'bit_depth')}
        last = snapshots[-1] if snapshots else None
        records[job.id] = {
            'kind': ('profile' if snapshots else
                     'legacy' if job.upgraded_at or job.completion_source == 'slskd' else 'not_recorded'),
            'profile': last[1]['policy_name'] if last else None,
            'original': first_before,
            'result': (last[1].get('after') if last else None) or
                      {key: getattr(job, 'current_' + key) for key in
                       ('codec', 'bitrate', 'sample_rate', 'bit_depth')},
            'completed_at': last[0].created_at if last else job.upgraded_at,
            'version_count': len(snapshots) + (1 if rebases and not snapshots else 0),
        }
    return records


@router.get('/history-records')
def upgrade_history(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not can_import_to_subsonic(db, user):
        raise HTTPException(403, 'Subsonic add permission is required')
    jobs = db.execute(select(QualityUpgradeJob)).scalars().all()
    events = db.execute(select(QualityUpgradeEvent).where(
        QualityUpgradeEvent.event.in_(['quality_policy_snapshot', 'upgrade_version_rebased']))).scalars().all()
    return {'records': history_payload(jobs, events)}
