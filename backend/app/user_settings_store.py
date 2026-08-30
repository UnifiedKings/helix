from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import UserSetting
from .settings_store import get_settings


USER_DEFAULTS: dict[str, Any] = {
    # Appearance
    # Core palette. These map directly to Helix design tokens so users can
    # recolor the application without relying on custom CSS for ordinary theming.
    "appearance_accent_color": "#a95f18",
    "appearance_accent_contrast_color": "#fff8ef",
    "appearance_logo_follow_accent": True,
    "appearance_logo_color": "#d66f12",
    "appearance_background_color": "#080a0d",
    "appearance_surface_color": "#0d1014",
    "appearance_surface_soft_color": "#12161b",
    "appearance_surface_raised_color": "#171b20",
    "appearance_sidebar_color": "#0a0d10",
    "appearance_queue_color": "#0d1013",
    "appearance_player_color": "#0b0d10",
    "appearance_control_color": "#10141a",
    "appearance_text_color": "#f5f2ec",
    "appearance_muted_color": "#aaa9a5",
    "appearance_faint_color": "#747570",
    "appearance_border_color": "#252a31",
    "appearance_danger_color": "#ff647d",
    "appearance_success_color": "#35e09b",
    "appearance_reduce_motion": False,
    "appearance_artwork_backgrounds": True,
    "appearance_ui_density": "comfortable",
    "appearance_artwork_radius": "soft",

    # Queue
    # Controls what "Add to queue" means for this user's normal playback queue.
    # Playback actions themselves still intentionally replace the queue.
    "queue_add_position": "append",
    "queue_show_duration": True,
    "queue_show_playing_indicator": True,

    # Playback / discovery
    "playback_default_volume": 0.85,
    "playback_bar_style": "helix",
    "search_default_mode": "hybrid",
    "search_default_tab": "songs",

    # Stations. This is capped by the global station_queue_ahead_max setting.
    "station_queue_ahead": 3,

    # Lobby creation defaults
    "lobbies_default_name": "Shared Lobby",
    "lobbies_default_guests_can_add": False,
    "lobbies_auto_copy_invite": False,

    # Notifications
    "notifications_import_queued": True,
    "notifications_duration": "normal",

    # Power-user escape hatch. Loaded after normal Helix CSS.
    "advanced_custom_css": "",
}

USER_SETTING_KEYS = frozenset(USER_DEFAULTS)


def _loads(value_json: str, fallback: Any) -> Any:
    try:
        return json.loads(value_json)
    except Exception:
        return fallback


def user_setting_limits(db: Session) -> dict[str, Any]:
    global_settings = get_settings(db)
    try:
        station_max = int(global_settings.get("station_queue_ahead_max", 10) or 10)
    except Exception:
        station_max = 10
    station_max = max(1, min(50, station_max))
    return {"station_queue_ahead_max": station_max}


