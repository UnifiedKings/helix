from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session

from ..auth import get_current_user, require_admin
from ..db import get_db
from ..models import User
from ..settings_store import get_settings, patch_settings
from ..subsonic_permissions import can_import_to_subsonic

router = APIRouter(tags=["settings"])

SECRET_SETTING_KEYS = {
    "subsonic_password",
    "listenbrainz_token",
    "ytmusic_cookie",
    "ytmusic_cookies",
    "slskd_api_key",
}

PUBLIC_SETTING_KEYS = {
    "subsonic_configured",
    "subsonic_client_name",
    "subsonic_api_version",
    "subsonic_timeout_s",
    "allow_all_users_subsonic_import",
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
    "station_queue_ahead_max",
    "download_prefetch_ahead",
    "player_omit_missing",
    "listen_history_retention",
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


def _capabilities_payload(db: Session, settings: dict[str, Any], user: User | None = None) -> dict[str, Any]:
    subsonic_configured = _subsonic_configured(settings)
    import_allowed = bool(user and can_import_to_subsonic(db, user))
    return {
        "subsonic_configured": subsonic_configured,
        "features": {
            "library_search": subsonic_configured,
            "subsonic_import": subsonic_configured and import_allowed,
            "quality_upgrades": subsonic_configured and import_allowed,
            "library_only_stations": subsonic_configured,
            "subsonic_playback": subsonic_configured,
            "ytmusic_discovery": True,
            "ytmusic_playback": True,
            "lobbies": True,
        },
    }


@router.get("/api/settings")
def get_public_settings(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _redact_settings(get_settings(db), admin=False)


@router.get("/api/admin/settings", tags=["admin", "settings"])
def admin_get_settings(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return _redact_settings(get_settings(db), admin=True)


@router.patch("/api/admin/settings", tags=["admin", "settings"])
def admin_patch_settings(
    payload: dict[str, Any] = Body(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    clean = _strip_unchanged_secret_placeholders(payload)
    if "station_queue_ahead_max" in clean:
        try:
            clean["station_queue_ahead_max"] = max(1, min(50, int(clean["station_queue_ahead_max"])))
        except Exception as exc:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="Maximum station tracks ahead must be between 1 and 50") from exc
    if "download_prefetch_ahead" in clean:
        try:
            clean["download_prefetch_ahead"] = max(0, min(20, int(clean["download_prefetch_ahead"])))
        except Exception as exc:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="Download tracks ahead must be between 0 and 20") from exc
    return _redact_settings(patch_settings(db, clean), admin=True)


@router.get("/capabilities")
def get_capabilities(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _capabilities_payload(db, get_settings(db), user)
