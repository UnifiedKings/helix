from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..db import get_db
from ..models import User, UserSetting
from ..user_settings_store import (
    USER_SETTING_KEYS,
    get_user_settings,
    patch_user_settings,
    user_setting_limits,
)

router = APIRouter(prefix="/api/user/settings", tags=["user-settings"])

PROFILE_DISPLAY_NAME_KEY = "profile_display_name"
PROFILE_AVATAR_KEY = "profile_avatar_data_url"
PROFILE_KEYS = frozenset({PROFILE_DISPLAY_NAME_KEY, PROFILE_AVATAR_KEY})
AVATAR_DATA_URL_RE = re.compile(r"^data:image/(?:png|jpeg|webp);base64,[A-Za-z0-9+/=]+$")


def _payload(db: Session, user: User) -> dict[str, Any]:
    return {
        "settings": get_user_settings(db, user.id),
        "limits": user_setting_limits(db),
    }


def _setting_row(db: Session, user_id: str, key: str) -> UserSetting | None:
    return db.execute(
        select(UserSetting).where(UserSetting.user_id == user_id, UserSetting.key == key)
    ).scalar_one_or_none()


def _profile_value(db: Session, user_id: str, key: str, fallback: str = "") -> str:
    row = _setting_row(db, user_id, key)
    if row is None:
        return fallback
    try:
        value = json.loads(row.value_json)
    except Exception:
        return fallback
    return value if isinstance(value, str) else fallback


def _write_profile_value(db: Session, user_id: str, key: str, value: str) -> None:
    row = _setting_row(db, user_id, key)
    if not value:
        if row is not None:
            db.delete(row)
        return
    if row is None:
        row = UserSetting(user_id=user_id, key=key)
    row.value_json = json.dumps(value)
    row.updated_at = datetime.utcnow()
    db.add(row)


def _profile_payload(db: Session, user: User) -> dict[str, str]:
    display_name = _profile_value(db, user.id, PROFILE_DISPLAY_NAME_KEY).strip()
    return {
        "username": user.username,
        "display_name": display_name or user.username,
        "avatar_data_url": _profile_value(db, user.id, PROFILE_AVATAR_KEY),
        "role": user.role,
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
    # Reset preferences without deleting profile identity. Profile values share
    # the UserSetting table, but are intentionally not part of USER_SETTING_KEYS.
    rows = db.execute(
        select(UserSetting).where(
            UserSetting.user_id == user.id,
            UserSetting.key.in_(tuple(USER_SETTING_KEYS)),
        )
    ).scalars().all()
    for row in rows:
        db.delete(row)
    db.commit()
    return _payload(db, user)


@router.get("/profile")
def read_user_profile(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _profile_payload(db, user)


@router.patch("/profile")
def update_user_profile(
    payload: dict[str, Any] = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    unknown = sorted(set(payload) - {"display_name", "avatar_data_url"})
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown profile field: {', '.join(unknown)}")

    if "display_name" in payload:
        display_name = str(payload.get("display_name") or "").strip()
        if len(display_name) > 64:
            raise HTTPException(status_code=400, detail="Display name is limited to 64 characters")
        _write_profile_value(db, user.id, PROFILE_DISPLAY_NAME_KEY, display_name)

    if "avatar_data_url" in payload:
        avatar = str(payload.get("avatar_data_url") or "").strip()
        if avatar:
            if len(avatar) > 500_000:
                raise HTTPException(status_code=400, detail="Avatar image is too large")
            if not AVATAR_DATA_URL_RE.fullmatch(avatar):
                raise HTTPException(status_code=400, detail="Avatar must be a PNG, JPEG, or WebP image")
        _write_profile_value(db, user.id, PROFILE_AVATAR_KEY, avatar)

    db.commit()
    return _profile_payload(db, user)