def _validated_value(db: Session, key: str, value: Any) -> Any:
    if key not in USER_SETTING_KEYS:
        raise KeyError(key)

    if key in {
        "appearance_accent_color",
        "appearance_accent_contrast_color",
        "appearance_logo_color",
        "appearance_background_color",
        "appearance_surface_color",
        "appearance_surface_soft_color",
        "appearance_surface_raised_color",
        "appearance_sidebar_color",
        "appearance_queue_color",
        "appearance_player_color",
        "appearance_control_color",
        "appearance_text_color",
        "appearance_muted_color",
        "appearance_faint_color",
        "appearance_border_color",
        "appearance_danger_color",
        "appearance_success_color",
    }:
        raw = str(value or "").strip()
        if len(raw) == 7 and raw.startswith("#"):
            try:
                int(raw[1:], 16)
                return raw.lower()
            except ValueError:
                pass
        raise ValueError(f"{key} must be a #RRGGBB hex color")

    if key in {
        "appearance_logo_follow_accent",
        "appearance_reduce_motion",
        "appearance_artwork_backgrounds",
        "queue_show_duration",
        "queue_show_playing_indicator",
        "lobbies_default_guests_can_add",
        "lobbies_auto_copy_invite",
        "notifications_import_queued",
    }:
        return bool(value)

    if key == "queue_add_position":
        raw = str(value or "").strip().lower()
        if raw not in {"append", "next"}:
            raise ValueError("Queue add position must be append or next")
        return raw

    if key == "appearance_ui_density":
        raw = str(value or "").strip().lower()
        if raw not in {"compact", "comfortable", "spacious"}:
            raise ValueError("Invalid UI density")
        return raw

    if key == "appearance_artwork_radius":
        raw = str(value or "").strip().lower()
        if raw not in {"square", "soft", "rounded"}:
            raise ValueError("Invalid artwork corner style")
        return raw

    if key == "notifications_duration":
        raw = str(value or "").strip().lower()
        if raw not in {"short", "normal", "long"}:
            raise ValueError("Invalid notification duration")
        return raw

    if key == "playback_default_volume":
        try:
            return max(0.0, min(1.0, float(value)))
        except Exception as exc:
            raise ValueError("Default volume must be between 0 and 1") from exc

    if key == "playback_bar_style":
        raw = str(value or "").strip().lower()
        if raw not in {"helix", "ytmusic", "spotify", "pandora"}:
            raise ValueError("Invalid playbar style")
        return raw

    if key == "search_default_mode":
        raw = str(value or "").strip().lower()
        if raw not in {"hybrid", "subsonic", "ytmusic"}:
            raise ValueError("Invalid search mode")
        return raw

    if key == "search_default_tab":
        raw = str(value or "").strip().lower()
        if raw not in {"songs", "albums", "artists"}:
            raise ValueError("Invalid default search tab")
        return raw

    if key == "station_queue_ahead":
        try:
            requested = int(value)
        except Exception as exc:
            raise ValueError("Station queue-ahead must be a number") from exc
        maximum = int(user_setting_limits(db)["station_queue_ahead_max"])
        return max(1, min(maximum, requested))

    if key == "lobbies_default_name":
        raw = str(value or "").strip()
        if not raw:
            return "Shared Lobby"
        if len(raw) > 80:
            raise ValueError("Default lobby name is limited to 80 characters")
        return raw

    if key == "advanced_custom_css":
        raw = str(value or "")
        if len(raw) > 100_000:
            raise ValueError("Custom CSS is limited to 100,000 characters")
        return raw

    return value


def get_user_settings(db: Session, user_id: str) -> dict[str, Any]:
    out = dict(USER_DEFAULTS)
    rows = db.execute(select(UserSetting).where(UserSetting.user_id == user_id)).scalars().all()
    for row in rows:
        if row.key not in USER_SETTING_KEYS:
            continue
        out[row.key] = _loads(row.value_json, USER_DEFAULTS.get(row.key))

    # Re-validate/clamp values against current admin ceilings. This means lowering
    # a global maximum takes effect immediately without rewriting every user row.
    for key in tuple(out):
        try:
            out[key] = _validated_value(db, key, out[key])
        except (ValueError, KeyError):
            out[key] = USER_DEFAULTS[key]
    return out


def patch_user_settings(db: Session, user_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(patch) - USER_SETTING_KEYS)
    if unknown:
        raise KeyError(", ".join(unknown))

    for key, raw_value in patch.items():
        value = _validated_value(db, key, raw_value)
        row = db.execute(
            select(UserSetting).where(UserSetting.user_id == user_id, UserSetting.key == key)
        ).scalar_one_or_none()
        if row is None:
            row = UserSetting(user_id=user_id, key=key)
        row.value_json = json.dumps(value)
        row.updated_at = datetime.utcnow()
        db.add(row)
    db.commit()
    return get_user_settings(db, user_id)


def reset_user_settings(db: Session, user_id: str) -> dict[str, Any]:
    rows = db.execute(select(UserSetting).where(UserSetting.user_id == user_id)).scalars().all()
    for row in rows:
        db.delete(row)
    db.commit()
    return get_user_settings(db, user_id)


def station_queue_ahead_for_user(db: Session, user_id: str) -> int:
    prefs = get_user_settings(db, user_id)
    maximum = int(user_setting_limits(db)["station_queue_ahead_max"])
    try:
        preferred = int(prefs.get("station_queue_ahead", USER_DEFAULTS["station_queue_ahead"]))
    except Exception:
        preferred = int(USER_DEFAULTS["station_queue_ahead"])
    return max(1, min(maximum, preferred))


def queue_add_position_for_user(db: Session, user_id: str) -> str:
    """Return where explicit Add-to-Queue actions should place new items.

    This preference only affects the user's normal queue append endpoints.
    Play-song/album/playlist actions continue to replace the queue.
    """
    prefs = get_user_settings(db, user_id)
    value = str(prefs.get("queue_add_position", USER_DEFAULTS["queue_add_position"]) or "").strip().lower()
    return value if value in {"append", "next"} else "append"
