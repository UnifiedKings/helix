"""Persistent quality profiles. Phase 1 is deliberately not connected to the worker.

No modifications to quality_upgrade_jobs or their existing retry/search state.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from .db import Base, engine
from .quality_profile_policy import legacy_profile, normalize_profile
from .settings_store import get_settings


class QualityProfile(Base):
    __tablename__ = 'quality_profiles'

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default='')
    policy_json: Mapped[str] = mapped_column(Text, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)


class QualityProfileAssignment(Base):
    """Explicit assignment, keyed by stable quality job identifier.

    Missing assignment resolves to the default profile, so existing jobs
    need no migration or foreign-key changes.
    """
    __tablename__ = 'quality_profile_assignments'

    job_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    profile_id: Mapped[str] = mapped_column(String(36), ForeignKey('quality_profiles.id'), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)


def initialize_profiles(db: Session) -> None:
    """Idempotent bootstrap after ordinary Helix init_db().

    The first default snapshots legacy settings for the future cutover.
    It does NOT change settings or cause the running upgrader to use profiles.
    """
    Base.metadata.create_all(bind=engine, tables=[QualityProfile.__table__, QualityProfileAssignment.__table__])
    if db.execute(select(QualityProfile.id).limit(1)).first():
        return
    defaults = legacy_profile(get_settings(db))
    db.add(QualityProfile(name='Main Library', description='Migrated from existing quality upgrade settings',
                          policy_json=json.dumps(defaults), is_default=True))
    db.commit()


def serialize(row: QualityProfile) -> dict[str, Any]:
    return {
        'id': row.id, 'name': row.name, 'description': row.description,
        'is_default': row.is_default, 'policy': json.loads(row.policy_json),
        'created_at': row.created_at, 'updated_at': row.updated_at,
    }


def create(db: Session, name: str, description: str, policy: dict[str, Any]) -> QualityProfile:
    name = name.strip()
    if not 1 <= len(name) <= 100:
        raise ValueError('Name must have 1–100 characters')
    if len(description) > 1000:
        raise ValueError('Description is too long')
    row = QualityProfile(name=name, description=description, policy_json=json.dumps(normalize_profile(policy)))
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update(db: Session, row: QualityProfile, *, name: str | None = None,
           description: str | None = None, policy: dict[str, Any] | None = None) -> QualityProfile:
    if name is not None:
        if not 1 <= len(name.strip()) <= 100:
            raise ValueError('Name must have 1–100 characters')
        row.name = name.strip()
    if description is not None:
        if len(description) > 1000:
            raise ValueError('Description is too long')
        row.description = description
    if policy is not None:
        row.policy_json = json.dumps(normalize_profile(policy))
    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return row


def set_default(db: Session, row: QualityProfile) -> None:
    for item in db.execute(select(QualityProfile)).scalars():
        item.is_default = item.id == row.id
    db.commit()


def assigned_profile(db: Session, job_id: str) -> QualityProfile | None:
    assignment = db.get(QualityProfileAssignment, job_id)
    if assignment:
        row = db.get(QualityProfile, assignment.profile_id)
        if row:
            return row
    return db.execute(select(QualityProfile).where(QualityProfile.is_default.is_(True))).scalar_one_or_none()


def assign(db: Session, job_id: str, profile_id: str | None) -> None:
    from .quality_models import QualityUpgradeJob
    if db.get(QualityUpgradeJob, job_id) is None:
        raise LookupError('Upgrade job not found')
    existing = db.get(QualityProfileAssignment, job_id)
    if profile_id is None:
        if existing:
            db.delete(existing)
    else:
        if db.get(QualityProfile, profile_id) is None:
            raise LookupError('Quality profile not found')
        if existing:
            existing.profile_id = profile_id
        else:
            db.add(QualityProfileAssignment(job_id=job_id, profile_id=profile_id))
    db.commit()


def delete_profile(db: Session, row: QualityProfile) -> None:
    if row.is_default:
        raise ValueError('Cannot delete the default profile')
    from sqlalchemy import delete
    db.execute(delete(QualityProfileAssignment).where(QualityProfileAssignment.profile_id == row.id))
    db.delete(row)
    db.commit()
