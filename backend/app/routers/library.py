from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from ..auth import get_current_user
from ..cache import TTLCache
from ..db import SessionLocal
from ..integrations.subsonic import SubsonicClient
from ..models import User
from ..rate_limit import RATE_LIMITER, make_key
from ..settings_store import get_settings
from ..subsonic_shapes import (
    subsonic_albums_to_results,
    subsonic_artist_to_result,
    subsonic_songs_to_results,
)

router = APIRouter(prefix="/api/subsonic/library", tags=["subsonic-library"])

# Browse shapes exposed by getAlbumList2.
_ALBUM_TYPES = (
    "newest",
    "recent",
    "frequent",
    "random",
    "starred",
    "alphabeticalByName",
    "alphabeticalByArtist",
)

# Song-list views: Subsonic has no "all songs" list, so the Songs tab maps to
# random selection or starred tracks.
_SONG_TYPES = ("random", "starred")

_LIBRARY_CACHE: TTLCache[Dict[str, Any]] = TTLCache(max_items=4096)
_LIBRARY_ALBUM_TTL_S = 60 * 5
_LIBRARY_ARTIST_TTL_S = 60 * 15
_LIBRARY_SONG_TTL_S = 60 * 5


def _load_settings_short() -> Dict[str, Any]:
    db = SessionLocal()
    try:
        return dict(get_settings(db) or {})
    finally:
        db.close()


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


def _client_ip(request: Request) -> str:
    try:
        return (request.client.host if request and request.client else "") or ""
    except Exception:
        return ""


def _settings_identity(settings: Dict[str, Any]) -> str:
    """Bust the library cache when the connected Subsonic server changes."""
    return "|".join(
        [
            str(settings.get("subsonic_base_url") or "").strip().rstrip("/").casefold(),
            str(settings.get("subsonic_username") or "").strip(),
        ]
    )


def _cache_key(*parts: Any) -> str:
    return "|".join(str(part) for part in parts)


@router.get("/albums", response_model=Dict[str, Any])
async def library_albums(
    request: Request,
    type: str = Query("newest", description="getAlbumList2 type"),
    offset: int = Query(0, ge=0, le=100000),
    size: int = Query(24, ge=1, le=200),
    user: User = Depends(get_current_user),
):
    """Browse Subsonic albums (newest, recent, frequent, random, starred, A-Z)."""
    ip = _client_ip(request)
    if not RATE_LIMITER.allow(make_key(scope="subsonic:library:albums", user_id=str(user.id), ip=ip), limit=60, window_s=60):
        raise HTTPException(status_code=429, detail="Too many requests")

    album_type = str(type or "newest").strip()
    if album_type not in _ALBUM_TYPES:
        album_type = "newest"

    settings = _load_settings_short()
    client = _subsonic_client_from_settings(settings)
    if client is None:
        raise HTTPException(status_code=503, detail="Subsonic is not configured.")

    cache_key = _cache_key("lib:albums", _settings_identity(settings), album_type, offset, size)
    hit = _LIBRARY_CACHE.get(cache_key)
    if hit is not None:
        return hit

    try:
        raw_albums = await client.get_albums2(kind=album_type, offset=offset, size=size)
    except Exception:
        raise HTTPException(status_code=502, detail="Subsonic album list request failed.")
    finally:
        await client.close()

    albums = subsonic_albums_to_results(raw_albums)
    payload = {
        "type": album_type,
        "offset": offset,
        "size": size,
        "count": len(albums),
        "has_more": len(albums) >= size,
        "albums": albums,
    }
    _LIBRARY_CACHE.set(cache_key, payload, _LIBRARY_ALBUM_TTL_S)
    return payload


@router.get("/artists", response_model=Dict[str, Any])
async def library_artists(
    request: Request,
    user: User = Depends(get_current_user),
):
    """Return the Subsonic A-Z artist index."""
    ip = _client_ip(request)
    if not RATE_LIMITER.allow(make_key(scope="subsonic:library:artists", user_id=str(user.id), ip=ip), limit=30, window_s=60):
        raise HTTPException(status_code=429, detail="Too many requests")

    settings = _load_settings_short()
    client = _subsonic_client_from_settings(settings)
    if client is None:
        raise HTTPException(status_code=503, detail="Subsonic is not configured.")

    cache_key = _cache_key("lib:artists", _settings_identity(settings))
    hit = _LIBRARY_CACHE.get(cache_key)
    if hit is not None:
        return hit

    try:
        raw_artists = await client.get_artists()
    except Exception:
        raise HTTPException(status_code=502, detail="Subsonic artist index request failed.")
    finally:
        await client.close()

    artists = [subsonic_artist_to_result(artist) for artist in raw_artists]
    payload = {"count": len(artists), "artists": artists}
    _LIBRARY_CACHE.set(cache_key, payload, _LIBRARY_ARTIST_TTL_S)
    return payload


