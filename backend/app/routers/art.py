from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from ..auth import get_current_user
from ..db import SessionLocal
from ..settings_store import get_settings
from ..integrations.subsonic import SubsonicClient
from ..cache import TTLCache

LOG = logging.getLogger("helix.art")
_COVER_ART_CACHE: TTLCache[bytes] = TTLCache(max_items=512)
_COVER_ART_TTL_SECONDS = 60 * 60 * 24
router = APIRouter(prefix="/api/art", tags=["art"])


def _load_settings_short() -> Dict[str, Any]:
    db = SessionLocal()
    try:
        return dict(get_settings(db) or {})
    finally:
        db.close()


def _infer_int(v: Any, default: int) -> int:
    try:
        return int(v)
    except Exception:
        return default


async def _subsonic_client_from_settings(settings: Dict[str, Any]) -> SubsonicClient:
    base_url = str(settings.get("subsonic_base_url") or "").strip()
    username = str(settings.get("subsonic_username") or "").strip()
    password = str(settings.get("subsonic_password") or "").strip()
    if not base_url or not username or not password:
        raise HTTPException(status_code=400, detail="Subsonic settings incomplete. Set base_url, username, password in Admin Settings.")
    return SubsonicClient(
        base_url=base_url,
        username=username,
        password=password,
        client_name=str(settings.get("subsonic_client_name") or "Helix"),
        api_version=str(settings.get("subsonic_api_version") or "1.16.1"),
        timeout_s=_infer_int(settings.get("subsonic_timeout_s"), 20) or 20,
    )


def _guess_content_type(data: bytes) -> str:
    if not data:
        return "application/octet-stream"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def _image_response(data: bytes) -> Response:
    return Response(content=data, media_type=_guess_content_type(data), headers={"Cache-Control": "private, max-age=86400"})


def _is_image(data: bytes | None) -> bool:
    return bool(data) and _guess_content_type(data) != "application/octet-stream"


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text or "")).casefold()
    return re.sub(r"[^\w]+", " ", text).strip()


async def _fetch_by_cover_or_song(client: SubsonicClient, identifier: str, size: int) -> bytes | None:
    """An id may be a cover id OR a song id (older Helix URLs used both).

    Try the direct cover first. If unavailable, resolve getSong.coverArt rather than
    assuming the song id itself is the cover id.
    """
    data = await client.fetch_cover_art_bytes(identifier, size=size)
    if _is_image(data):
        return data
    try:
        song = await client.get_song(identifier)
    except Exception:
        song = None
    cover_id = str((song or {}).get("coverArt") or "").strip()
    if cover_id and cover_id != identifier:
        data = await client.fetch_cover_art_bytes(cover_id, size=size)
        if _is_image(data):
            return data
    return None


async def _find_song(client: SubsonicClient, *, song_id: str, title: str, artist: str, album: str) -> dict[str, Any] | None:
    if song_id:
        try:
            found = await client.get_song(song_id)
            if found:
                return found
        except Exception:
            LOG.debug("Subsonic song id lookup failed: %s", song_id, exc_info=True)
    if not title:
        return None
    if artist:
        try:
            found = await client.search_song_best(title, artist, album=album)
            if found and _normalize(str(found.get("title") or "")) == _normalize(title) and _normalize(str(found.get("artist") or "")) == _normalize(artist):
                return found
        except Exception:
            LOG.debug("Subsonic metadata art lookup failed: %r / %r", title, artist, exc_info=True)
        return None

    # Title-only fallback is allowed ONLY for a unique exact title + artist.
    # Otherwise identical song names across different artists would receive
    # unrelated covers. Do not guess when the library is ambiguous.
    try:
        result = await client.search3(title, song_count=100)
    except Exception:
        LOG.debug("Subsonic title-only art lookup failed: %r", title, exc_info=True)
        return None
    candidates = [s for s in (result.get("song") or []) if _normalize(str(s.get("title") or "")) == _normalize(title) and str(s.get("coverArt") or "").strip()]
    if not candidates:
        return None
    identities = {(_normalize(str(s.get("artist") or "")), _normalize(str(s.get("album") or ""))) for s in candidates}
    return candidates[0] if len(identities) == 1 else None


@router.get("/subsonic/{cover_id}")
async def subsonic_cover_art(
    cover_id: str,
    size: int = Query(512, ge=32, le=2048),
    user=Depends(get_current_user),
):
    cid = (cover_id or "").strip()
    if not cid:
        raise HTTPException(status_code=400, detail="cover_id is required")
    cache_key = f"cover:{cid}:{size}"
    cached = _COVER_ART_CACHE.get(cache_key)
    if cached:
        return _image_response(cached)
    client: Optional[SubsonicClient] = None
    try:
        client = await _subsonic_client_from_settings(_load_settings_short())
        data = await _fetch_by_cover_or_song(client, cid, size)
        if not data:
            raise HTTPException(status_code=404, detail="Cover art not found")
        _COVER_ART_CACHE.set(cache_key, data, ttl_seconds=_COVER_ART_TTL_SECONDS)
        return _image_response(data)
    except HTTPException:
        raise
    except Exception:
        LOG.exception("Subsonic cover art fetch failed for id=%s size=%s", cid, size)
        raise HTTPException(status_code=502, detail="Failed to fetch Subsonic cover art")
    finally:
        if client:
            await client.close()


@router.get("/resolve")
async def resolve_track_art(
    title: str = Query("", max_length=250),
    artist: str = Query("", max_length=250),
    album: str = Query("", max_length=250),
    subsonic_song_id: str = Query("", max_length=512),
    size: int = Query(512, ge=32, le=2048),
    user=Depends(get_current_user),
):
    """Artwork fallback for missing/broken track images, not a track ownership claim."""
    title, artist, album, subsonic_song_id = (str(v or "").strip() for v in (title, artist, album, subsonic_song_id))
    if not subsonic_song_id and not title:
        raise HTTPException(status_code=400, detail="Song id or title is required")
    key = f"resolve:{subsonic_song_id}:{_normalize(title)}:{_normalize(artist)}:{_normalize(album)}:{size}"
    cached = _COVER_ART_CACHE.get(key)
    if cached:
        return _image_response(cached)
    client: Optional[SubsonicClient] = None
    try:
        client = await _subsonic_client_from_settings(_load_settings_short())
        song = await _find_song(client, song_id=subsonic_song_id, title=title, artist=artist, album=album)
        if song is None:
            raise HTTPException(status_code=404, detail="No matching Subsonic song")
        cover_id = str(song.get("coverArt") or "").strip()
        if not cover_id:
            raise HTTPException(status_code=404, detail="Subsonic song has no cover art")
        data = await _fetch_by_cover_or_song(client, cover_id, size)
        if not data:
            raise HTTPException(status_code=404, detail="Subsonic cover art unavailable")
        _COVER_ART_CACHE.set(key, data, ttl_seconds=_COVER_ART_TTL_SECONDS)
        return _image_response(data)
    except HTTPException:
        raise
    except Exception:
        LOG.exception("Subsonic artwork fallback failed for %r / %r", title, artist)
        raise HTTPException(status_code=502, detail="Subsonic artwork lookup failed")
    finally:
        if client:
            await client.close()
