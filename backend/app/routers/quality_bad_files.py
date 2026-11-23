"""Verified bad-file reports, including older and previously upgraded Helix-owned copies.

Never automatically adopt arbitrary library tracks. Explicit confirmation is
required when historical fingerprints are absent; existing files are left in place.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..auth import get_current_user
from ..db import get_db
from ..models import User
from ..quality_models import QualityUpgradeJob, QualityUpgradeMetadata, QualityUpgradeEvent
from ..quality_bad_files import QualityBadFile, active_bad_report
from ..quality_source_registry import record_rejection
from ..quality_profile_store import QualityProfile, QualityProfileAssignment
from ..quality_profile_policy import normalize_profile
from ..quality_upgrade_service import _sha256_file, _audio_info, _fingerprint_file
from ..realtime import schedule_quality_upgrades_changed
from ..subsonic_permissions import can_import_to_subsonic

router = APIRouter(prefix='/api/quality-upgrades', tags=['quality-upgrades'])

ACTIVE = {'searching','waiting_search','waiting_peer','downloading','validating','tagging','replacing','reverting'}

def allowed(db: Session, user: User) -> None:
    if not can_import_to_subsonic(db, user):
        raise HTTPException(403, 'Subsonic add permission is required')

def require_job(db: Session, job_id: str) -> QualityUpgradeJob:
    job = db.get(QualityUpgradeJob, job_id)
    if job is None:
        raise HTTPException(404, 'Quality upgrade job not found')
    return job

@router.get('/bad-files')
def list_bad_files(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    allowed(db,user)
    rows = db.execute(select(QualityBadFile)).scalars().all()
    return {'items': [{'job_id': r.job_id, 'reason': r.reason, 'reported_at': r.created_at}
                      for r in rows if (job := db.get(QualityUpgradeJob, r.job_id)) and active_bad_report(db, job)]}

def _recorded_source(job: QualityUpgradeJob) -> Path:
    """Use only a path explicitly recorded by Helix, never a title-based match."""
    raw = job.library_path if job.status == 'upgraded' else job.original_path
    if not raw:
        raise HTTPException(409, 'NO_RECORDED_PATH: Helix has no recorded file path for this job')
    root = Path(__import__('os').environ.get('HELIX_MUSIC_LIBRARY_ROOT', '/data/music')).resolve()
    path = Path(raw).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        raise HTTPException(409, 'OUTSIDE_LIBRARY: Recorded file is outside the configured music library')
    if not path.is_file():
        raise HTTPException(409, 'FILE_MISSING: The recorded library file no longer exists')
    return path


def _legacy_record_is_confirmable(db: Session, job: QualityUpgradeJob, path: Path) -> bool:
    """Older Helix jobs may predate SHA-256 tracking and store Beets paths.

    The upgrade job itself is evidence of Helix import provenance. An exact
    recorded path under the library root, matching the old recorded file size
    when available, and *explicit user confirmation* are still required.
    A successful older slskd upgrade is similarly identified by its job state.
    No directory-name inference or broad library search is performed.
    """
    if not job.yt_video_id:
        return False
    if job.status == 'upgraded':
        return job.completion_source == 'slskd' and bool(job.library_path)
    return bool(job.original_path)


@router.post('/{job_id}/mark-bad')
def mark_bad(job_id: str, payload: dict[str,Any] = Body(...), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    allowed(db, user)
    job = require_job(db, job_id)
    if job.status in {'reverted', 'reverting', 'externally_modified'} or job.completion_source == 'external':
        raise HTTPException(409, 'OWNERSHIP_PROTECTED: This copy is reverted or was modified outside Helix')
    reason = str(payload.get('reason') or '').strip()[:400]
    if not reason:
        raise HTTPException(422, 'A reason for reporting the file is required')
    path = _recorded_source(job)
    meta = db.get(QualityUpgradeMetadata, job_id)
    if meta and meta.provenance not in ('helix_imported', ''):
        raise HTTPException(409, 'OWNERSHIP_PROTECTED: Helix does not own this copy')
    stored_hash = ((meta.current_sha256 if job.status == 'upgraded' else meta.original_sha256) if meta else '') or ''
    try:
        size, mtime = _fingerprint_file(path)
        actual_hash = _sha256_file(path)
    except OSError as exc:
        raise HTTPException(409, 'VERIFY_FAILED: Cannot read the recorded library file') from exc
    if stored_hash and actual_hash != stored_hash:
        raise HTTPException(409, 'FINGERPRINT_MISMATCH: The file changed outside Helix; report blocked')
    if not stored_hash:
        # For an old original copy, a changed recorded size is hard evidence of
        # external modification; explicit confirmation must not override it.
        if job.status != 'upgraded' and job.original_size and size != job.original_size:
            raise HTTPException(409, 'SIZE_MISMATCH: The historical library copy changed outside Helix')
        if not _legacy_record_is_confirmable(db, job, path):
            raise HTTPException(409, 'UNVERIFIED_OWNERSHIP: The recorded path cannot be safely associated with a Helix-managed file')
        if not payload.get('confirm_legacy_ownership'):
            raise HTTPException(409, 'LEGACY_CONFIRM_REQUIRED: No historical fingerprint exists. Confirm that this exact library copy is still managed by Helix and has not been manually replaced.')
    if meta is None:
        meta = QualityUpgradeMetadata(job_id=job_id, provenance='helix_imported')
        db.add(meta)
    # A previous successful upgrade is now the physical baseline for repair.
    # The existing worker only consumes original_path, so rebasing the verified
    # Helix-created copy allows it to use the same safe replacement transaction.
    # Record the rejected *content*, and its Soulseek source only when the
    # current library version is known to have been successfully supplied by
    # slskd. A pending/failed candidate is NOT the current library source.
    record_rejection(
        db,
        username=job.slskd_username if job.completion_source == 'slskd' else '',
        filename=job.slskd_filename if job.completion_source == 'slskd' else '',
        size=job.slskd_size if job.completion_source == 'slskd' else 0,
        sha256=actual_hash,
        reason=reason,
        job_id=job_id,
    )
    if job.status == 'upgraded':
        prior = {
            'original_path': job.original_path,
            'current_path': job.library_path,
            'original_sha256': meta.original_sha256,
            'current_sha256': meta.current_sha256,
            'completed_at': str(job.upgraded_at or ''),
        }
        info = _audio_info(path)
        job.original_path = str(path)
        job.original_codec = str(info['codec'])
        job.original_bitrate = int(info['bitrate'])
        job.original_sample_rate = int(info['sample_rate'])
        job.original_bit_depth = int(info['bit_depth'])
        job.subsonic_song_id = ''  # Re-resolve the library track from its recorded identity.
        job.upgraded_at = None
        job.completion_source = ''
        db.add(QualityUpgradeEvent(job_id=job_id, event='repair_baseline',
            message='Verified previous Helix upgrade adopted as repair baseline',
            data_json=json.dumps(prior)))
    if job.status != 'upgraded':
        record_rejection(db, sha256=actual_hash, reason=reason, job_id=job_id)
    meta.provenance = 'helix_imported'
    meta.original_sha256 = actual_hash
    meta.current_sha256 = actual_hash
    meta.updated_at = datetime.utcnow()
    job.original_size = size
    job.original_mtime_ns = mtime
    job.library_path = str(path)
    report = db.get(QualityBadFile, job_id)
    if report is None:
        report = QualityBadFile(job_id=job_id, original_sha256=actual_hash, reason=reason)
        db.add(report)
    else:
        report.original_sha256 = actual_hash
        report.reason = reason
    # An ongoing transfer may finish normally; its completion hook clears the
    # report only if it successfully replaces the file. Never mutate its status
    # or search ID while it is in flight.
    deferred = job.status in ACTIVE
    if not deferred:
        job.status = 'pending'
        job.next_search_at = None
        job.last_error = ''
        job.attempts = 0
    job.updated_at = datetime.utcnow()
    db.add(QualityUpgradeEvent(job_id=job_id, event='bad_file_reported',
                               message=reason, data_json=json.dumps({'deferred': deferred})))
    db.commit()
    schedule_quality_upgrades_changed()
    return {'ok': True, 'job_id': job_id, 'reason': reason, 'deferred': deferred}

@router.post('/{job_id}/clear-bad')
def clear_bad(job_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    allowed(db,user)
    job = require_job(db,job_id)
    if job.status in ACTIVE:
        raise HTTPException(409,'Cannot clear flag during an active replacement')
    report = db.get(QualityBadFile,job_id)
    if report:
        db.delete(report)
        db.add(QualityUpgradeEvent(job_id=job_id,event='bad_file_cleared',message='Bad-file report removed by user'))
        db.commit()
        schedule_quality_upgrades_changed()
    return {'ok': True}

@router.get('/profile-targets')
def profile_targets(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    allowed(db,user)
    jobs = db.execute(select(QualityUpgradeJob.id)).scalars().all()
    profiles = {p.id: p for p in db.execute(select(QualityProfile)).scalars()}
    assignments = dict(db.execute(select(QualityProfileAssignment.job_id, QualityProfileAssignment.profile_id)).all())
    default = next((p for p in profiles.values() if p.is_default), None)
    parsed = {}
    for pid, profile in profiles.items():
        try:
            policy = normalize_profile(json.loads(profile.policy_json))
            tier = policy['tiers'][policy['target_tier']]
        except (ValueError, TypeError, KeyError, IndexError):
            continue
        parts = [tier['label']]
        if tier['min_sample_rate']:
            parts.append(f"{tier['min_sample_rate']/1000:g} kHz+")
        if tier['min_bit_depth']:
            parts.append(f"{tier['min_bit_depth']}-bit+")
        parsed[pid] = {'label':' · '.join(parts), 'profile_name':profile.name}
    targets = {}
    for job_id in jobs:
        profile = profiles.get(assignments.get(job_id)) or default
        if profile and profile.id in parsed:
            targets[job_id] = parsed[profile.id]
    return {'targets':targets}
