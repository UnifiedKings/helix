from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..db import get_db
from ..models import User
from ..user_settings_store import get_user_settings, patch_user_settings, reset_user_settings, user_setting_limits

router = APIRouter(prefix="/api/user/settings", tags=["user-settings"])

# Per-user secrets: never echoed back to the client; a blank/"********" value on
# PATCH means "keep the currently configured value".
USER_SECRET_KEYS = frozenset({"listenbrainz_token"})


def _redact_secrets(settings: dict[str, Any]) -> dict[str, Any]:
    out = dict(settings)
    for key in USER_SECRET_KEYS:
        if key in out:
            out[key] = ""
    return out


def _strip_secret_placeholders(payload: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in payload.items():
        if key in USER_SECRET_KEYS and (value is None or str(value) == "" or str(value).startswith("********")):
            continue
        clean[key] = value
    return clean


def _payload(db: Session, user: User) -> dict[str, Any]:
    return {
        "settings": _redact_secrets(get_user_settings(db, user.id)),
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
        patch_user_settings(db, user.id, _strip_secret_placeholders(payload))
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
