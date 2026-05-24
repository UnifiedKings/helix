from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session

from ..auth import get_current_user, require_admin
from ..db import get_db
from ..models import User
from ..settings_store import get_settings, patch_settings

router = APIRouter(tags=["settings"])


@router.get("/settings")
def get_public_settings(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Read-only global settings for the authenticated UI."""
    return get_settings(db)


@router.get("/admin/settings", tags=["admin", "settings"])
def admin_get_settings(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return get_settings(db)


@router.patch("/admin/settings", tags=["admin", "settings"])
def admin_patch_settings(
    payload: dict[str, Any] = Body(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return patch_settings(db, payload)
