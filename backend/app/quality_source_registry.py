"""Durable, file-specific Soulseek rejection registry.

A bad recording is identified by (peer, remote name, size); a downloaded
identical blob can additionally be rejected by SHA-256, regardless of peer.
Temporary transfer failures are intentionally NOT recorded here.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path

from sqlalchemy import DateTime, String, Integer, Text, select
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base, SessionLocal, engine


class RejectedQualitySource(Base):
    __tablename__ = 'quality_rejected_sources'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(255), nullable=False, default='')
    filename: Mapped[str] = mapped_column(Text, nullable=False, default='')
    size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, default='')
    reason: Mapped[str] = mapped_column(Text, nullable=False, default='')
    job_id: Mapped[str] = mapped_column(String(36), nullable=False, default='')
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)


def initialize_rejections() -> None:
    Base.metadata.create_all(bind=engine, tables=[RejectedQualitySource.__table__])


def normalized_key(username: str, filename: str, size: int) -> tuple[str, str, int]:
    return username.strip().casefold(), re.sub(r'/+', '/', filename.strip().replace('\\', '/')).casefold(), max(0, int(size or 0))


def record_rejection(db, *, username: str = '', filename: str = '', size: int = 0,
                     sha256: str = '', reason: str, job_id: str = '') -> bool:
    """Record only a known identity/hash; don't blacklist all files from a peer."""
    username, filename, size = normalized_key(username, filename, size)
    sha256 = sha256.strip().lower()
    if len(sha256) != 64 or any(c not in '0123456789abcdef' for c in sha256):
        sha256 = ''
    if not sha256 and not (username and filename):
        return False
    # Multiple peers may share identical bad bytes. Keep a separate candidate
    # identity for each peer so future searches can exclude it *before* download.
    if sha256 and not (username and filename):
        existing = db.execute(select(RejectedQualitySource.id).where(
            RejectedQualitySource.sha256 == sha256).limit(1)).first()
        if existing:
            return False
    if username and filename:
        existing = db.execute(select(RejectedQualitySource).where(
            RejectedQualitySource.username == username,
            RejectedQualitySource.filename == filename,
            RejectedQualitySource.size == size,
        )).scalars().first()
        if existing:
            if sha256 and not existing.sha256:
                existing.sha256 = sha256
            return False
    db.add(RejectedQualitySource(username=username, filename=filename, size=size,
                                 sha256=sha256, reason=reason[:400], job_id=job_id))
    return True


def rejection_keys() -> set[tuple[str, str, int]]:
    """Fetch candidate identity snapshot once per job, not once per result."""
    db = SessionLocal()
    try:
        rows = db.execute(select(RejectedQualitySource.username,
                                 RejectedQualitySource.filename,
                                 RejectedQualitySource.size).where(
            RejectedQualitySource.username != '',
            RejectedQualitySource.filename != '',
        )).all()
        return {normalized_key(*row) for row in rows}
    finally:
        db.close()


def rejected_candidate(username: str, filename: str, size: int) -> bool:
    username, filename, size = normalized_key(username, filename, size)
    if not username or not filename:
        return False
    db = SessionLocal()
    try:
        return db.execute(select(RejectedQualitySource.id).where(
            RejectedQualitySource.username == username,
            RejectedQualitySource.filename == filename,
            RejectedQualitySource.size.in_([size, 0]),
        ).limit(1)).first() is not None
    finally:
        db.close()


def rejected_content(path: Path) -> bool:
    """Hash after a download, before tag copying/replacing the library file."""
    digest = hashlib.sha256()
    with path.open('rb') as reader:
        for data in iter(lambda: reader.read(1024 * 1024), b''):
            digest.update(data)
    db = SessionLocal()
    try:
        return db.execute(select(RejectedQualitySource.id).where(
            RejectedQualitySource.sha256 == digest.hexdigest()).limit(1)).first() is not None
    finally:
        db.close()
