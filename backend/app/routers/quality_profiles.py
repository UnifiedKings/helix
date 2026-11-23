"""Administrator-managed profile API; not enabled in the upgrade worker until Phase 4."""
from __future__ import annotations

from typing import Any
from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require_admin
from ..db import get_db
from ..models import User
from ..quality_profile_policy import evaluate
from ..quality_profile_store import (
    QualityProfile, assign, assigned_profile, create, delete_profile, initialize_profiles,
    serialize, set_default, update,
)

router = APIRouter(prefix='/api/quality-profiles', tags=['quality-profiles'])


def _get(db: Session, profile_id: str) -> QualityProfile:
    row = db.get(QualityProfile, profile_id)
    if row is None:
        raise HTTPException(404, 'Quality profile not found')
    return row


def _bad(exc: ValueError) -> HTTPException:
    return HTTPException(422, str(exc))


@router.get('')
def list_profiles(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    initialize_profiles(db)
    return {'items': [serialize(row) for row in db.execute(select(QualityProfile).order_by(QualityProfile.created_at)).scalars()]}


@router.post('', status_code=201)
def create_profile(payload: dict[str, Any] = Body(...), admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    initialize_profiles(db)
    try:
        return serialize(create(db, str(payload.get('name') or ''), str(payload.get('description') or ''), payload['policy']))
    except (ValueError, KeyError, TypeError) as exc:
        raise _bad(ValueError(str(exc))) from exc


@router.get('/{profile_id}')
def get_profile(profile_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    initialize_profiles(db)
    return serialize(_get(db, profile_id))


@router.patch('/{profile_id}')
def edit_profile(profile_id: str, payload: dict[str, Any] = Body(...), admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    initialize_profiles(db)
    try:
        return serialize(update(db, _get(db, profile_id), name=payload.get('name'), description=payload.get('description'), policy=payload.get('policy')))
    except (ValueError, TypeError) as exc:
        raise _bad(ValueError(str(exc))) from exc


@router.post('/{profile_id}/duplicate', status_code=201)
def duplicate_profile(profile_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    initialize_profiles(db)
    original = _get(db, profile_id)
    return serialize(create(db, f'{original.name} (copy)'[:100], original.description, serialize(original)['policy']))


@router.post('/{profile_id}/default')
def choose_default(profile_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    initialize_profiles(db)
    row = _get(db, profile_id)
    set_default(db, row)
    return serialize(row)


@router.delete('/{profile_id}')
def remove_profile(profile_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    initialize_profiles(db)
    try:
        delete_profile(db, _get(db, profile_id))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {'ok': True}


@router.post('/{profile_id}/evaluate')
def preview(profile_id: str, payload: dict[str, Any] = Body(...), admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    initialize_profiles(db)
    try:
        return evaluate(payload.get('current'), payload['candidate'], serialize(_get(db, profile_id))['policy'], invalid_current=bool(payload.get('invalid_current', False))).as_dict()
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise _bad(ValueError(str(exc))) from exc


@router.get('/jobs/{job_id}/assignment')
def get_assignment(job_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    initialize_profiles(db)
    row = assigned_profile(db, job_id)
    return {'profile_id': row.id if row else None, 'profile': serialize(row) if row else None}


@router.put('/jobs/{job_id}/assignment')
def set_assignment(job_id: str, payload: dict[str, Any] = Body(...), admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    initialize_profiles(db)
    try:
        assign(db, job_id, payload.get('profile_id'))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {'ok': True}
