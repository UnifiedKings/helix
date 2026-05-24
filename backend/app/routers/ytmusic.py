from __future__ import annotations

import asyncio
import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Set, Tuple

from fastapi import APIRouter, Depends, Query, HTTPException, Request
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..db import get_db
from ..models import User
from ..settings_store import get_settings
from ..cache import TTLCache
from ..integrations.ytmusic import (
    find_album,
    find_track,
    get_artist_albums,
    get_artist_overview,
    get_artist_popular_songs,
    search_artists,
    search_ytmusic,
)
from ..integrations.listenbrainz import lb_similar_artists_for_artist, lb_top_recordings_for_artist
from ..integrations.subsonic import SubsonicClient
from ..integrations.musicbrainz import resolve_artist_mbid_from_yt
from ..rate_limit import RATE_LIMITER, make_key


router = APIRouter(prefix="/api/ytmusic", tags=["ytmusic"])

# Small cache to avoid repeated external calls when the user clicks around.
_CACHE: TTLCache[Dict[str, Any]] = TTLCache(max_items=4096)
_RESOLUTION_IN_FLIGHT: Set[str] = set()
_RESOLUTION_LOCK = asyncio.Lock()



_DETAIL_TTL_S = 60 * 30
_RESOLUTION_TTL_S = 60 * 60 * 24 * 30

_SUBSONIC_STRONG_SONG_THRESHOLD = 0.78
_SUBSONIC_STRONG_ALBUM_THRESHOLD = 0.82
_SUBSONIC_MAX_SONGS = 5
_SUBSONIC_MAX_ALBUMS = 5


def _subsonic_client_from_settings(settings: Dict[str, Any]) -> Optional[SubsonicClient]:
    base_url = str(settings.get("subsonic_base_url") or "").strip()
    username = str(settings.get("subsonic_username") or "").strip()
    password = str(settings.get("subsonic_password") or "").strip()
    if not base_url or not username or not password:
        return None
    return SubsonicClient(
        base_url=base_url,
        username=username,
        password=password,
        client_name=str(settings.get("subsonic_client_name") or "Helix"),
        api_version=str(settings.get("subsonic_api_version") or "1.16.1"),
        timeout_s=int(settings.get("subsonic_timeout_s") or 20),
    )


def _norm_search_text(s: str) -> str:
    s = (s or "").strip().lower()
    s = s.replace("’", "'").replace("`", "'").replace("´", "'")
    s = s.replace("–", "-").replace("—", "-")
    s = s.replace("&", " and ")
    s = re.sub(r"\bfeat\.?\b", " ", s)
    s = re.sub(r"\bft\.?\b", " ", s)
    s = re.sub(r"[\(\[][^\)\]]*(remaster|remastered|live|version|edit|mono|stereo|deluxe|expanded)[^\)\]]*[\)\]]", " ", s)
    s = s.replace("'", "")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def _token_overlap(a: str, b: str) -> float:
    aa = set(_norm_search_text(a).split())
    bb = set(_norm_search_text(b).split())
    if not aa or not bb:
        return 0.0
    return len(aa & bb) / max(1, len(aa | bb))


