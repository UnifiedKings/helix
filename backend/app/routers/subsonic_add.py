from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Set

import re

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..db import get_db
from ..models import User
from ..settings_store import get_settings
from ..download_manager import DOWNLOAD_MANAGER, DownloadJob
from ..integrations import ytmusic as ytmusic_integration
from ..integrations.subsonic import SubsonicClient
from ..rate_limit import RATE_LIMITER, make_key

# Optional: cache invalidation helpers if your subsonic resolver uses them.
try:
    from .subsonic import invalidate_song_cache, invalidate_album_cache  # type: ignore
except Exception:
    def invalidate_song_cache(_: str) -> None:
        return

    def invalidate_album_cache(_: str) -> None:
        return


router = APIRouter(prefix="/api/subsonic/add", tags=["subsonic"])


def _norm_text(s: str) -> str:
    s = (s or "").strip().lower()
    s = s.replace("’", "'").replace("`", "'").replace("´", "'")
    s = s.replace("–", "-").replace("—", "-")
    s = s.replace("'", "")
    s = re.sub(r"[^0-9a-z\s]+", " ", s)
    return " ".join(s.split())


def _duration_close_ms(a: int, b: int, tolerance_ms: int = 3000) -> bool:
    if not a or not b:
        return False
    return abs(int(a) - int(b)) <= int(tolerance_ms)


def _build_existing_album_track_keys(songs: List[Dict[str, Any]]) -> tuple[Set[str], Set[tuple[str, int]]]:
    title_keys: Set[str] = set()
    timed_keys: Set[tuple[str, int]] = set()
    for s in songs or []:
        title = _norm_text(str(s.get("title") or ""))
        if not title:
            continue
        title_keys.add(title)
        try:
            duration_s = int(s.get("duration") or 0)
        except Exception:
            duration_s = 0
        if duration_s > 0:
            timed_keys.add((title, duration_s * 1000))
    return title_keys, timed_keys


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