@router.get("/artists/{artist_id}", response_model=Dict[str, Any])
async def library_artist_detail(
    request: Request,
    artist_id: str,
    user: User = Depends(get_current_user),
):
    """Return a Subsonic artist in the same shape ArtistDetailPage renders for YTMusic."""
    ip = _client_ip(request)
    if not RATE_LIMITER.allow(make_key(scope="subsonic:library:artist", user_id=str(user.id), ip=ip), limit=40, window_s=60):
        raise HTTPException(status_code=429, detail="Too many requests")

    artist_id = str(artist_id or "").strip()
    if not artist_id:
        raise HTTPException(status_code=404, detail="Artist not found")

    settings = _load_settings_short()
    client = _subsonic_client_from_settings(settings)
    if client is None:
        raise HTTPException(status_code=503, detail="Subsonic is not configured.")

    cache_key = _cache_key("lib:artist", _settings_identity(settings), artist_id)
    hit = _LIBRARY_CACHE.get(cache_key)
    if hit is not None:
        return hit

    try:
        artist = await client.get_artist_detail(artist_id)
        if not artist:
            raise HTTPException(status_code=404, detail="Artist not found in Subsonic.")
        songs = await client.get_artist_songs(artist_id)
        info = await client.get_artist_info(artist_id)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=502, detail="Subsonic artist request failed.")
    finally:
        await client.close()

    raw_albums = [album for album in (artist.get("album") or []) if isinstance(album, dict)]
    album_count = len(raw_albums)

    biography = str((info or {}).get("biography") or "").strip()
    similar: List[Dict[str, Any]] = []
    for row in (info or {}).get("similarArtist") or []:
        if not isinstance(row, dict):
            continue
        sid = str(row.get("id") or "").strip()
        name = str(row.get("name") or "").strip()
        if not sid or not name:
            continue
        similar.append({
            "kind": "artist",
            "source": "subsonic",
            "browse_id": sid,
            "artist_id": sid,
            "name": name,
            "album_count": 0,
            "thumbnail_url": "",
            "art_url": "",
        })

    payload = {
        "artist": {
            "source": "subsonic",
            "browse_id": artist_id,
            "artist_id": artist_id,
            "name": str(artist.get("name") or ""),
            "albums_count": album_count,
            "songs_count": len(songs),
            "description": biography,
            "description_source": "subsonic" if biography else "",
            "thumbnail_url": "",
            "art_url": "",
        },
        "albums": subsonic_albums_to_results(raw_albums),
        "singles": [],
        "songs": subsonic_songs_to_results(songs),
        "similar_artists": similar,
    }
    _LIBRARY_CACHE.set(cache_key, payload, _LIBRARY_ARTIST_TTL_S)
    return payload


@router.get("/songs", response_model=Dict[str, Any])
async def library_songs(
    request: Request,
    type: str = Query("random", description="Song list view: random or starred"),
    size: int = Query(100, ge=1, le=500),
    user: User = Depends(get_current_user),
):
    """Return a Subsonic song list (random selection or starred favorites)."""
    ip = _client_ip(request)
    if not RATE_LIMITER.allow(make_key(scope="subsonic:library:songs", user_id=str(user.id), ip=ip), limit=60, window_s=60):
        raise HTTPException(status_code=429, detail="Too many requests")

    song_type = str(type or "random").strip()
    if song_type not in _SONG_TYPES:
        song_type = "random"

    settings = _load_settings_short()
    client = _subsonic_client_from_settings(settings)
    if client is None:
        raise HTTPException(status_code=503, detail="Subsonic is not configured.")

    cache_key = _cache_key("lib:songs", _settings_identity(settings), song_type, size)
    hit = _LIBRARY_CACHE.get(cache_key)
    if hit is not None:
        return hit

    try:
        if song_type == "starred":
            starred = await client.get_starred2()
            raw_songs = [song for song in (starred.get("song") or []) if isinstance(song, dict)]
        else:
            raw_songs = await client.get_random_songs(size=size)
    except Exception:
        raise HTTPException(status_code=502, detail="Subsonic song list request failed.")
    finally:
        await client.close()

    payload = {"type": song_type, "songs": subsonic_songs_to_results(raw_songs)}
    _LIBRARY_CACHE.set(cache_key, payload, _LIBRARY_SONG_TTL_S)
    return payload