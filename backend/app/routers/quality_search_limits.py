"""Administrator endpoints for the persistent quality search scheduler."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from ..auth import require_admin
from ..db import get_db
from ..models import User
from ..quality_search_budget import effective_config, snapshot
from ..settings_store import get_settings, set_setting

router = APIRouter(prefix='/api/quality-upgrades/admin/search-limits', tags=['quality-upgrades'])


@router.get('')
def get_search_limits(admin: User = Depends(require_admin)) -> dict[str, Any]:
    return snapshot()


@router.patch('')
def update_search_limits(payload: dict[str, Any] = Body(...), admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    allowed = {'quality_search_max_hourly': (0, 240), 'quality_search_max_daily': (0, 2000), 'slskd_concurrent_searches': (1, 3)}
    if not payload or set(payload).difference(allowed):
        raise HTTPException(status_code=422, detail='Unsupported search limit field')
    normalized = {}
    for key, value in payload.items():
        if isinstance(value, bool) or not isinstance(value, int):
            raise HTTPException(status_code=422, detail=f'{key} must be an integer')
        lo, hi = allowed[key]
        if not lo <= value <= hi:
            raise HTTPException(status_code=422, detail=f'{key} must be between {lo} and {hi}')
        normalized[key] = value
    for key, value in normalized.items():
        set_setting(db, key, value)
    return snapshot()
