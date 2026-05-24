from __future__ import annotations

import asyncio
import math
import re
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query
from starlette.responses import RedirectResponse
from ..auth import get_current_user
from ..db import SessionLocal
from ..models import User
from ..settings_store import get_settings
from ..cache import TTLCache
from ..integrations.musicbrainz import MusicBrainzClient


router = APIRouter(prefix="/api", tags=["search"])


def _load_settings_short() -> Dict[str, Any]:
    db = SessionLocal()
    try:
        return dict(get_settings(db) or {})
    finally:
        db.close()


_SEARCH_CACHE: TTLCache[Dict[str, Any]] = TTLCache(max_items=4096)
_ARTIST_IMG_CACHE: TTLCache[Optional[str]] = TTLCache(max_items=10000)


def _img_proxy_url(remote_url: str) -> str:
    return f"/api/img?u={urllib.parse.quote(remote_url, safe='')}" if remote_url else ""


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _norm_text(s: str) -> str:
    """Normalize text for intent-oriented matching.

    We want punctuation-insensitive matching so users don't need to type
    apostrophes (e.g., "rifles" should match "rifle's").
    """
    s = (s or "").lower().strip()
    # Normalize common apostrophe characters.
    s = re.sub(r"[’'`´]", "", s)
    # Collapse any remaining non-alphanumerics to spaces.
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _query_variants(q: str) -> List[str]:
    """Generate a small set of query variants to improve recall.

    MusicBrainz search can be sensitive to punctuation/possessives. We keep
    the variant set small to avoid extra latency.
    """
    base = (q or "").strip()
    if not base:
        return []

    variants: List[str] = []

    def add(v: str):
        v = re.sub(r"\s+", " ", (v or "").strip())
        if v and v not in variants:
            variants.append(v)

    add(base)

    # Strip apostrophes entirely.
    add(re.sub(r"[’'`´]", "", base))

    # Replace all punctuation with spaces.
    add(re.sub(r"[^0-9A-Za-z]+", " ", base))

    # Heuristic: convert plural-looking tokens into possessives (rifles -> rifle's).
    toks = re.split(r"\s+", base)
    poss = []
    changed = False
    for t in toks:
        if re.fullmatch(r"[A-Za-z]{4,}s", t) and not t.lower().endswith("ss"):
            poss.append(t[:-1] + "'s")
            changed = True
        else:
            poss.append(t)
    if changed:
        add(" ".join(poss))

    return variants


def _match_score(text: str, q: str) -> float:
    t = _norm_text(text)
    qn = _norm_text(q)
    if not t or not qn:
        return 0.0
    if t == qn:
        return 100.0
    if t.startswith(qn):
        return 70.0
    if qn in t:
        return 45.0
    # token overlap
    tt = set(t.split())
    qq = set(qn.split())
    if not tt or not qq:
        return 0.0
    inter = len(tt & qq)
    if inter == 0:
        return 0.0
    return 10.0 * (inter / max(1, len(qq)))


def cover_url_release_group(rg_id: str, size: int = 250) -> str:
    return f"https://coverartarchive.org/release-group/{rg_id}/front-{int(size)}"


def cover_url_release(rel_id: str, size: int = 250) -> str:
    return f"https://coverartarchive.org/release/{rel_id}/front-{int(size)}"


def _proxy_url(settings: Dict[str, Any], remote_url: str) -> str:
    if not remote_url:
        return ""
    if settings.get("image_proxy_enabled", True):
        return "/api/img?u=" + urllib.parse.quote(remote_url, safe="")
    return remote_url