def _string_ratio(a: str, b: str) -> float:
    na = _norm_search_text(a)
    nb = _norm_search_text(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def _subsonic_song_score(query: str, title: str, artist: str, album: str = "") -> float:
    q = _norm_search_text(query)
    title_artist = f"{title} {artist}".strip()
    title_artist_album = f"{title} {artist} {album}".strip()
    ratios = [
        _string_ratio(q, title_artist),
        _string_ratio(q, title),
        _string_ratio(q, title_artist_album),
    ]
    overlap = max(
        _token_overlap(q, title_artist),
        _token_overlap(q, title),
        _token_overlap(q, title_artist_album),
    )
    contains_bonus = 0.08 if q and q in _norm_search_text(title_artist_album) else 0.0
    return max(ratios) * 0.82 + overlap * 0.18 + contains_bonus


def _subsonic_album_score(query: str, title: str, artist: str) -> float:
    q = _norm_search_text(query)
    title_artist = f"{title} {artist}".strip()
    ratios = [
        _string_ratio(q, title_artist),
        _string_ratio(q, title),
    ]
    overlap = max(_token_overlap(q, title_artist), _token_overlap(q, title))
    contains_bonus = 0.08 if q and q in _norm_search_text(title_artist) else 0.0
    return max(ratios) * 0.85 + overlap * 0.15 + contains_bonus


def _subsonic_song_to_result(song: Dict[str, Any]) -> Dict[str, Any]:
    cover_id = str(song.get("coverArt") or "").strip()
    return {
        "kind": "song",
        "source": "subsonic",
        "subsonic_song_id": str(song.get("id") or ""),
        "video_id": "",
        "title": str(song.get("title") or ""),
        "artist": str(song.get("artist") or ""),
        "album": str(song.get("album") or ""),
        "duration_seconds": int(song.get("duration") or 0) if str(song.get("duration") or "").isdigit() else 0,
        "thumbnail_url": f"/api/art/subsonic/{cover_id}?size=512" if cover_id else "",
        "youtube_url": "",
        "ytmusic_url": "",
    }


def _subsonic_album_to_result(album: Dict[str, Any]) -> Dict[str, Any]:
    cover_id = str(album.get("coverArt") or "").strip()
    return {
        "kind": "album",
        "source": "subsonic",
        "subsonic_album_id": str(album.get("id") or ""),
        "browse_id": "",
        "title": str(album.get("title") or album.get("name") or ""),
        "artist": str(album.get("artist") or ""),
        "year": str(album.get("year") or ""),
        "thumbnail_url": f"/api/art/subsonic/{cover_id}?size=512" if cover_id else "",
        "ytmusic_url": "",
    }


def _dedupe_key_song(item: Dict[str, Any]) -> Tuple[str, str]:
    return (
        _norm_search_text(str(item.get("title") or "")),
        _norm_search_text(str(item.get("artist") or "")),
    )


def _dedupe_key_album(item: Dict[str, Any]) -> Tuple[str, str]:
    return (
        _norm_search_text(str(item.get("title") or "")),
        _norm_search_text(str(item.get("artist") or "")),
    )


async def _subsonic_blended_search(settings: Dict[str, Any], query: str) -> Dict[str, List[Dict[str, Any]]]:
    client = _subsonic_client_from_settings(settings)
    if client is None:
        return {"songs": [], "albums": []}

    try:
        raw = await client.search3(query)
        songs_raw = list(raw.get("song") or [])
        albums_raw = list(raw.get("album") or [])

        scored_songs: List[Tuple[float, Dict[str, Any]]] = []
        for song in songs_raw:
            score = _subsonic_song_score(
                query=query,
                title=str(song.get("title") or ""),
                artist=str(song.get("artist") or ""),
                album=str(song.get("album") or ""),
            )
            if score >= _SUBSONIC_STRONG_SONG_THRESHOLD:
                scored_songs.append((score, song))
        scored_songs.sort(key=lambda item: item[0], reverse=True)

        scored_albums: List[Tuple[float, Dict[str, Any]]] = []
        for album in albums_raw:
            score = _subsonic_album_score(
                query=query,
                title=str(album.get("title") or album.get("name") or ""),
                artist=str(album.get("artist") or ""),
            )
            if score >= _SUBSONIC_STRONG_ALBUM_THRESHOLD:
                scored_albums.append((score, album))
        scored_albums.sort(key=lambda item: item[0], reverse=True)

        return {
            "songs": [_subsonic_song_to_result(song) for _, song in scored_songs[:_SUBSONIC_MAX_SONGS]],
            "albums": [_subsonic_album_to_result(album) for _, album in scored_albums[:_SUBSONIC_MAX_ALBUMS]],
        }
    finally:
        await client.close()


def _client_ip(request: Request) -> str:
    try:
        return (request.client.host if request and request.client else "") or ""
    except Exception:
        return ""


def _artist_detail_cache_key(browse_id: str) -> str:
    return f"artist_detail|{browse_id}"


def _artist_resolution_cache_key(browse_id: str) -> str:
    return f"artist_resolution|{browse_id}"


def _resolved_like_payload(overview: Dict[str, Any], resolution: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    base = dict(overview or {})
    res = dict(resolution or {})
    if not res:
        res = {
            "mb_artist_id": "",
            "mb_resolution_status": "unresolved",
            "mb_resolution_confidence": 0.0,
            "canonical_name": "",
            "artist_type": "",
            "country": "",
            "disambiguation": "",
            "matched_albums": [],
        }
    base.update(res)
    base["source"] = "ytmusic"
    return base


def _yt_artist_overview_cached(browse_id: str) -> Dict[str, Any]:
    cache_key = _artist_detail_cache_key(browse_id)
    hit = _CACHE.get(cache_key)
    if hit is not None:
        return hit
    overview = get_artist_overview(browse_id)
    if overview:
        _CACHE.set(cache_key, overview, ttl_seconds=_DETAIL_TTL_S)
    return overview


def _artist_resolution_cached(browse_id: str) -> Optional[Dict[str, Any]]:
    return _CACHE.get(_artist_resolution_cache_key(browse_id))


async def _resolve_artist_and_cache(browse_id: str, overview: Dict[str, Any]) -> None:
    try:
        resolution = await resolve_artist_mbid_from_yt(
            artist_name=str(overview.get("name") or ""),
            album_titles=list(overview.get("top_albums_hint") or []),
            song_titles=list(overview.get("top_tracks_hint") or []),
        )
        if not isinstance(resolution, dict):
            resolution = {}
    except Exception:
        resolution = {
            "mb_artist_id": "",
            "mb_resolution_status": "failed",
            "mb_resolution_confidence": 0.0,
            "canonical_name": "",
            "artist_type": "",
            "country": "",
            "disambiguation": "",
            "matched_albums": [],
        }
    _CACHE.set(_artist_resolution_cache_key(browse_id), resolution, ttl_seconds=_RESOLUTION_TTL_S)


async def _ensure_artist_resolution_started(browse_id: str, overview: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    cached = _artist_resolution_cached(browse_id)
    if cached is not None:
        return cached

    bid = (browse_id or "").strip()
    if not bid or not overview:
        return None

    async with _RESOLUTION_LOCK:
        cached = _artist_resolution_cached(bid)
        if cached is not None:
            return cached
        if bid in _RESOLUTION_IN_FLIGHT:
            return {
                "mb_artist_id": "",
                "mb_resolution_status": "resolving",
                "mb_resolution_confidence": 0.0,
                "canonical_name": "",
                "artist_type": "",
                "country": "",
                "disambiguation": "",
                "matched_albums": [],
            }
        _RESOLUTION_IN_FLIGHT.add(bid)

    async def _runner() -> None:
        try:
            await _resolve_artist_and_cache(bid, overview)
        finally:
            async with _RESOLUTION_LOCK:
                _RESOLUTION_IN_FLIGHT.discard(bid)

    asyncio.create_task(_runner())
    return {
        "mb_artist_id": "",
        "mb_resolution_status": "resolving",
        "mb_resolution_confidence": 0.0,
        "canonical_name": "",
        "artist_type": "",
        "country": "",
        "disambiguation": "",
        "matched_albums": [],
    }


async def _artist_payload_nonblocking(browse_id: str) -> Dict[str, Any]:
    overview = _yt_artist_overview_cached(browse_id)
    if not overview:
        return {}
    resolution = _artist_resolution_cached(browse_id)
    if resolution is None:
        resolution = await _ensure_artist_resolution_started(browse_id, overview)
    return _resolved_like_payload(overview, resolution)


def _norm_artist_name(value: str) -> str:
    s = (value or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _pick_best_artist_hit(name: str, rows: list[dict[str, Any]]) -> Optional[Dict[str, Any]]:
    target = _norm_artist_name(name)
    best: Optional[Dict[str, Any]] = None
    best_score = 0.0
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        candidate_name = str(row.get("name") or row.get("artist_name") or row.get("artist") or "")
        candidate_norm = _norm_artist_name(candidate_name)
        if not candidate_norm:
            continue
        score = SequenceMatcher(None, target, candidate_norm).ratio()
        if candidate_norm == target:
            score += 1.0
        elif target and (candidate_norm.startswith(target) or target.startswith(candidate_norm)):
            score += 0.35
        elif target and (target in candidate_norm or candidate_norm in target):
            score += 0.2
        if score > best_score:
            best_score = score
            best = row
    if best is None:
        return None
    return best if best_score >= 0.55 else None


async def _enrich_similar_artist_row(row: Dict[str, Any]) -> Dict[str, Any]:
    name = str(row.get("artist_name") or row.get("name") or "").strip()
    mbid = str(row.get("artist_mbid") or row.get("mb_artist_id") or "").strip()
    score = int(row.get("score") or 0)
    payload = {
        "artist_name": name,
        "name": name,
        "artist_mbid": mbid,
        "mb_artist_id": mbid,
        "score": score,
        "yt_browse_id": "",
        "browse_id": "",
        "thumbnail_url": "",
    }
    if not name:
        return payload

    cache_key = f"similar_yt_artist|{_norm_artist_name(name)}"
    cached = _CACHE.get(cache_key)
    if cached is None:
        try:
            result = await asyncio.to_thread(search_artists, name, artist_limit=5)
            rows = list((result or {}).get("artists") or [])
            best = _pick_best_artist_hit(name, rows)
            cached = best or {}
        except Exception:
            cached = {}
        _CACHE.set(cache_key, cached, ttl_seconds=60 * 60 * 24 * 7)

    if isinstance(cached, dict) and cached:
        yt_name = str(cached.get("name") or name).strip()
        yt_browse_id = str(cached.get("browse_id") or cached.get("artist_id") or "").strip()
        thumbnail_url = str(cached.get("thumbnail_url") or "").strip()
        payload.update({
            "artist_name": yt_name or name,
            "name": yt_name or name,
            "yt_browse_id": yt_browse_id,
            "browse_id": yt_browse_id,
            "thumbnail_url": thumbnail_url,
        })
    return payload


async def _enrich_similar_artist_rows(rows: list[Dict[str, Any]], limit: int) -> list[Dict[str, Any]]:
    if not rows:
        return []
    sem = asyncio.Semaphore(6)

    async def _run(row: Dict[str, Any]) -> Dict[str, Any]:
        async with sem:
            return await _enrich_similar_artist_row(row)

    tasks = [_run(dict(row or {})) for row in list(rows)[: max(0, int(limit))]]
    return await asyncio.gather(*tasks)


@router.get("/find", response_model=Dict[str, Any])
def ytmusic_find(
    request: Request,
    kind: str = Query(..., description="'song' or 'album'"),
    title: str = Query(..., description="Song title (for kind=song) or album title (for kind=album)"),
    artist: str = Query(...),
    album: Optional[str] = Query(None, description="Album title (only for kind=song)"),
    duration_seconds: Optional[int] = Query(None, description="Song duration in seconds (optional)"),
    user: User = Depends(get_current_user),
):
    ip = _client_ip(request)
    if not RATE_LIMITER.allow(make_key(scope="ytmusic:find", user_id=user.id, ip=ip), limit=60, window_s=60):
        raise HTTPException(status_code=429, detail="Too many requests")

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

    _CACHE.set(cache_key, payload, ttl_seconds=60 * 10)
    return payload


@router.get("/search", response_model=Dict[str, Any])
async def ytmusic_search(
    request: Request,
    q: str = Query(..., description="Search query"),
    song_limit: int = Query(15, ge=1, le=50),
    album_limit: int = Query(15, ge=0, le=50),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Unified search: strong Subsonic matches first, then YouTube Music."""
    qq = (q or "").strip()
    if not qq:
        return {"songs": [], "albums": []}

    ip = _client_ip(request)
    if not RATE_LIMITER.allow(make_key(scope="ytmusic:search", user_id=user.id, ip=ip), limit=30, window_s=30):
        raise HTTPException(status_code=429, detail="Too many requests")

    settings = get_settings(db) or {}
    subsonic_enabled = bool((settings.get("subsonic_base_url") or "").strip() and (settings.get("subsonic_username") or "").strip() and (settings.get("subsonic_password") or "").strip())
    cache_key = f"search_blended|{qq}|{song_limit}|{album_limit}|sub={1 if subsonic_enabled else 0}"
    hit = _CACHE.get(cache_key)
    if hit is not None:
        return hit

    yt_payload = search_ytmusic(qq, song_limit=song_limit, album_limit=album_limit)
    sub_payload = await _subsonic_blended_search(settings, qq) if subsonic_enabled else {"songs": [], "albums": []}

    sub_song_keys = {_dedupe_key_song(item) for item in sub_payload.get("songs") or []}

    yt_songs: List[Dict[str, Any]] = []
    for item in yt_payload.get("songs") or []:
        key = _dedupe_key_song(item)
        if key in sub_song_keys:
            continue
        merged = dict(item)
        merged.setdefault("source", "ytmusic")
        merged.setdefault("subsonic_song_id", "")
        yt_songs.append(merged)

    yt_albums: List[Dict[str, Any]] = []
    for item in yt_payload.get("albums") or []:
        merged = dict(item)
        merged.setdefault("source", "ytmusic")
        merged.setdefault("subsonic_album_id", "")
        yt_albums.append(merged)

    payload = {
        "songs": list(sub_payload.get("songs") or []) + yt_songs,
        "albums": yt_albums,
    }
    _CACHE.set(cache_key, payload, ttl_seconds=60 * 2)
    return payload


@router.get("/search/artists", response_model=Dict[str, Any])
def ytmusic_search_artists(
    request: Request,
    q: str = Query(..., description="Search query"),
    artist_limit: int = Query(15, ge=1, le=50),
    user: User = Depends(get_current_user),
):
    qq = (q or "").strip()
    if not qq:
        return {"artists": []}

    ip = _client_ip(request)
    if not RATE_LIMITER.allow(make_key(scope="ytmusic:search:artists", user_id=user.id, ip=ip), limit=30, window_s=30):
        raise HTTPException(status_code=429, detail="Too many requests")

    cache_key = f"search_artists|{qq}|{artist_limit}"
    hit = _CACHE.get(cache_key)
    if hit is not None:
        return hit

    payload = search_artists(qq, artist_limit=artist_limit)
    _CACHE.set(cache_key, payload, ttl_seconds=60 * 2)
    return payload


@router.get("/artists/{browse_id}", response_model=Dict[str, Any])
async def ytmusic_artist_detail(
    request: Request,
    browse_id: str,
    user: User = Depends(get_current_user),
):
    ip = _client_ip(request)
    if not RATE_LIMITER.allow(make_key(scope="ytmusic:artist", user_id=user.id, ip=ip), limit=40, window_s=60):
        raise HTTPException(status_code=429, detail="Too many requests")

    payload = await _artist_payload_nonblocking(browse_id)
    if not payload:
        raise HTTPException(status_code=404, detail="Artist not found")
    return payload


@router.get("/artists/{browse_id}/popular", response_model=Dict[str, Any])
async def ytmusic_artist_popular(
    request: Request,
    browse_id: str,
    limit: int = Query(10, ge=1, le=50),
    user: User = Depends(get_current_user),
):
    ip = _client_ip(request)
    if not RATE_LIMITER.allow(make_key(scope="ytmusic:artist:popular", user_id=user.id, ip=ip), limit=40, window_s=60):
        raise HTTPException(status_code=429, detail="Too many requests")

    cache_key = f"artist_popular|{browse_id}|{limit}"
    hit = _CACHE.get(cache_key)
    if hit is not None:
        return hit

    detail = await _artist_payload_nonblocking(browse_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Artist not found")
    tracks = get_artist_popular_songs(browse_id, limit=limit)
    payload = {
        "artist_name": detail.get("name") or "",
        "yt_browse_id": browse_id,
        "mb_artist_id": detail.get("mb_artist_id") or "",
        "mb_resolution_status": detail.get("mb_resolution_status") or "unresolved",
        "tracks": tracks,
    }
    _CACHE.set(cache_key, payload, ttl_seconds=60 * 15)
    return payload


@router.get("/artists/{browse_id}/albums", response_model=Dict[str, Any])
async def ytmusic_artist_albums(
    request: Request,
    browse_id: str,
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(get_current_user),
):
    ip = _client_ip(request)
    if not RATE_LIMITER.allow(make_key(scope="ytmusic:artist:albums", user_id=user.id, ip=ip), limit=40, window_s=60):
        raise HTTPException(status_code=429, detail="Too many requests")

    cache_key = f"artist_albums|{browse_id}|{limit}"
    hit = _CACHE.get(cache_key)
    if hit is not None:
        return hit

    detail = await _artist_payload_nonblocking(browse_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Artist not found")
    albums = get_artist_albums(browse_id, limit=limit, category="albums")
    singles = get_artist_albums(browse_id, limit=limit, category="singles")
    payload = {
        "artist_name": detail.get("name") or "",
        "yt_browse_id": browse_id,
        "mb_artist_id": detail.get("mb_artist_id") or "",
        "mb_resolution_status": detail.get("mb_resolution_status") or "unresolved",
        "albums": albums,
        "singles": singles,
    }
    _CACHE.set(cache_key, payload, ttl_seconds=60 * 60)
    return payload


@router.get("/artists/{browse_id}/similar", response_model=Dict[str, Any])
async def ytmusic_artist_similar(
    request: Request,
    browse_id: str,
    limit: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
):
    ip = _client_ip(request)
    if not RATE_LIMITER.allow(make_key(scope="ytmusic:artist:similar", user_id=user.id, ip=ip), limit=20, window_s=60):
        raise HTTPException(status_code=429, detail="Too many requests")

    cache_key = f"artist_similar|{browse_id}|{limit}"
    hit = _CACHE.get(cache_key)
    if hit is not None:
        return hit

    overview = _yt_artist_overview_cached(browse_id)
    if not overview:
        raise HTTPException(status_code=404, detail="Artist not found")

    resolution = _artist_resolution_cached(browse_id)
    if resolution is None:
        resolution = await _ensure_artist_resolution_started(browse_id, overview)

    mbid = str((resolution or {}).get("mb_artist_id") or "")
    status = str((resolution or {}).get("mb_resolution_status") or "unresolved")
    if not mbid or status not in {"resolved", "ambiguous"}:
        payload = {
            "artist_name": overview.get("name") or "",
            "yt_browse_id": browse_id,
            "mb_artist_id": mbid,
            "mb_resolution_status": status,
            "similar_artists": [],
        }
        _CACHE.set(cache_key, payload, ttl_seconds=15 if status == "resolving" else 60)
        return payload

    rows = await lb_similar_artists_for_artist(mbid, limit=limit)
    enriched_rows = await _enrich_similar_artist_rows(rows, limit=limit)
    payload = {
        "artist_name": overview.get("name") or "",
        "yt_browse_id": browse_id,
        "mb_artist_id": mbid,
        "mb_resolution_status": status,
        "similar_artists": enriched_rows,
    }
    _CACHE.set(cache_key, payload, ttl_seconds=60 * 60 * 6)
    return payload


@router.get("/artists/{browse_id}/station-seeds", response_model=Dict[str, Any])
async def ytmusic_artist_station_seeds(
    request: Request,
    browse_id: str,
    limit: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
):
    ip = _client_ip(request)
    if not RATE_LIMITER.allow(make_key(scope="ytmusic:artist:station-seeds", user_id=user.id, ip=ip), limit=20, window_s=60):
        raise HTTPException(status_code=429, detail="Too many requests")

    cache_key = f"artist_station_seeds|{browse_id}|{limit}"
    hit = _CACHE.get(cache_key)
    if hit is not None:
        return hit

    overview = _yt_artist_overview_cached(browse_id)
    if not overview:
        raise HTTPException(status_code=404, detail="Artist not found")

    resolution = _artist_resolution_cached(browse_id)
    if resolution is None:
        resolution = await _ensure_artist_resolution_started(browse_id, overview)

    mbid = str((resolution or {}).get("mb_artist_id") or "")
    status = str((resolution or {}).get("mb_resolution_status") or "unresolved")
    if not mbid or status not in {"resolved", "ambiguous"}:
        payload = {
            "artist_name": overview.get("name") or "",
            "yt_browse_id": browse_id,
            "mb_artist_id": mbid,
            "mb_resolution_status": status,
            "tracks": [],
        }
        _CACHE.set(cache_key, payload, ttl_seconds=15 if status == "resolving" else 60)
        return payload

    tracks = await lb_top_recordings_for_artist(mbid, limit=limit)
    payload = {
        "artist_name": overview.get("name") or "",
        "yt_browse_id": browse_id,
        "mb_artist_id": mbid,
        "mb_resolution_status": status,
        "tracks": tracks,
    }
    _CACHE.set(cache_key, payload, ttl_seconds=60 * 60)
    return payload
