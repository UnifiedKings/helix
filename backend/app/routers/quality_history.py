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
    records = {}
    for event in sorted(events, key=lambda e: (e.created_at, e.id)):
        if event.event != 'quality_policy_snapshot' or event.job_id in records:
            continue
        try:
            data = json.loads(event.data_json)
            if not isinstance(data, dict) or not data.get('policy_name'):
                continue
            records[event.job_id] = dict(kind='profile', profile=data['policy_name'],
                                         original=data.get('before'), result=data.get('after'),
                                         completed_at=event.created_at)
        except (TypeError, ValueError):
            continue
    for job in jobs:
        if job.id in records:
            continue
        # Status alone cannot prove when or under which rules upgrade occurred.
        # Never present today's assigned/default profile as the historical one.
        records[job.id] = {
            'kind': 'legacy' if job.upgraded_at or job.completion_source == 'slskd' else 'not_recorded',
            'profile': None,
            'original': {'codec': job.original_codec, 'bitrate':job.original_bitrate,
                         'sample_rate':job.original_sample_rate,'bit_depth':job.original_bit_depth},
            'result': {'codec': job.current_codec,'bitrate':job.current_bitrate,
                       'sample_rate':job.current_sample_rate,'bit_depth':job.current_bit_depth},
            'completed_at':job.upgraded_at,
        }
    return records


@router.get('/history-records')
def upgrade_history(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not can_import_to_subsonic(db, user):
        raise HTTPException(403, 'Subsonic add permission is required')
    jobs = db.execute(select(QualityUpgradeJob)).scalars().all()
    events = db.execute(select(QualityUpgradeEvent).where(
        QualityUpgradeEvent.event == 'quality_policy_snapshot')).scalars().all()
    return {'records': history_payload(jobs, events)}
