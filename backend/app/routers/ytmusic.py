from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query

from ..auth import get_current_user
from ..models import User
from ..cache import TTLCache
from ..integrations.ytmusic_search import find_album, find_track
from ..integrations.ytmusic_api import search_ytmusic


router = APIRouter(prefix="/api/ytmusic", tags=["ytmusic"])

# Small cache to avoid repeated yt-dlp calls when the user clicks around.
_CACHE: TTLCache[Dict[str, Any]] = TTLCache(max_items=4096)


@router.get("/find")
def ytmusic_find(
    kind: str = Query(..., description="'song' or 'album'"),
    title: str = Query(..., description="Song title (for kind=song) or album title (for kind=album)"),
    artist: str = Query(...),
    album: Optional[str] = Query(None, description="Album title (only for kind=song)"),
    duration_seconds: Optional[int] = Query(None, description="Song duration in seconds (optional)"),
    user: User = Depends(get_current_user),
):
    k = (kind or "").strip().lower()
    if k not in ("song", "album"):
        return {"found": False, "error": "kind must be 'song' or 'album'"}

    cache_key = f"{k}|{artist}|{title}|{album or ''}|{duration_seconds or ''}"
    hit = _CACHE.get(cache_key)
    if hit is not None:
        return hit

    if k == "song":
        res = find_track(title=title, artist=artist, album=album, duration_seconds=duration_seconds)
    else:
        res = find_album(album_title=title, artist=artist)

    payload: Dict[str, Any] = {
        "found": bool(res.found),
        "confidence": float(res.confidence),
        "video_id": res.video_id,
        "title": res.title,
        "uploader": res.uploader,
        "duration_seconds": res.duration_seconds,
        "youtube_url": res.youtube_url if res.video_id else "",
        "ytmusic_url": res.ytmusic_url if res.video_id else "",
    }

    _CACHE.set(cache_key, payload, ttl_seconds=60 * 10)  # 10 minutes
    return payload


@router.get("/search")
def ytmusic_search(
    q: str = Query(..., description="Search query"),
    song_limit: int = Query(15, ge=1, le=50),
    album_limit: int = Query(15, ge=1, le=50),
    user: User = Depends(get_current_user),
):
    """Search YouTube Music directly and return ONLY songs and albums.

    This powers the Helix 'YT MUSIC' search mode.
    """
    qq = (q or "").strip()
    if not qq:
        return {"songs": [], "albums": []}

    cache_key = f"search|{qq}|{song_limit}|{album_limit}"
    hit = _CACHE.get(cache_key)
    if hit is not None:
        return hit

    payload = search_ytmusic(qq, song_limit=song_limit, album_limit=album_limit)
    _CACHE.set(cache_key, payload, ttl_seconds=60 * 2)  # keep fresh-ish
    return payload


