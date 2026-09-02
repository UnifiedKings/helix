from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class QualityUpgradeJob(Base):
    __tablename__ = "quality_upgrade_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    requested_by_user_id: Mapped[str] = mapped_column(String(36), nullable=False, default="", index=True)

    yt_video_id: Mapped[str] = mapped_column(String(128), nullable=False, default="", index=True)
    yt_browse_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    artist: Mapped[str] = mapped_column(Text, nullable=False, default="")
    album: Mapped[str] = mapped_column(Text, nullable=False, default="")
    album_artist: Mapped[str] = mapped_column(Text, nullable=False, default="")
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    track_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    art_url: Mapped[str] = mapped_column(Text, nullable=False, default="")

    subsonic_song_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    library_path: Mapped[str] = mapped_column(Text, nullable=False, default="")
    original_path: Mapped[str] = mapped_column(Text, nullable=False, default="")
    original_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    original_mtime_ns: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    original_codec: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    original_bitrate: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    original_sample_rate: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    original_bit_depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    current_codec: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    current_bitrate: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_sample_rate: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_bit_depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    best_match_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    last_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    last_search_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    next_search_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)

    slskd_username: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    slskd_filename: Mapped[str] = mapped_column(Text, nullable=False, default="")
    slskd_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    completion_source: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    upgraded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    reverted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)


class AdminNotification(Base):
    __tablename__ = "admin_notifications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    kind: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    data_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, index=True)


class QualityUpgradeMetadata(Base):
    """Extensible per-job metadata kept separate from the original job table.

    Keeping this in a sidecar table lets existing installations gain stronger
    provenance/fingerprint/resume data without rebuilding quality_upgrade_jobs.
    It also leaves a clean path for a future opt-in "adopt existing library"
    workflow while today's automatic upgrades remain Helix-owned only.
    """
    __tablename__ = "quality_upgrade_metadata"

    job_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    provenance: Mapped[str] = mapped_column(String(32), nullable=False, default="helix_imported", index=True)
    original_sha256: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    current_sha256: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    slskd_search_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    slskd_search_query: Mapped[str] = mapped_column(Text, nullable=False, default="")
    candidate_pool_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    candidate_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    operation_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)


class QualityUpgradeEvent(Base):
    __tablename__ = "quality_upgrade_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    job_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    event: Mapped[str] = mapped_column(String(64), nullable=False, default="", index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    data_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, index=True)