def _pick_representative_release(releases: List[Dict[str, Any]], settings: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not releases:
        return None

    def_country = str(settings.get("search_default_country", "US") or "US").upper()
    hide_non_official = settings.get("search_hide_non_official", True) is not False
    prefer_original = bool(settings.get("search_prefer_original_release", False))

    candidates = releases
    if hide_non_official:
        candidates = [r for r in candidates if (r.get("status") or "").lower() in ("official", "")]
        if not candidates:
            candidates = releases

    def parse_date(d: str) -> Tuple[int, int, int]:
        if not d:
            return (9999, 12, 31)
        parts = d.split("-")
        try:
            y = int(parts[0])
        except Exception:
            return (9999, 12, 31)
        m = int(parts[1]) if len(parts) > 1 else 12
        day = int(parts[2]) if len(parts) > 2 else 31
        return (y, m, day)

    # Determine common track-count (if provided) to prefer "normal" releases.
    counts: Dict[int, int] = {}
    for r in candidates:
        tc = r.get("track-count")
        if isinstance(tc, int) and tc > 0:
            counts[tc] = counts.get(tc, 0) + 1
    common_tc = max(counts.items(), key=lambda kv: kv[1])[0] if counts else None

    def score(r: Dict[str, Any]) -> float:
        s = 0.0
        country = (r.get("country") or "").upper()
        if country == def_country:
            s += 50.0
        date = r.get("date") or ""
        y, m, d = parse_date(date)
        # prefer earliest if prefer_original else earlier within country subset still matters
        if prefer_original:
            s += max(0.0, 30.0 - (y - 1900) * 0.1)
        else:
            s += max(0.0, 20.0 - (y - 1900) * 0.05)
        if (r.get("status") or "").lower() == "official":
            s += 10.0
        if common_tc is not None and r.get("track-count") == common_tc:
            s += 8.0
        if r.get("id"):
            s += 1.0
        return s

    return max(candidates, key=score)


def _canonical_track_key(title: str, artist: str) -> str:
    t = re.sub(r"\([^\)]*\)", "", title or "")
    t = re.sub(r"\[[^\]]*\]", "", t)
    t = re.sub(r"\s+", " ", t).strip().lower()
    a = re.sub(r"\s+", " ", (artist or "").strip().lower())
    return f"{t}::{a}"

# -----------------------------------------------------------------------------
# Helix search modes
# -----------------------------------------------------------------------------
# These endpoints separate the three frontend search intents:
# - hybrid: local Subsonic matches first, then YTMusic discovery results
# - subsonic: local library only
# - ytmusic: YTMusic discovery only
#
# The older /api/ytmusic/search route still exists for lower-level YTMusic
# workflows, but the browser frontend should use these clearer API routes.

from difflib import SequenceMatcher as _SequenceMatcher
from fastapi import Request as _Request

from ..integrations.ytmusic import search_ytmusic as _ytmusic_search
from ..integrations.subsonic import SubsonicClient as _SubsonicClient
from ..rate_limit import RATE_LIMITER as _RATE_LIMITER, make_key as _make_key


def _search_client_ip(request: _Request) -> str:
    try:
        return (request.client.host if request and request.client else "") or ""
    except Exception:
        return ""


def _search_subsonic_client(settings: Dict[str, Any]) -> Optional[_SubsonicClient]:
    base_url = str(settings.get("subsonic_base_url") or "").strip()
    username = str(settings.get("subsonic_username") or "").strip()
    password = str(settings.get("subsonic_password") or "").strip()
    if not base_url or not username or not password:
        return None
    return _SubsonicClient(
        base_url=base_url,
        username=username,
        password=password,
        client_name=str(settings.get("subsonic_client_name") or "Helix"),
        api_version=str(settings.get("subsonic_api_version") or "1.16.1"),
        timeout_s=int(settings.get("subsonic_timeout_s") or 20),
    )


def _search_norm(value: str) -> str:
    value = (value or "").strip().lower()
    value = value.replace("’", "'").replace("`", "'").replace("´", "'")
    value = value.replace("–", "-").replace("—", "-")
    value = value.replace("&", " and ")
    value = re.sub(r"\bfeat\.?\b", " ", value)
    value = re.sub(r"\bft\.?\b", " ", value)
    value = value.replace("'", "")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def _search_overlap(a: str, b: str) -> float:
    aa = set(_search_norm(a).split())
    bb = set(_search_norm(b).split())
    if not aa or not bb:
        return 0.0
    return len(aa & bb) / max(1, len(aa | bb))


def _search_ratio(a: str, b: str) -> float:
    aa = _search_norm(a)
    bb = _search_norm(b)
    if not aa or not bb:
        return 0.0
    if aa == bb:
        return 1.0
    return _SequenceMatcher(None, aa, bb).ratio()


def _search_song_score(query: str, title: str, artist: str, album: str = "") -> float:
    q = _search_norm(query)
    title_artist = f"{title} {artist}".strip()
    title_artist_album = f"{title} {artist} {album}".strip()
    ratios = [
        _search_ratio(q, title_artist),
        _search_ratio(q, title),
        _search_ratio(q, title_artist_album),
    ]
    overlap = max(
        _search_overlap(q, title_artist),
        _search_overlap(q, title),
        _search_overlap(q, title_artist_album),
    )
    contains_bonus = 0.08 if q and q in _search_norm(title_artist_album) else 0.0
    return max(ratios) * 0.82 + overlap * 0.18 + contains_bonus


def _search_album_score(query: str, title: str, artist: str) -> float:
    q = _search_norm(query)
    title_artist = f"{title} {artist}".strip()
    ratios = [_search_ratio(q, title_artist), _search_ratio(q, title)]
    overlap = max(_search_overlap(q, title_artist), _search_overlap(q, title))
    contains_bonus = 0.08 if q and q in _search_norm(title_artist) else 0.0
    return max(ratios) * 0.85 + overlap * 0.15 + contains_bonus


def _search_subsonic_song_to_result(song: Dict[str, Any]) -> Dict[str, Any]:
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


def _search_subsonic_album_to_result(album: Dict[str, Any]) -> Dict[str, Any]:
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


def _search_song_key(item: Dict[str, Any]) -> Tuple[str, str]:
    return (_search_norm(str(item.get("title") or "")), _search_norm(str(item.get("artist") or "")))


def _search_album_key(item: Dict[str, Any]) -> Tuple[str, str]:
    return (_search_norm(str(item.get("title") or "")), _search_norm(str(item.get("artist") or "")))


async def _search_subsonic_only(settings: Dict[str, Any], query: str, song_limit: int, album_limit: int) -> Dict[str, List[Dict[str, Any]]]:
    client = _search_subsonic_client(settings)
    if client is None:
        return {"songs": [], "albums": []}

    try:
        raw = await client.search3(query)
        songs_raw = list(raw.get("song") or [])
        albums_raw = list(raw.get("album") or [])

        scored_songs: List[Tuple[float, Dict[str, Any]]] = []
        for song in songs_raw:
            score = _search_song_score(
                query=query,
                title=str(song.get("title") or ""),
                artist=str(song.get("artist") or ""),
                album=str(song.get("album") or ""),
            )
            scored_songs.append((score, song))
        scored_songs.sort(key=lambda item: item[0], reverse=True)

        scored_albums: List[Tuple[float, Dict[str, Any]]] = []
        for album in albums_raw:
            score = _search_album_score(
                query=query,
                title=str(album.get("title") or album.get("name") or ""),
                artist=str(album.get("artist") or ""),
            )
            scored_albums.append((score, album))
        scored_albums.sort(key=lambda item: item[0], reverse=True)

        return {
            "songs": [_search_subsonic_song_to_result(song) for _, song in scored_songs[:song_limit]],
            "albums": [_search_subsonic_album_to_result(album) for _, album in scored_albums[:album_limit]],
        }
    finally:
        await client.close()


def _search_mark_ytmusic(payload: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    songs: List[Dict[str, Any]] = []
    for item in payload.get("songs") or []:
        row = dict(item or {})
        row.setdefault("source", "ytmusic")
        row.setdefault("subsonic_song_id", "")
        songs.append(row)

    albums: List[Dict[str, Any]] = []
    for item in payload.get("albums") or []:
        row = dict(item or {})
        row.setdefault("source", "ytmusic")
        row.setdefault("subsonic_album_id", "")
        albums.append(row)

    return {"songs": songs, "albums": albums}


@router.get("/search/hybrid", response_model=Dict[str, Any])
async def search_hybrid(
    request: _Request,
    q: str = Query(..., description="Search query"),
    song_limit: int = Query(20, ge=1, le=50),
    album_limit: int = Query(20, ge=0, le=50),
    subsonic_limit: int = Query(3, ge=0, le=10),
    user: User = Depends(get_current_user),
):
    """Default Helix search: top local Subsonic matches first, then YTMusic results."""
    qq = (q or "").strip()
    if not qq:
        return {"songs": [], "albums": []}

    ip = _search_client_ip(request)
    if not _RATE_LIMITER.allow(_make_key(scope="search:hybrid", user_id=user.id, ip=ip), limit=30, window_s=30):
        raise HTTPException(status_code=429, detail="Too many requests")

    settings = _load_settings_short()
    subsonic_enabled = bool(
        str(settings.get("subsonic_base_url") or "").strip()
        and str(settings.get("subsonic_username") or "").strip()
        and str(settings.get("subsonic_password") or "").strip()
    )
    cache_key = f"search:hybrid|{qq}|{song_limit}|{album_limit}|{subsonic_limit}|sub={1 if subsonic_enabled else 0}"
    hit = _SEARCH_CACHE.get(cache_key)
    if hit is not None:
        return hit

    yt_payload = _search_mark_ytmusic(_ytmusic_search(qq, song_limit=song_limit, album_limit=album_limit))
    sub_payload = await _search_subsonic_only(settings, qq, subsonic_limit, subsonic_limit) if subsonic_enabled and subsonic_limit > 0 else {"songs": [], "albums": []}

    sub_song_keys = {_search_song_key(item) for item in sub_payload.get("songs") or []}
    sub_album_keys = {_search_album_key(item) for item in sub_payload.get("albums") or []}

    yt_songs = [item for item in yt_payload.get("songs") or [] if _search_song_key(item) not in sub_song_keys]
    yt_albums = [item for item in yt_payload.get("albums") or [] if _search_album_key(item) not in sub_album_keys]

    payload = {
        "mode": "hybrid",
        "songs": list(sub_payload.get("songs") or []) + yt_songs,
        "albums": list(sub_payload.get("albums") or []) + yt_albums,
    }
    _SEARCH_CACHE.set(cache_key, payload, ttl_seconds=60 * 2)
    return payload


@router.get("/search/subsonic", response_model=Dict[str, Any])
async def search_subsonic(
    request: _Request,
    q: str = Query(..., description="Search query"),
    song_limit: int = Query(20, ge=1, le=100),
    album_limit: int = Query(20, ge=0, le=100),
    user: User = Depends(get_current_user),
):
    """Library-only search. This hits Subsonic and never calls YTMusic."""
    qq = (q or "").strip()
    if not qq:
        return {"songs": [], "albums": []}

    ip = _search_client_ip(request)
    if not _RATE_LIMITER.allow(_make_key(scope="search:subsonic", user_id=user.id, ip=ip), limit=60, window_s=60):
        raise HTTPException(status_code=429, detail="Too many requests")

    settings = _load_settings_short()
    cache_key = f"search:subsonic|{qq}|{song_limit}|{album_limit}"
    hit = _SEARCH_CACHE.get(cache_key)
    if hit is not None:
        return hit

    payload = await _search_subsonic_only(settings, qq, song_limit, album_limit)
    payload["mode"] = "subsonic"
    _SEARCH_CACHE.set(cache_key, payload, ttl_seconds=60 * 2)
    return payload


@router.get("/search/ytmusic", response_model=Dict[str, Any])
def search_ytmusic_only(
    request: _Request,
    q: str = Query(..., description="Search query"),
    song_limit: int = Query(20, ge=1, le=50),
    album_limit: int = Query(20, ge=0, le=50),
    user: User = Depends(get_current_user),
):
    """Discovery-only search. This calls YTMusic and does not prepend Subsonic results."""
    qq = (q or "").strip()
    if not qq:
        return {"songs": [], "albums": []}

    ip = _search_client_ip(request)
    if not _RATE_LIMITER.allow(_make_key(scope="search:ytmusic", user_id=user.id, ip=ip), limit=30, window_s=30):
        raise HTTPException(status_code=429, detail="Too many requests")

    cache_key = f"search:ytmusic|{qq}|{song_limit}|{album_limit}"
    hit = _SEARCH_CACHE.get(cache_key)
    if hit is not None:
        return hit

    payload = _search_mark_ytmusic(_ytmusic_search(qq, song_limit=song_limit, album_limit=album_limit))
    payload["mode"] = "ytmusic"
    _SEARCH_CACHE.set(cache_key, payload, ttl_seconds=60 * 2)
    return payload
