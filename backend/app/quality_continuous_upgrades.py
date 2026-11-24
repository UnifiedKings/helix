"""Conservative, recurring reconciliation of *verified* Helix-managed quality jobs.

A completed upgrade is a version, not the end of a track's quality lifecycle.
This module never searches Soulseek or modifies audio. The existing worker
and search-budget gate remain authoritative for all network activity.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select

from .db import SessionLocal
from .quality_models import QualityUpgradeEvent, QualityUpgradeJob, QualityUpgradeMetadata
from .quality_profile_policy import normalize_profile, tier_for
from .quality_profile_store import assigned_profile
from .quality_bad_files import active_bad_report
from . import quality_upgrade_service as service

LOG = logging.getLogger(__name__)
# A successful intermediate replacement is kept for at least a week before
# another discretionary search. Misses grow to a 90-day steady-state retry.
SUCCESS_COOLDOWN = timedelta(days=7)
DORMANT_COOLDOWN = timedelta(days=90)
ACTIVE = frozenset({'searching', 'waiting_search', 'waiting_peer', 'downloading',
                    'validating', 'tagging', 'replacing', 'reverting'})


def current_quality(job: QualityUpgradeJob) -> dict[str, Any]:
    return {key: getattr(job, 'current_' + key) or getattr(job, 'original_' + key)
            for key in ('codec', 'bitrate', 'sample_rate', 'bit_depth')}


def profile_policy(db: Any, job: QualityUpgradeJob) -> dict[str, Any] | None:
    profile = assigned_profile(db, job.id)
    if not profile:
        return None
    return normalize_profile(json.loads(profile.policy_json))


def managed_file(db: Any, job: QualityUpgradeJob) -> tuple[Path | None, str]:
    """Return only a cryptographically identified Helix-owned current version.

    Never infer ownership from a directory name, title, codec, or file size.
    This rejects older upgraded records lacking a SHA-256 rather than
    accidentally adopting somebody's manual replacement.
    """
    meta = db.get(QualityUpgradeMetadata, job.id)
    if not meta or meta.provenance != 'helix_imported':
        return None, 'ownership_unverified'
    raw = job.library_path if job.status == 'upgraded' else (job.original_path or job.library_path)
    if not raw:
        return None, 'path_missing'
    root = Path(os.getenv('HELIX_MUSIC_LIBRARY_ROOT', '/data/music')).resolve()
    try:
        raw_path = Path(raw)
        if raw_path.is_symlink():
            return None, 'path_unavailable'
        path = raw_path.resolve(strict=True)
        path.relative_to(root)
        if not path.is_file():
            return None, 'path_unavailable'
    except (OSError, ValueError):
        return None, 'path_unavailable'
    # For completed upgrades, current_sha256 is the expected fingerprint. For
    # original copies, use original_sha256. The SHA is REQUIRED for rebasing.
    expected = (meta.current_sha256 if job.status == 'upgraded' else
                (meta.original_sha256 or meta.current_sha256))
    if not expected:
        return None, 'fingerprint_missing'
    try:
        if service._sha256_file(path) != expected:
            return None, 'fingerprint_mismatch'
    except OSError:
        return None, 'fingerprint_unreadable'
    return path, 'verified'


def target_reached(job: QualityUpgradeJob, policy: dict[str, Any]) -> bool:
    tier = tier_for(current_quality(job), policy)
    return bool(policy['stop_at_target'] and tier is not None and tier <= policy['target_tier'])


def _event(db: Any, job: QualityUpgradeJob, kind: str, message: str, details: dict[str, Any] | None = None) -> None:
    db.add(QualityUpgradeEvent(job_id=job.id, event=kind, message=message,
                               data_json=json.dumps(details or {}, ensure_ascii=False)))


def _rebase_completed_version(db: Any, job: QualityUpgradeJob, path: Path, now: datetime) -> None:
    """Make the *current verified file* the next replacement baseline.

    The old service's destructive replacement checks original_path,
    original_size, original_mtime_ns and original_sha256. Rebase all together
    in one transaction, with an immutable event recording previous metadata.
    Prior source/upgrade events are deliberately retained.
    """
    meta = db.get(QualityUpgradeMetadata, job.id)
    assert meta is not None and meta.current_sha256
    previous = {
        'original_path': job.original_path,
        'original': {k: getattr(job, 'original_' + k) for k in
                     ('codec', 'bitrate', 'sample_rate', 'bit_depth')},
        'current_path': str(path),
        'current': current_quality(job),
        'prior_upgraded_at': job.upgraded_at.isoformat() if job.upgraded_at else None,
        'prior_sha256': meta.original_sha256,
        'current_sha256': meta.current_sha256,
        'source_username': job.slskd_username,
        'source_filename': job.slskd_filename,
    }
    stat = path.stat()
    job.original_path = str(path)
    job.library_path = str(path)
    job.original_size = stat.st_size
    job.original_mtime_ns = stat.st_mtime_ns
    for key in ('codec', 'bitrate', 'sample_rate', 'bit_depth'):
        setattr(job, 'original_' + key, getattr(job, 'current_' + key))
    meta.original_sha256 = meta.current_sha256
    meta.updated_at = now
    _event(db, job, 'upgrade_version_rebased',
           'Verified prior upgrade became the baseline for a later upgrade', previous)


def reevaluate_quality_jobs(*, job_ids: set[str] | None = None,
                            now: datetime | None = None) -> dict[str, int]:
    """Reevaluate existing jobs without issuing any Soulseek searches.

    First run after upgrade respects the success cooldown. Dormant jobs
    resume after 90 days and cannot flood the queue on a restart. Finished
    records are never reopened without a verified, unchanged managed file.
    """
    now = now or datetime.utcnow()
    counts = {key: 0 for key in ('examined', 'below_target', 'target_satisfied',
                                  'reactivated', 'deferred', 'unverified')}
    db = SessionLocal()
    try:
        stmt = select(QualityUpgradeJob)
        if job_ids is not None:
            if not job_ids:
                return counts
            stmt = stmt.where(QualityUpgradeJob.id.in_(job_ids))
        jobs = db.execute(stmt).scalars().all()
        for job in jobs:
            if job.status in ACTIVE or job.status in {'externally_modified', 'reverting', 'reverted'}:
                continue
            counts['examined'] += 1
            try:
                policy = profile_policy(db, job)
            except (ValueError, TypeError, KeyError):
                LOG.exception('Invalid quality profile job=%s', job.id)
                continue
            if policy is None:
                continue
            rank = tier_for(current_quality(job), policy)
            if rank is None or rank > policy['target_tier'] or not policy['stop_at_target']:
                counts['below_target'] += 1
            else:
                counts['target_satisfied'] += 1
            # Original pending and no-match jobs already have correct lifecycle.
            # Only terminal upgraded/satisfied/dormant jobs need reevaluation.
            if job.status not in {'upgraded', 'satisfied', 'dormant'}:
                continue
            # A completed version at its target is already terminal for search
            # purposes; don't hash every finished FLAC every two minutes.
            if job.status in {'upgraded', 'satisfied'} and target_reached(job, policy):
                continue
            # Don't read/hash large lossless files before a search is due.
            if job.status == 'upgraded':
                due = (job.upgraded_at or job.updated_at or now) + SUCCESS_COOLDOWN
                if due > now:
                    if job.next_search_at != due:
                        job.next_search_at = due
                        job.updated_at = now
                    counts['deferred'] += 1
                    continue
            if job.status == 'dormant':
                due = job.next_search_at or ((job.last_search_at or job.updated_at or now) + DORMANT_COOLDOWN)
                if due > now:
                    if job.next_search_at != due:
                        job.next_search_at = due
                        job.updated_at = now
                    counts['deferred'] += 1
                    continue
            path, reason = managed_file(db, job)
            if path is None:
                counts['unverified'] += 1
                continue
            if target_reached(job, policy) and active_bad_report(db, job) is None:
                if job.status not in {'satisfied', 'upgraded'}:
                    job.status = 'satisfied'
                    job.completion_source = 'helix_library'
                    job.next_search_at = None
                    job.updated_at = now
                    _event(db, job, 'quality_target_reached', 'Verified file meets assigned quality target')
                continue
            if job.status == 'upgraded':
                _rebase_completed_version(db, job, path, now)
            job.status = 'pending'
            job.attempts = 0
            job.next_search_at = None
            job.last_error = ''
            job.updated_at = now
            _event(db, job, 'continuous_upgrade_reactivated',
                   'Verified managed file remains below its quality target; scheduled for another search')
            counts['reactivated'] += 1
        db.commit()
        if counts['reactivated']:
            service.schedule_quality_upgrades_changed()
        return counts
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

async def safe_continuous_revert(job_id: str, user_id: str) -> None:
    """Protect the original Revert workflow after a multi-step rebase.

    The legacy revert writer writes *Opus bytes* to job.original_path. A
    multi-step job's latest baseline can instead be .flac/.m4a, so reusing
    that basename would leave an incorrectly named file. Revert to a distinct
    .opus sibling, never overwrite an existing, unrelated file, and let the
    original service perform the verified download and atomic replacement.
    """
    db = SessionLocal()
    old_path: str | None = None
    old_next_search: datetime | None = None
    changed = False
    try:
        job = db.get(QualityUpgradeJob, job_id)
        if not job:
            raise LookupError('Upgrade job not found')
        if job.status != 'upgraded':
            raise RuntimeError('Only completed upgrades can be reverted')
        path = Path(job.original_path or '')
        if not job.original_path or not path.name:
            raise RuntimeError('Missing original Helix path')
        if path.suffix.lower() != '.opus':
            target = path.with_suffix('.opus')
            root = Path(os.getenv('HELIX_MUSIC_LIBRARY_ROOT', '/data/music')).resolve()
            try:
                path.resolve().relative_to(root)
                target.resolve().relative_to(root)
            except (ValueError, OSError) as exc:
                raise RuntimeError('Revert target lies outside the managed library') from exc
            # The original service uses os.replace. Do not overwrite an
            # independent .opus file that might have appeared at this path.
            if target.exists() or target.is_symlink():
                raise RuntimeError(f'Revert target already exists; refusing to overwrite: {target.name}')
            old_path = job.original_path
            old_next_search = job.next_search_at
            job.original_path = str(target)
            # Prevent background re-evaluation from rebasing the job in the
            # short interval before the original revert sets 'reverting'.
            job.next_search_at = datetime.utcnow() + timedelta(hours=1)
            job.updated_at = datetime.utcnow()
            _event(db, job, 'continuous_revert_prepared',
                   'Prepared Opus revert destination without overwriting the existing file',
                   {'previous_baseline': old_path, 'opus_destination': str(target)})
            db.commit()
            changed = True
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    try:
        await service.revert_quality_upgrade_job(job_id, user_id)
    except BaseException:
        # If original revert did not complete, restore its previous metadata
        # so an ordinary future upgrade is not tied to an imaginary path.
        if changed:
            db = SessionLocal()
            try:
                job = db.get(QualityUpgradeJob, job_id)
                if job and job.status == 'upgraded' and old_path:
                    job.original_path = old_path
                    job.next_search_at = old_next_search
                    job.updated_at = datetime.utcnow()
                    db.commit()
            finally:
                db.close()
        raise


def install_safe_revert() -> None:
    """Bind the existing Quality Upgrades endpoint to the safe revert wrapper."""
    from .routers import quality_upgrades
    if quality_upgrades.revert_quality_upgrade_job is not safe_continuous_revert:
        quality_upgrades.revert_quality_upgrade_job = safe_continuous_revert
