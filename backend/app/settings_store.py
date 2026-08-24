from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Setting

# Defaults double as documentation and provide stable behavior
DEFAULTS: dict[str, Any] = {
    # Where Helix (or your downstream player) should talk to your Subsonic-compatible library.
    # Example: "http://navidrome:4533"
    "subsonic_base_url": "",
    "subsonic_username": "",
    "subsonic_password": "",
    "subsonic_client_name": "Helix",
    "subsonic_api_version": "1.16.1",
    "subsonic_timeout_s": 20,

    # Playback queue behavior
    "player_max_queue_items": 500,
    # Maximum number of station-generated tracks a user may choose to keep ahead.
    # Individual users may select any value from 1 up to this ceiling.
    "station_queue_ahead_max": 10,
    # Number of upcoming queue items Helix proactively downloads. This is a
    # server resource policy and remains independent of each user's logical
    # station queue-ahead preference.
    "download_prefetch_ahead": 1,
    # Keep missing items in the queue so Helix can fulfill them on-demand.
    "player_omit_missing": False,

    # Number of listen-history rows retained per user. History API pagination is separate.
    "listen_history_retention": 10000,

    # Future: fulfilled (lossy) tracks location within your Navidrome library
    # Example: "/music/Helix YouTube"
    "fulfillment_library_subfolder": "Helix YouTube",
    # Tag key/value to mark fulfilled tracks in metadata
    "fulfillment_tag_comment": "Downloaded from YouTube by Helix",

    # How long Helix should wait before it considers a first-play "fulfillment" attempt to be too slow.
    # (This doesn't limit background downloads — it's purely for UX thresholds.)
    "fulfillment_first_play_timeout_seconds": 10,

    # Placeholder for selecting default behavior when multiple versions exist.
    # Examples: "prefer_studio", "prefer_highest_quality", "prefer_shortest_latency"
    "fulfillment_version_preference": "prefer_studio",


    # Catalog/search preference: which country to prefer when picking a "representative" release.
    # This affects cover art defaults and which tracklist is shown when you click an Album.
    "search_default_country": "US",

    # Whether to hide non-official releases when listing "Other versions".
    "search_hide_non_official": True,

    # If True, representative release selection prefers the earliest official release date even if it is not in the default country.
    # If False (default), the default country wins when available.
    "search_prefer_original_release": False,

    # --- Artist image enrichment ---

    # If True, and no Wikidata photo is available, fall back to representative album art for the artist.
    "artist_images_fallback_to_album_art": True,

    # MusicBrainz request throttle (minimum interval between requests).
    "musicbrainz_min_interval_ms": 1000,

    # User-Agent string sent to MusicBrainz (they require a descriptive UA). Set to something with contact info.
    "musicbrainz_user_agent": "Helix/0.1 (admin@example.invalid)",

}

def _loads(value_json: str, fallback: Any) -> Any:
    try:
        return json.loads(value_json)
    except Exception:
        return fallback

def get_settings(db: Session) -> dict[str, Any]:
    """
    Returns settings as an object:
    - starts with DEFAULTS
    - overlays every row from the DB (unknown keys allowed)
    - optional backward-compat migrations
    """
    out: dict[str, Any] = dict(DEFAULTS)

    rows = db.execute(select(Setting)).scalars().all()
    for row in rows:
        fallback = DEFAULTS.get(row.key, None)
        out[row.key] = _loads(row.value_json, fallback)

    # Back-compat: some older UI versions PATCHed {"settings": {...}}
    if "settings" in out and isinstance(out["settings"], dict):
        return out["settings"]

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
