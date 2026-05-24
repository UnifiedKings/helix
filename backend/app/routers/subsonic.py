from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from ..auth import get_current_user
from ..cache import TTLCache
from ..db import SessionLocal
from ..integrations.subsonic import SubsonicClient
from ..models import User
from ..rate_limit import RATE_LIMITER, make_key
from ..settings_store import get_settings

router = APIRouter(prefix="/api/subsonic", tags=["subsonic"])

# Cache results so search typing doesn't hammer Subsonic.
_song_cache: TTLCache[Dict[str, Any]] = TTLCache(max_items=4096)
_album_cache: TTLCache[Dict[str, Any]] = TTLCache(max_items=2048)

# IMPORTANT: cache misses briefly so newly-added items show up quickly.
_SONG_HIT_TTL_SECONDS = 24 * 3600
_SONG_MISS_TTL_SECONDS = 60

_ALBUM_HIT_TTL_SECONDS = 24 * 3600
_ALBUM_MISS_TTL_SECONDS = 60


def _subsonic_client_from_settings(settings: Dict[str, Any]) -> Optional[SubsonicClient]:
    base_url = (settings.get("subsonic_base_url") or "").strip()
    username = (settings.get("subsonic_username") or "").strip()
    password = (settings.get("subsonic_password") or "").strip()
    if not base_url or not username or not password:
        return None

    return SubsonicClient(
        base_url=base_url,
        username=username,
        password=password,
        client_name=settings.get("subsonic_client_name") or "Helix",
        api_version=settings.get("subsonic_api_version") or "1.16.1",
        timeout_s=int(settings.get("subsonic_timeout_s") or 20),
    )


@router.post("/resolve", response_model=dict[str, Any])
async def resolve_subsonic(
    request: Request,
    user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Batch resolver for Subsonic availability.

    Payload:
      {
        "songs": [{"key","title","artist","album","yt_video_id"}],
        "albums": [{"key","title","artist","browse_id"}]
      }

    Response:
      { "songs": {key: {available, subsonic_song_id}}, "albums": {key: {available, subsonic_album_id}} }
    """
    ip = getattr(getattr(request, "client", None), "host", "") or ""
    if not RATE_LIMITER.allow(
        make_key(scope="subsonic_resolve", user_id=str(user.id), ip=ip),
        limit=30,
        window_s=10,
    ):
        raise HTTPException(status_code=429, detail="Too many resolve requests")

    payload = await request.json()
    songs_in: List[Dict[str, Any]] = payload.get("songs") or []
    albums_in: List[Dict[str, Any]] = payload.get("albums") or []

    settings = _load_settings_short()
    client = _subsonic_client_from_settings(settings)
    if client is None:
        # Subsonic not configured -> nothing available.
        return {
            "songs": {s.get("key", ""): {"available": False, "subsonic_song_id": None} for s in songs_in if s.get("key")},
            "albums": {a.get("key", ""): {"available": False, "subsonic_album_id": None} for a in albums_in if a.get("key")},
        }

    songs_out: Dict[str, Any] = {}
    for s in songs_in:
        key = s.get("key")
        if not key:
            continue

        cached = _song_cache.get(key)
        if cached is not None:
            songs_out[key] = cached
            continue

        title = s.get("title") or ""
        artist = s.get("artist") or ""
        duration_ms = s.get("duration_ms") or None

        sub_id = None
        try:
            match = await client.search_song_best(title=title, artist=artist, duration_ms=duration_ms)
            if match:
                sub_id = match.get("id")
        except Exception:
            sub_id = None

        result = {"available": bool(sub_id), "subsonic_song_id": sub_id}
        songs_out[key] = result
        _song_cache.set(key, result, _SONG_HIT_TTL_SECONDS if sub_id else _SONG_MISS_TTL_SECONDS)

    albums_out: Dict[str, Any] = {}
    for a in albums_in:
        key = a.get("key")
        if not key:
            continue

        cached = _album_cache.get(key)
        if cached is not None:
            albums_out[key] = cached
            continue

        # If you later re-enable album completeness, this is where that logic goes.
        albums_out[key] = {"available": False, "subsonic_album_id": None}
        _album_cache.set(key, albums_out[key], _ALBUM_MISS_TTL_SECONDS)

    try:
        await client.close()
    except Exception:
        pass

    return {"songs": songs_out, "albums": albums_out}


def invalidate_song_cache(key: str) -> None:
    # Called by /add endpoints so badges appear immediately after successful enqueue/import.
    _song_cache.delete(key)


def invalidate_album_cache(key: str) -> None:
    _album_cache.delete(key)
