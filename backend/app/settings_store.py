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
    # Keep missing items in the queue so Helix can fulfill them on-demand.
    "player_omit_missing": False,

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

    # Track search preference: if True, hide track results that cannot be anchored to a release with a cover thumbnail.
    # (Practically: if we can't pick any representative release for a recording, we drop it from search results.)
    "search_hide_tracks_without_art": False,

    # --- Artist image enrichment ---
    # If True, Helix will try to resolve artist photos via MusicBrainz -> Wikipedia (page summary thumbnail).
    "artist_images_enable_wikipedia": True,

    # If True, and no Wikidata photo is available, fall back to representative album art for the artist.
    "artist_images_fallback_to_album_art": True,

    # If True, Helix will proxy & cache remote thumbnails (Wikimedia / Cover Art Archive) to improve speed and stability.
    "image_proxy_enabled": True,

    # Maximum on-disk size (MB) for the thumbnail cache.
    "image_cache_max_mb": 500,

    # Target thumbnail width for proxied images.
    "image_cache_thumb_px": 256,

    # How long (days) to keep cached thumbnails before they become eligible for eviction.
    "image_cache_ttl_days": 90,

    # Caching for search responses (seconds). Keep short so results stay responsive, but avoid hammering upstream services.
    "search_cache_ttl_seconds": 300,

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