@router.post("/track")
async def add_track(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Enqueue a single YTMusic track for download/import, to end up in the Subsonic library.
    Body: { "yt_video_id": "...", "title": "...", "artist": "...", "album": "...", "art_url": "..." }
    """
    ip = getattr(getattr(request, "client", None), "host", "") or ""
    if not RATE_LIMITER.allow(make_key(scope="subsonic_add_track", user_id=str(user.id), ip=ip), limit=20, window_s=10):
        raise HTTPException(status_code=429, detail="Too many requests")

    body = await request.json()
    print(body)
    vid = (body.get("yt_video_id") or body.get("video_id") or "").strip()
    title = (body.get("title") or "").strip()
    artist = (body.get("artist") or "").strip()
    album = (body.get("album") or "").strip()
    art_url = (body.get("art_url") or "").strip()

    if not vid or not title or not artist:
        raise HTTPException(status_code=400, detail="yt_video_id, title, and artist are required")

    job = DownloadJob(
        video_id=vid,
        url=f"https://music.youtube.com/watch?v={vid}",
        title=title,
        artist=artist,
        album=album,
        art_url=art_url,
        track_no=0,
        duration_ms=0,
        priority=30,  # behind "play now", ahead of deep background
    )
    await DOWNLOAD_MANAGER.enqueue_normal(job)

    # If a prior resolve cached "not available", clear it so badge can appear quickly.
    invalidate_song_cache(f"song:{vid}")

    return {"ok": True, "video_id": vid}


@router.post("/album")
async def add_album(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Enqueue only the missing tracks from a YTMusic album for download/import.

    For album-add, we intentionally treat album membership as the source of truth:
      - Resolve the target album in Subsonic using album title + album artist
      - Fetch that album's current song list once
      - Compare YTMusic tracks against that album track list by normalized title,
        using duration as an additional confirmation when available

    This avoids false positives from per-track global search3 lookups, which can
    incorrectly mark tracks as "already present" if the artist has another song
    with the same title elsewhere in the library.
    """
    ip = getattr(getattr(request, "client", None), "host", "") or ""
    if not RATE_LIMITER.allow(make_key(scope="subsonic_add_album", user_id=str(user.id), ip=ip), limit=8, window_s=10):
        raise HTTPException(status_code=429, detail="Too many requests")

    body = await request.json()
    browse_id = (body.get("browse_id") or "").strip()
    if not browse_id:
        raise HTTPException(status_code=400, detail="browse_id is required")

    album = ytmusic_integration.get_album_full(browse_id)
    tracks: List[Dict[str, Any]] = album.get("tracks") or []
    if not tracks:
        return {"ok": True, "total": 0, "enqueued": 0, "skipped_existing": 0}

    album_title = (body.get("title") or album.get("title") or "").strip()
    album_artist = (body.get("artist") or album.get("artist") or "").strip()
    art_url = (body.get("art_url") or album.get("thumbnail_url") or album.get("thumbnail") or "").strip()

    settings = get_settings(db)
    client = _subsonic_client_from_settings(settings)

    existing_title_keys: Set[str] = set()
    existing_timed_keys: Set[tuple[str, int]] = set()

    try:
        if client is not None and album_title and album_artist:
            yt_title_keys: Set[str] = set()
            yt_timed_keys: Set[tuple[str, int]] = set()
            for track in tracks:
                yt_title = _norm_text(str(track.get("title") or ""))
                if not yt_title:
                    continue
                yt_title_keys.add(yt_title)
                try:
                    yt_duration_ms = int(track.get("duration_ms") or track.get("lengthMs") or 0)
                except Exception:
                    yt_duration_ms = 0
                if yt_duration_ms > 0:
                    yt_timed_keys.add((yt_title, yt_duration_ms))

            best_overlap = -1
            best_score = -1.0
            for sub_album in await client.search_album_candidates(album=album_title, artist=album_artist, limit=8):
                album_id = str(sub_album.get("id") or "").strip()
                if not album_id:
                    continue
                album_songs = await client.get_album_songs(album_id)
                cand_title_keys, cand_timed_keys = _build_existing_album_track_keys(album_songs)
                if not cand_title_keys:
                    continue

                overlap = len(yt_title_keys & cand_title_keys)
                if overlap <= 0:
                    continue

                duration_matches = 0
                if yt_timed_keys and cand_timed_keys:
                    for title_key, yt_ms in yt_timed_keys:
                        if any(k == title_key and _duration_close_ms(yt_ms, cand_ms) for k, cand_ms in cand_timed_keys):
                            duration_matches += 1

                score = float(overlap) * 100.0 + float(duration_matches) * 25.0
                # Prefer candidates whose apparent track count is close to the YT album track count.
                score -= abs(len(cand_title_keys) - len(yt_title_keys)) * 5.0

                if score > best_score:
                    best_score = score
                    best_overlap = overlap
                    existing_title_keys, existing_timed_keys = cand_title_keys, cand_timed_keys

            # Do not trust a weak candidate. This avoids skipping tracks from the wrong album.
            if best_overlap <= 0:
                existing_title_keys = set()
                existing_timed_keys = set()
    except Exception:
        # Conservative behavior: if the album lookup fails, assume nothing exists
        # so the user still gets the requested tracks downloaded.
        existing_title_keys = set()
        existing_timed_keys = set()

    enqueued = 0
    skipped = 0

    for t in tracks:
        vid = (t.get("videoId") or t.get("video_id") or "").strip()
        title = (t.get("title") or "").strip()
        alb = (t.get("album") or album_title or album.get("title") or "").strip()
        duration_ms = int(t.get("duration_ms") or t.get("lengthMs") or 0)
        title_key = _norm_text(title)

        if not vid or not title or not album_artist:
            continue

        exists = False
        if title_key and title_key in existing_title_keys:
            exists = True
            if duration_ms and existing_timed_keys:
                exists = any(k == title_key and _duration_close_ms(duration_ms, dms) for k, dms in existing_timed_keys)

        if exists:
            skipped += 1
            continue

        job = DownloadJob(
            video_id=vid,
            url=f"https://music.youtube.com/watch?v={vid}",
            title=title,
            artist=album_artist,
            album=alb,
            album_artist=album_artist,
            browse_id=browse_id,
            art_url=(t.get("art_url") or art_url or "").strip(),
            track_no=int(t.get("track_no") or t.get("pos") or 0),
            duration_ms=duration_ms,
            priority=40,
        )
        await DOWNLOAD_MANAGER.enqueue_normal(job)
        invalidate_song_cache(f"song:{vid}")
        enqueued += 1

    invalidate_album_cache(f"album:{browse_id}")

    if client is not None:
        await client.close()

    return {"ok": True, "total": len(tracks), "enqueued": enqueued, "skipped_existing": skipped}
