"""Budgeted quality upgrade worker, without altering the existing fulfillment engine.

Uses the current implementation's _process_job and wraps only the Soulseek
client referenced by quality_upgrade_service. Each new search (not a resume)
requires a persisted budget reservation. No separate installation step.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import case, select, and_

from .db import SessionLocal
from .quality_models import QualityUpgradeJob
from .quality_bad_files import QualityBadFile
from .quality_search_budget import wait_for_search_budget
from .settings_store import get_settings
from . import quality_upgrade_service as service
from .quality_upgrade_profile_runtime import process_profile_job, install_profile_hooks, uninstall_profile_hooks
from .quality_continuous_upgrades import reevaluate_quality_jobs

LOG = logging.getLogger(__name__)

# Override only the class reference used by quality upgrade operations, not
# the slskd integration used elsewhere in Helix.
OriginalSlskdClient = service.SlskdClient


class BudgetedQualitySlskdClient(OriginalSlskdClient):
    async def search(self, query: str, *, timeout_s: float = 35.0,
                     max_results: int = 200, search_id: str = "",
                     on_search_started=None):
        if not (search_id or "").strip():
            # The reservation is committed before slskd receives a POST.
            # A resumed search uses the existing ID and is not a new POST.
            await wait_for_search_budget(self._quality_job_id, query)
        return await super().search(query, timeout_s=timeout_s,
                                    max_results=max_results, search_id=search_id,
                                    on_search_started=on_search_started)


# The service creates clients with no job_id argument. Attach each job's ID
# with a ContextVar so concurrent async jobs cannot charge one another's IDs.
from contextvars import ContextVar
_CURRENT_JOB = ContextVar('quality_upgrade_budget_job_id', default='')


class JobScopedSlskdClient(BudgetedQualitySlskdClient):
    @property
    def _quality_job_id(self) -> str:
        return _CURRENT_JOB.get()


async def _process_budgeted_job(job_id: str) -> None:
    token = _CURRENT_JOB.set(job_id)
    try:
        await process_profile_job(job_id)
    finally:
        _CURRENT_JOB.reset(token)


async def quality_upgrade_worker_loop() -> None:
    """Preserve worker execution and retry paths, with priority ordering."""
    # The service's own SlskdClient symbol is used only in upgrade functions;
    # do not change the shared integration client for the rest of Helix.
    previous = service.SlskdClient
    service.SlskdClient = JobScopedSlskdClient
    install_profile_hooks()
    next_reconcile = datetime.min
    try:
        while True:
            try:
                if datetime.utcnow() >= next_reconcile:
                    await asyncio.to_thread(reevaluate_quality_jobs)
                    next_reconcile = datetime.utcnow() + timedelta(minutes=2)
                db = SessionLocal()
                try:
                    settings = get_settings(db)
                    enabled = bool(settings.get('slskd_enabled'))
                    now = datetime.utcnow()
                    active = ['searching','waiting_search','waiting_peer','downloading',
                              'validating','tagging','replacing','reverting']
                    jobs = [] if not enabled else db.execute(
                        select(QualityUpgradeJob)
                        .outerjoin(QualityBadFile, QualityBadFile.job_id == QualityUpgradeJob.id)
                        .where(QualityUpgradeJob.status.in_(['pending','no_match',*active]))
                        .where((QualityUpgradeJob.next_search_at.is_(None)) |
                               (QualityUpgradeJob.next_search_at <= now))
                        .order_by(
                            case((QualityUpgradeJob.status.in_(active), 0),
                                 (QualityBadFile.job_id.isnot(None), 1),
                                 (QualityUpgradeJob.status == 'pending', 2), else_=3),
                            case((QualityUpgradeJob.current_codec.in_(
                                ['mp3','aac','opus','vorbis','ogg','m4a']), 0), else_=1),
                            QualityUpgradeJob.created_at.asc(),
                        )
                        .limit(max(1, min(3, int(settings.get('slskd_concurrent_searches') or 1))))
                    ).scalars().all()
                    ids = [job.id for job in jobs]
                finally:
                    db.close()
                if not enabled:
                    await asyncio.sleep(10)
                elif ids:
                    await asyncio.gather(*(_process_budgeted_job(job_id) for job_id in ids))
                    await asyncio.sleep(1)
                else:
                    await asyncio.sleep(5)
            except asyncio.CancelledError:
                raise
            except Exception:
                LOG.exception('Quality upgrade worker loop failed')
                await asyncio.sleep(10)
    finally:
        uninstall_profile_hooks()
        service.SlskdClient = previous
