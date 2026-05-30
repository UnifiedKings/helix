from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session

from ..auth import get_current_user, require_admin
from ..db import get_db
from ..models import User
from ..settings_store import get_settings, patch_settings

router = APIRouter(tags=["settings"])

SECRET_SETTING_KEYS = {
    "subsonic_password",
    "listenbrainz_token",
    "ytmusic_cookie",
    "ytmusic_cookies",
}

PUBLIC_SETTING_KEYS = {
    "subsonic_configured",
    "subsonic_client_name",
    "subsonic_api_version",
    "subsonic_timeout_s",
    "player_max_queue_items",
    "player_omit_missing",
    "search_hide_non_official",
    "search_prefer_original_release",
}


ADMIN_SETTING_KEYS = {
    "subsonic_base_url",
    "subsonic_username",
    "subsonic_password",
    "subsonic_client_name",
    "subsonic_api_version",
    "subsonic_timeout_s",
    "player_max_queue_items",
    "player_omit_missing",
    "listen_history_limit",
    "fulfillment_library_subfolder",
    "fulfillment_tag_comment",
    "fulfillment_first_play_timeout_seconds",
    "fulfillment_version_preference",
    "search_default_country",
    "search_hide_non_official",
    "search_prefer_original_release",
    "musicbrainz_min_interval_ms",
    "musicbrainz_user_agent",
}


def _redact_settings(settings: dict[str, Any], *, admin: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {}
    allowed_keys = ADMIN_SETTING_KEYS if admin else PUBLIC_SETTING_KEYS

    for key, value in settings.items():
        if key not in allowed_keys:
            continue

        if key in SECRET_SETTING_KEYS or "password" in key.lower() or "token" in key.lower() or "secret" in key.lower():
            if admin:
                # Send a blank editable secret field, but do not expose or render
                # derived *_configured values as editable settings.
                out[key] = ""
            continue

        out[key] = value

    if not admin:
        out["subsonic_configured"] = _subsonic_configured(settings)

    return out


def _strip_unchanged_secret_placeholders(payload: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in payload.items():
        if key.endswith("_configured"):
            continue
        is_secret = key in SECRET_SETTING_KEYS or "password" in key.lower() or "token" in key.lower() or "secret" in key.lower()
        if is_secret and (value is None or str(value) == "" or str(value).startswith("********")):
            continue
        clean[key] = value
    return clean



def _subsonic_configured(settings: dict[str, Any]) -> bool:
    return bool(
        str(settings.get("subsonic_base_url") or "").strip()
        and str(settings.get("subsonic_username") or "").strip()
        and str(settings.get("subsonic_password") or "").strip()
    )


def _capabilities_payload(settings: dict[str, Any], user: User | None = None) -> dict[str, Any]:
    subsonic_configured = _subsonic_configured(settings)
    import_allowed = bool(user and (user.role == "admin" or os.getenv("HELIX_ALLOW_NON_ADMIN_IMPORT", "false").strip().lower() in {"1", "true", "yes", "on"}))
    return {
        "subsonic_configured": subsonic_configured,
        "features": {
            "library_search": subsonic_configured,
            "subsonic_import": subsonic_configured and import_allowed,
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
    return _redact_settings(get_settings(db), admin=False)


@router.get("/admin/settings", tags=["admin", "settings"])
def admin_get_settings(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return _redact_settings(get_settings(db), admin=True)


@router.patch("/admin/settings", tags=["admin", "settings"])
def admin_patch_settings(
    payload: dict[str, Any] = Body(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return _redact_settings(patch_settings(db, _strip_unchanged_secret_placeholders(payload)), admin=True)


@router.get("/capabilities")
def get_capabilities(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _capabilities_payload(get_settings(db), user)
