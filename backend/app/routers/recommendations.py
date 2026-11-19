from __future__ import annotations

import asyncio
import os
import random
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..integrations.ytmusic import (
    find_artist_by_name,
    find_song,
    get_artist_popular_songs,
    get_artist_related_artists,
    get_song_radio,
)
from ..security import secrets_compare

router = APIRouter(prefix="/api/recommendations", tags=["recommendations"])


class SongResult(BaseModel):
    title: str
    artist: str
    album: str = ""


class SimilarResponse(BaseModel):
    songs: list[SongResult]


def _clean(value: str) -> str:
    return " ".join((value or "").strip().split())


def _norm(value: str) -> str:
    v = _clean(value).casefold()
    v = v.replace("’", "'").replace("‘", "'").replace("–", "-").replace("—", "-")
    return " ".join(v.split())


def _pair(title: str, artist: str) -> str:
    return f"{_norm(title)}|{_norm(artist)}"


def _require_api_token(request: Request) -> None:
    expected = (os.getenv("HELIX_RECOMMENDATIONS_TOKEN") or "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="Recommendations endpoint not configured")
    auth = request.headers.get("Authorization") or ""
    if auth.startswith("Bearer "):
        token = auth[7:]
    else:
        token = auth
    token = token.strip()
    if not secrets_compare(token, expected):
        raise HTTPException(status_code=401, detail="Invalid token")


def _artist_tracks(name: str, limit: int) -> list[dict[str, Any]]:
    if not name:
        return []
    try:
        artist = find_artist_by_name(name, artist_limit=8) or {}
    except Exception:
        return []
    browse_id = _clean(str(artist.get("browse_id") or artist.get("artist_id") or ""))
    if not browse_id:
        return []

    songs: list[dict[str, Any]] = []
    try:
        songs = get_artist_popular_songs(browse_id, limit=limit) or []
    except Exception:
        songs = []

    # If still few, pull related artists and sample
    if len(songs) < limit:
        try:
            related = get_artist_related_artists(browse_id, limit=20) or []
        except Exception:
            related = []
        for r in related[:10]:
            rname = _clean(str(r.get("name") or r.get("artist") or ""))
            if not rname:
                continue
            try:
                ra = find_artist_by_name(rname, artist_limit=1) or {}
                rb = _clean(str(ra.get("browse_id") or ra.get("artist_id") or ""))
                if not rb:
                    continue
                rs = get_artist_popular_songs(rb, limit=max(1, min(5, limit))) or []
                songs.extend(rs)
            except Exception:
                continue
            if len(songs) >= limit * 2:
                break

    return songs


def _track_radio(title: str, artist: str, album: str | None, limit: int) -> list[dict[str, Any]]:
    if not title or not artist:
        return []
    try:
        m = find_song(title=title, artist=artist, album=album or None)
    except Exception:
        m = None
    vid = _clean(str(getattr(m, "video_id", "") or "")) if m and getattr(m, "found", False) else ""
    if not vid:
        return []
    try:
        return get_song_radio(vid, limit=limit) or []
    except Exception:
        return []


def _album_tracks(title: str, artist: str, limit: int) -> list[dict[str, Any]]:
    # Simple heuristic: for similar songs by album, fall back to artist radio/popular songs
    # as a reasonable approximation without adding heavy album lookup.
    return _artist_tracks(artist, limit)


@router.get("/similar", response_model=SimilarResponse)
async def similar_songs(
    request: Request,
    type: str = "track",
    title: str = "",
    artist: str = "",
    album: str = "",
    limit: int = 20,
):
    _require_api_token(request)

    rec_type = (type or "track").lower()
    limit = max(1, min(100, int(limit or 20)))

    rows: list[dict[str, Any]] = []
    if rec_type == "track":
        rows = _track_radio(title, artist, album or None, limit=limit)
    elif rec_type == "album":
        rows = _album_tracks(album or title, artist, limit=limit)
    elif rec_type == "artist":
        rows = _artist_tracks(artist or title, limit=limit)
    else:
        raise HTTPException(status_code=400, detail="type must be one of track, album, artist")

    # Deduplicate by normalized pair
    seen: set[str] = set()
    songs: list[SongResult] = []
    for r in rows:
        rtitle = _clean(str(r.get("title") or r.get("track_name") or r.get("name") or ""))
        rartist = _clean(str(r.get("artist") or r.get("artist_name") or ""))
        if not rtitle or not rartist:
            continue
        key = _pair(rtitle, rartist)
        if key in seen:
            continue
        seen.add(key)
        ral = _clean(str(r.get("album") or ""))
        songs.append(SongResult(title=rtitle, artist=rartist, album=ral))
        if len(songs) >= limit:
            break

    # If no results, try artist fallback
    if not songs and rec_type != "artist" and artist:
        rows = _artist_tracks(artist, limit=limit)
        for r in rows:
            rtitle = _clean(str(r.get("title") or r.get("track_name") or r.get("name") or ""))
            rartist = _clean(str(r.get("artist") or r.get("artist_name") or ""))
            if not rtitle or not rartist:
                continue
            key = _pair(rtitle, rartist)
            if key in seen:
                continue
            seen.add(key)
            ral = _clean(str(r.get("album") or ""))
            songs.append(SongResult(title=rtitle, artist=rartist, album=ral))
            if len(songs) >= limit:
                break

    return SimilarResponse(songs=songs)
