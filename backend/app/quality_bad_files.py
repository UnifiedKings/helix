"""Manual reports of invalid Helix-owned audio files.

Reports never remove or mutate audio. The existing upgrade pipeline verifies
source ownership, validates the replacement, and performs the file swap.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base
from .quality_models import QualityUpgradeJob, QualityUpgradeMetadata

class QualityBadFile(Base):
    __tablename__ = 'quality_bad_files'
    job_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    original_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default='')
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)


def active_bad_report(db, job: QualityUpgradeJob) -> QualityBadFile | None:
    """Only a still-Helix-owned original path can be replaced via this flag."""
    if not job.original_path or job.status in {'externally_modified', 'reverted', 'reverting'}:
        return None
    row = db.get(QualityBadFile, job.id)
    if row is None:
        return None
    meta = db.get(QualityUpgradeMetadata, job.id)
    if not meta or meta.provenance != 'helix_imported':
        return None
    if not meta.original_sha256 or row.original_sha256 != meta.original_sha256:
        return None
    return row
