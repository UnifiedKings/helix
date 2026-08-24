from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..db import get_db
from ..models import User
from ..user_settings_store import get_user_settings, patch_user_settings, reset_user_settings, user_setting_limits

router = APIRouter(prefix="/api/user/settings", tags=["user-settings"])


def _payload(db: Session, user: User) -> dict[str, Any]:
    return {
        "settings": get_user_settings(db, user.id),
        "limits": user_setting_limits(db),
    }


@router.get("")
def read_user_settings(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _payload(db, user)


@router.patch("")
def update_user_settings(
    payload: dict[str, Any] = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        patch_user_settings(db, user.id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"Unknown user setting: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _payload(db, user)


@router.delete("")
def reset_current_user_settings(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    reset_user_settings(db, user.id)
    return _payload(db, user)
