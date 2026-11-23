"""Bridge Phase 1 profiles into the existing, Phase 3 budgeted quality worker.

Only Helix-owned jobs already admitted to the quality-upgrade queue are handled.
No changes are made to initial music fulfillment or global slskd calls.
"""
from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from .db import SessionLocal
from .quality_models import QualityUpgradeJob, QualityUpgradeMetadata
from .quality_bad_files import QualityBadFile, active_bad_report
from .quality_source_registry import rejected_candidate, rejected_content, record_rejection, rejection_keys
from .quality_models import QualityUpgradeEvent
from .quality_profile_policy import LOSSLESS, evaluate, normalize_profile, tier_for
from .quality_profile_store import assigned_profile
from . import quality_upgrade_service as service

LOG = logging.getLogger(__name__)
_CONTEXT: ContextVar[tuple[str, dict[str, Any]] | None] = ContextVar('quality_profile_job_context', default=None)
_REJECTED_KEYS: ContextVar[set[tuple[str, str, int]] | None] = ContextVar('quality_rejected_keys', default=None)

_ORIGINAL_COMMIT = service._commit_quality_change
_ORIGINAL_CANDIDATE = service._candidate_meets_policy
_ORIGINAL_FINAL = service._quality_info_meets_policy
_ORIGINAL_RANK = service._candidate_rank
_ORIGINAL_IS_LOSSLESS = service._is_lossless
_ORIGINAL_FINISH = service._finish_downloaded_upgrade


def _quality(job: QualityUpgradeJob) -> dict[str, Any]:
    return {
        'codec': job.current_codec or job.original_codec,
        'bitrate': job.current_bitrate or job.original_bitrate,
        'sample_rate': job.current_sample_rate or job.original_sample_rate,
        'bit_depth': job.current_bit_depth or job.original_bit_depth,
    }


