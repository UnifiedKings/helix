from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session

from ..auth import get_current_user, require_admin
from ..db import get_db
from ..models import User
from ..settings_store import get_settings, patch_settings

router = APIRouter(tags=["settings"])


def _subsonic_configured(settings: dict[str, Any]) -> bool:
    return bool(
        str(settings.get("subsonic_base_url") or "").strip()
        and str(settings.get("subsonic_username") or "").strip()
        and str(settings.get("subsonic_password") or "").strip()
    )


def _capabilities_payload(settings: dict[str, Any]) -> dict[str, Any]:
    subsonic_configured = _subsonic_configured(settings)
    return {
        "subsonic_configured": subsonic_configured,
        "features": {
            "library_search": subsonic_configured,
            "subsonic_import": subsonic_configured,
            "library_only_stations": subsonic_configured,
            "subsonic_playback": subsonic_configured,
            "ytmusic_discovery": True,
            "ytmusic_playback": True,
            "lobbies": True,
        },
    }

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


@router.get("/capabilities")
def get_capabilities(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _capabilities_payload(get_settings(db))
