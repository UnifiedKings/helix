from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Setting

# Defaults double as documentation and provide stable behavior
DEFAULTS: dict[str, Any] = {
    "subsonic_base_url": "",
    "subsonic_username": "",
    "subsonic_password": "",
    "subsonic_client_name": "Helix",
    "subsonic_api_version": "1.16.1",
    "subsonic_timeout_s": 20,
    "allow_all_users_subsonic_import": False,
    "player_max_queue_items": 500,
    "player_omit_missing": False,
    "listen_history_retention": 10000,
    "fulfillment_library_subfolder": "Helix YouTube",
    "fulfillment_tag_comment": "Downloaded from YouTube by Helix",
    "fulfillment_first_play_timeout_seconds": 10,
    "fulfillment_version_preference": "prefer_studio",
    "search_default_country": "US",
    "search_hide_non_official": True,
    "search_prefer_original_release": False,
    "artist_images_fallback_to_album_art": True,
    "musicbrainz_min_interval_ms": 1000,
    "musicbrainz_user_agent": "Helix/0.1 (admin@example.invalid)",

    # Optional slskd quality-upgrade layer. Playback and initial fulfillment
    # continue to use YTMusic; slskd is never in the critical playback path.
    "slskd_enabled": False,
    "slskd_url": "",
    "slskd_api_key": "",
    "slskd_downloads_path": "",
    "slskd_timeout_s": 20,
    "slskd_search_timeout_s": 35,
    "slskd_download_timeout_s": 900,
    "slskd_max_results": 200,
    "slskd_concurrent_searches": 2,
    "slskd_match_threshold": 78.0,
    # Conservative quality policy. These apply only to Helix-owned tracks for
    # now; provenance metadata intentionally leaves room for a future opt-in
    # adoption workflow for pre-existing library files.
    "quality_upgrade_lossless_only": True,
    "quality_upgrade_min_sample_rate": 44100,
    "quality_upgrade_min_bit_depth": 16,
    "quality_upgrade_replace_lossless": False,
}


def _loads(value_json: str, fallback: Any) -> Any:
    try:
        return json.loads(value_json)
    except Exception:
        return fallback


def get_settings(db: Session) -> dict[str, Any]:
    out: dict[str, Any] = dict(DEFAULTS)
    rows = db.execute(select(Setting)).scalars().all()
    for row in rows:
        fallback = DEFAULTS.get(row.key, None)
        out[row.key] = _loads(row.value_json, fallback)

    if "settings" in out and isinstance(out["settings"], dict):
        out = out["settings"]

    # Environment variables are authoritative when present. This keeps secrets
    # suitable for Docker/Compose while still allowing UI configuration.
    env_map = {
        "SLSKD_URL": "slskd_url",
        "SLSKD_API_KEY": "slskd_api_key",
        "SLSKD_DOWNLOADS_PATH": "slskd_downloads_path",
    }
    for env_key, setting_key in env_map.items():
        value = os.getenv(env_key)
        if value is not None and value.strip():
            out[setting_key] = value.strip()

    enabled_env = os.getenv("SLSKD_ENABLED")
    if enabled_env is not None and enabled_env.strip():
        out["slskd_enabled"] = enabled_env.strip().lower() in {"1", "true", "yes", "on"}

    return out


def set_setting(db: Session, key: str, value: Any) -> None:
    row = db.execute(select(Setting).where(Setting.key == key)).scalar_one_or_none()
    if not row:
        row = Setting(key=key, value_json=json.dumps(value), updated_at=datetime.utcnow())
    else:
        row.value_json = json.dumps(value)
        row.updated_at = datetime.utcnow()
    db.add(row)
    db.commit()


def patch_settings(db: Session, patch: dict[str, Any]) -> dict[str, Any]:
    for key, value in patch.items():
        set_setting(db, key, value)
    return get_settings(db)