def _candidate_quality(candidate: Any, *, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    codec = Path(str(candidate.filename or '')).suffix.lower().lstrip('.')
    if codec == 'aif': codec = 'aiff'
    # Search responses often omit audio attributes; a missing field is not a
    # confirmed disqualifier. Fill it only for *search prefiltering*, not after
    # the file is downloaded. The downloaded file is inspected with Mutagen.
    quality = {
        'codec': codec,
        'bitrate': int(candidate.bitrate or 0),
        'sample_rate': int(candidate.sample_rate or 0),
        'bit_depth': int(candidate.bit_depth or 0),
    }
    if policy:
        for tier in policy['tiers']:
            if codec not in tier['codecs']:
                continue
            for field in ('bitrate', 'sample_rate', 'bit_depth'):
                if not quality[field]:
                    quality[field] = int(tier.get('min_' + field) or 0)
            break
    return quality


def _invalid_existing(job: QualityUpgradeJob | None) -> bool:
    if job is None:
        return False
    db = SessionLocal()
    try:
        record = db.get(QualityUpgradeJob, job.id)
        return record is not None and active_bad_report(db, record) is not None
    finally:
        db.close()


def _current_policy() -> dict[str, Any] | None:
    context = _CONTEXT.get()
    return context[1] if context else None


def _current_job() -> QualityUpgradeJob | None:
    context = _CONTEXT.get()
    if not context: return None
    db = SessionLocal()
    try:
        job = db.get(QualityUpgradeJob, context[0])
        if job is not None:
            db.expunge(job)
        return job
    finally:
        db.close()


def profile_candidate_meets_policy(candidate: Any, settings: dict[str, Any], job: Any | None = None) -> bool:
    blocked = _REJECTED_KEYS.get()
    from .quality_source_registry import normalized_key
    source_key = normalized_key(candidate.username, candidate.filename, candidate.size)
    if ((source_key in blocked or source_key[:2] + (0,) in blocked) if blocked is not None
        else rejected_candidate(candidate.username, candidate.filename, candidate.size)):
        return False
    policy = _current_policy()
    if policy is None:
        return _ORIGINAL_CANDIDATE(candidate, settings, job)
    current = _quality(job) if job is not None else None
    result = evaluate(current, _candidate_quality(candidate, policy=policy), policy, invalid_current=_invalid_existing(job))
    return result.kind == 'upgrade'


def profile_quality_info_meets_policy(info: dict[str, Any], settings: dict[str, Any]) -> bool:
    policy = _current_policy()
    if policy is None:
        return _ORIGINAL_FINAL(info, settings)
    job = _current_job()
    if job is None: return False
    return evaluate(_quality(job), info, policy, invalid_current=_invalid_existing(job)).kind == 'upgrade'


def profile_candidate_rank(candidate: Any) -> tuple[int, int, int, int]:
    policy = _current_policy()
    if policy is None: return _ORIGINAL_RANK(candidate)
    tier = tier_for(_candidate_quality(candidate, policy=policy), policy)
    # Existing score/peer availability stay primary; profile precedence is
    # used for the current candidate-quality tie breaker.
    return (len(policy['tiers']) - tier if tier is not None else 0,
            int(candidate.bit_depth or 0), int(candidate.sample_rate or 0),
            int(candidate.bitrate or 0))


def profile_eligible_lossless(candidate: Any) -> bool:
    """Do not end search early on three lossless files rejected by profile."""
    if not _ORIGINAL_IS_LOSSLESS(candidate):
        return False
    policy = _current_policy()
    if policy is None:
        return True
    job = _current_job()
    if job is None:
        return False
    return evaluate(_quality(job), _candidate_quality(candidate, policy=policy), policy, invalid_current=_invalid_existing(job)).kind == 'upgrade'


async def _finish_with_rejected_source(*, db: Any, job: QualityUpgradeJob, sub: Any,
                                       downloaded: Path, download_root: Path) -> None:
    # Validate the raw download BEFORE copying tags or replacing the library.
    # A known bad blob may be shared by many different Soulseek peers.
    if rejected_content(downloaded):
        record_rejection(db, username=job.slskd_username,
                         filename=job.slskd_filename, size=job.slskd_size,
                         reason='Downloaded content matches a previously reported bad file',
                         job_id=job.id)
        db.commit()
        raise RuntimeError('Downloaded content matches a known bad file; source excluded from future searches')
    await _ORIGINAL_FINISH(db=db, job=job, sub=sub, downloaded=downloaded,
                           download_root=download_root)


def _commit_with_profile(db: Any) -> None:
    context = _CONTEXT.get()
    if context:
        job = db.get(QualityUpgradeJob, context[0])
        if job and job.original_path and job.status == 'satisfied' and job.completion_source == 'helix_library':
            # Old worker marks existing lossless copies satisfied before search.
            # A reported invalid copy must never be considered satisfied.
            invalid = active_bad_report(db, job) is not None
            rank = tier_for(_quality(job), context[1])
            if invalid or rank is None or not (context[1]['stop_at_target'] and rank <= context[1]['target_tier']):
                job.status = 'pending'
                job.completion_source = ''
                job.next_search_at = None
        if job and job.status == 'upgraded':
            # Immutable snapshot is recorded at the moment of completion; old
            # upgrades with no such event remain explicitly labeled Legacy.
            existing = db.execute(select(QualityUpgradeEvent.id).where(
                QualityUpgradeEvent.job_id == job.id,
                QualityUpgradeEvent.event == 'quality_policy_snapshot').limit(1)).first()
            if not existing:
                profile_name = ''
                try:
                    profile = assigned_profile(db, job.id)
                    profile_name = profile.name if profile else ''
                except Exception:
                    LOG.exception('Could not resolve profile name for job=%s', job.id)
                before = {k: getattr(job, 'original_' + k) for k in ('codec','bitrate','sample_rate','bit_depth')}
                after = {k: getattr(job, 'current_' + k) for k in ('codec','bitrate','sample_rate','bit_depth')}
                service._record_event(db, job.id, 'quality_policy_snapshot',
                                      'Quality profile at completion', {
                    'policy_name':profile_name or 'Unnamed profile',
                    'policy': context[1], 'before':before, 'after':after,
                    'completion_source':job.completion_source,
                })
            report = db.get(QualityBadFile, job.id)
            if report is not None:
                db.delete(report)
                service._record_event(db, job.id, 'bad_file_replaced', 'Reported bad file replaced with validated audio')
    _ORIGINAL_COMMIT(db)


def _owned_file_unchanged(db: Any, job: QualityUpgradeJob) -> bool:
    """Do not bypass existing ownership protection when marking as satisfied."""
    if not job.original_path or job.status in {'upgraded','reverted','externally_modified'}:
        return False
    path = Path(job.original_path)
    if not path.is_file(): return False
    metadata = db.get(QualityUpgradeMetadata, job.id)
    if metadata and metadata.provenance not in ('helix_imported', ''):
        return False
    try:
        if metadata and metadata.original_sha256:
            return service._sha256_file(path) == metadata.original_sha256
        size = path.stat().st_size
        return bool(job.original_size and size == job.original_size)
    except OSError:
        return False


def _preflight_target(job_id: str, policy: dict[str, Any]) -> bool:
    """Skip *all* Soulseek queries when a verified owned copy met its target."""
    if not policy['stop_at_target']: return False
    db = SessionLocal()
    try:
        job = db.get(QualityUpgradeJob, job_id)
        if not job or job.status not in {'pending', 'no_match', 'dormant'}:
            return False
        if not _owned_file_unchanged(db, job): return False
        if active_bad_report(db, job) is not None: return False
        rank = tier_for(_quality(job), policy)
        if rank is None or rank > policy['target_tier']:
            return False
        job.status = 'satisfied'
        job.completion_source = 'helix_library'
        job.next_search_at = None
        job.last_error = ''
        job.updated_at = datetime.utcnow()
        service._record_event(db, job_id, 'profile_satisfied',
                              'Existing verified Helix copy meets quality profile target')
        db.commit()
        service.schedule_quality_upgrades_changed()
        return True
    finally:
        db.close()


def _load_policy(job_id: str) -> dict[str, Any]:
    db = SessionLocal()
    try:
        profile = assigned_profile(db, job_id)
        if profile is None:
            raise RuntimeError('No default quality profile configured')
        return normalize_profile(json.loads(profile.policy_json))
    finally:
        db.close()


async def process_profile_job(job_id: str) -> None:
    """Run normal worker under a job-specific profile; no global setting edits."""
    policy = _load_policy(job_id)
    if _preflight_target(job_id, policy): return
    token = _CONTEXT.set((job_id, policy))
    rejected_token = _REJECTED_KEYS.set(rejection_keys())
    try:
        await service._process_job(job_id)
    finally:
        _REJECTED_KEYS.reset(rejected_token)
        _CONTEXT.reset(token)


def install_profile_hooks() -> None:
    """Called once by the Phase 3 worker, restoring hooks at worker shutdown."""
    service._candidate_meets_policy = profile_candidate_meets_policy
    service._quality_info_meets_policy = profile_quality_info_meets_policy
    service._candidate_rank = profile_candidate_rank
    service._is_lossless = profile_eligible_lossless
    service._commit_quality_change = _commit_with_profile
    service._finish_downloaded_upgrade = _finish_with_rejected_source


def uninstall_profile_hooks() -> None:
    service._candidate_meets_policy = _ORIGINAL_CANDIDATE
    service._quality_info_meets_policy = _ORIGINAL_FINAL
    service._candidate_rank = _ORIGINAL_RANK
    service._is_lossless = _ORIGINAL_IS_LOSSLESS
    service._commit_quality_change = _ORIGINAL_COMMIT
    service._finish_downloaded_upgrade = _ORIGINAL_FINISH


def reactivate_newly_eligible_jobs() -> int:
    """Re-evaluate legacy satisfied jobs after a profile edit/change.

    Only formerly satisfied, still-owned *original* copies are reopened. An
    upgraded or externally modified file is never implicitly re-adopted.
    No searches are issued here; the Phase 3 worker still owns all budgets.
    """
    reopened = 0
    db = SessionLocal()
    try:
        jobs = db.execute(select(QualityUpgradeJob).where(
            QualityUpgradeJob.status == 'satisfied',
            QualityUpgradeJob.completion_source == 'helix_library',
        )).scalars().all()
        for job in jobs:
            profile = assigned_profile(db, job.id)
            if not profile:
                continue
            try:
                policy = normalize_profile(json.loads(profile.policy_json))
            except (ValueError, TypeError, KeyError):
                LOG.exception('Invalid profile for satisfied job=%s', job.id)
                continue
            rank = tier_for(_quality(job), policy)
            is_done = policy['stop_at_target'] and rank is not None and rank <= policy['target_tier']
            if is_done or not _owned_file_unchanged(db, job):
                continue
            job.status = 'pending'
            job.completion_source = ''
            job.attempts = 0
            job.next_search_at = None
            job.updated_at = datetime.utcnow()
            service._record_event(db, job.id, 'profile_reactivated',
                                  'Quality profile target changed; Helix-owned copy is eligible again')
            reopened += 1
        if reopened:
            db.commit()
            service.schedule_quality_upgrades_changed()
        return reopened
    finally:
        db.close()
