from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Set

import re

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..db import SessionLocal
from ..models import User
from ..settings_store import get_settings
from ..download_manager import DOWNLOAD_MANAGER, DownloadJob
from ..integrations import ytmusic as ytmusic_integration
from ..integrations.subsonic import SubsonicClient
from ..rate_limit import RATE_LIMITER, make_key
from ..subsonic_permissions import can_import_to_subsonic

# Optional: cache invalidation helpers if your subsonic resolver uses them.
try:
    from .subsonic import invalidate_song_cache, invalidate_album_cache  # type: ignore
except Exception:
    def invalidate_song_cache(_: str) -> None:
        return

    def invalidate_album_cache(_: str) -> None:
        return


router = APIRouter(prefix="/api/subsonic/add", tags=["subsonic"])


def _load_settings_short() -> Dict[str, Any]:
    db = SessionLocal()
    try:
        return dict(get_settings(db) or {})
    finally:
        db.close()


def _require_import_permission(user: User) -> None:
    db = SessionLocal()
    try:
        allowed = can_import_to_subsonic(db, user)
    finally:
        db.close()
    if not allowed:
        raise HTTPException(
            status_code=403,
            detail="Your account is not allowed to import tracks into the Subsonic library",
        )


def _skip_existing_album_tracks_enabled() -> bool:
    return os.getenv("HELIX_SUBSONIC_ADD_SKIP_EXISTING_ALBUM_TRACKS", "false").strip().lower() in {"1", "true", "yes", "on"}



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


def _track_duration_ms(track: Dict[str, Any]) -> int:
    for key in ("duration_ms", "lengthMs"):
        try:
            value = int(track.get(key) or 0)
        except Exception:
            value = 0
        if value > 0:
            return value
    try:
        seconds = int(track.get("duration_seconds") or 0)
    except Exception:
        seconds = 0
    return seconds * 1000 if seconds > 0 else 0


def _track_number(track: Dict[str, Any], fallback: int) -> int:
    for key in ("track_no", "trackNumber", "track_number", "pos", "position"):
        try:
            value = int(track.get(key) or 0)
        except Exception:
            value = 0
        if value > 0:
            return value
    return int(fallback or 0)


def _resolve_album_track_video_id(track: Dict[str, Any], *, album_title: str, album_artist: str) -> str:
    vid = str(track.get("videoId") or track.get("video_id") or "").strip()
    if vid:
        return vid

    title = str(track.get("title") or "").strip()
    artist = str(track.get("artist") or album_artist or "").strip()
    if not title or not artist:
        return ""

    try:
        found = ytmusic_integration.find_track(
            title=title,
            artist=artist,
            album=album_title,
            duration_seconds=int((_track_duration_ms(track) or 0) / 1000) or None,
        )
    except Exception:
        found = None

    return str(getattr(found, "video_id", "") or "").strip() if getattr(found, "found", False) else ""


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


@router.post("/track", response_model=Dict[str, Any])
async def add_track(
    request: Request,
    user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Enqueue a single YTMusic track for download/import, to end up in the Subsonic library.
    Body: { "yt_video_id": "...", "title": "...", "artist": "...", "album": "...", "art_url": "..." }
    """
    _require_import_permission(user)
    ip = getattr(getattr(request, "client", None), "host", "") or ""
    if not RATE_LIMITER.allow(make_key(scope="subsonic_add_track", user_id=str(user.id), ip=ip), limit=20, window_s=10):
        raise HTTPException(status_code=429, detail="Too many requests")

    body = await request.json()
    vid = (body.get("yt_video_id") or body.get("video_id") or "").strip()
    title = (body.get("title") or "").strip()
    artist = (body.get("artist") or "").strip()
    album = (body.get("album") or "").strip()
    art_url = (body.get("art_url") or "").strip()

    if not vid or not title or not artist:
        raise HTTPException(status_code=400, detail="yt_video_id, title, and artist are required")

    settings = _load_settings_short()
    if _subsonic_client_from_settings(settings) is None:
        raise HTTPException(status_code=409, detail="Subsonic is not configured. Add-to-library is disabled.")

    job = DownloadJob(
        video_id=vid,
        url=f"https://music.youtube.com/watch?v={vid}",
        title=title,
        artist=artist,
        album=album,
        art_url=art_url,
        track_no=0,
        duration_ms=0,
        persist_to_subsonic=True,
        priority=30,  # behind "play now", ahead of deep background
    )
    await DOWNLOAD_MANAGER.enqueue_normal(job)

    # If a prior resolve cached "not available", clear it so badge can appear quickly.
    invalidate_song_cache(f"song:{vid}")

    return {"ok": True, "video_id": vid}


@router.post("/album", response_model=Dict[str, Any])
async def add_album(
    request: Request,
    user: User = Depends(get_current_user),
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
    _require_import_permission(user)
    ip = getattr(getattr(request, "client", None), "host", "") or ""
    if not RATE_LIMITER.allow(make_key(scope="subsonic_add_album", user_id=str(user.id), ip=ip), limit=8, window_s=10):
        raise HTTPException(status_code=429, detail="Too many requests")

    body = await request.json()
    browse_id = (body.get("browse_id") or "").strip()
    if not browse_id:
        raise HTTPException(status_code=400, detail="browse_id is required")

    album = await asyncio.to_thread(ytmusic_integration.get_album_full, browse_id)
    tracks: List[Dict[str, Any]] = album.get("tracks") or []
    if not tracks:
        return {"ok": True, "total": 0, "enqueued": 0, "skipped_existing": 0}

    album_title = (body.get("title") or album.get("title") or "").strip()
    album_artist = (body.get("artist") or album.get("artist") or "").strip()
    art_url = (body.get("art_url") or album.get("thumbnail_url") or album.get("thumbnail") or "").strip()

    settings = _load_settings_short()
    client = _subsonic_client_from_settings(settings)
    if client is None:
        raise HTTPException(status_code=409, detail="Subsonic is not configured. Add-to-library is disabled.")

    existing_title_keys: Set[str] = set()
    existing_timed_keys: Set[tuple[str, int]] = set()

    try:
        if _skip_existing_album_tracks_enabled() and client is not None and album_title and album_artist:
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

            # Do not trust a weak candidate. A single common title can make
            # the wrong Subsonic album look "existing" and cause most of a new
            # album import to be skipped. Only skip existing tracks when the
            # matched album overlaps strongly with the YTMusic tracklist.
            required_overlap = max(2, int(len(yt_title_keys) * 0.65 + 0.999))
            if best_overlap < required_overlap:
                existing_title_keys = set()
                existing_timed_keys = set()
    except Exception:
        # Conservative behavior: if the album lookup fails, assume nothing exists
        # so the user still gets the requested tracks downloaded.
        existing_title_keys = set()
        existing_timed_keys = set()

    enqueued = 0
    skipped = 0

    unresolved = 0
    unresolved_tracks: List[str] = []
    for index, t in enumerate(tracks, start=1):
        title = (t.get("title") or "").strip()
        track_artist = (t.get("artist") or album_artist or "").strip()
        alb = (t.get("album") or album_title or album.get("title") or "").strip()
        duration_ms = _track_duration_ms(t)
        track_no = _track_number(t, index)
        title_key = _norm_text(title)

        if not title or not album_artist:
            continue

        exists = False
        if title_key and title_key in existing_title_keys:
            exists = True
            if duration_ms and existing_timed_keys:
                exists = any(k == title_key and _duration_close_ms(duration_ms, dms) for k, dms in existing_timed_keys)

        if exists:
            skipped += 1
            continue

        vid = _resolve_album_track_video_id(t, album_title=alb or album_title, album_artist=album_artist)
        if not vid:
            unresolved += 1
            unresolved_tracks.append(title)
            continue

        job = DownloadJob(
            video_id=vid,
            url=f"https://music.youtube.com/watch?v={vid}",
            title=title,
            artist=track_artist or album_artist,
            album=alb,
            album_artist=album_artist,
            browse_id=browse_id,
            art_url=(t.get("art_url") or art_url or "").strip(),
            track_no=track_no,
            duration_ms=duration_ms,
            persist_to_subsonic=True,
            priority=40,
        )
        await DOWNLOAD_MANAGER.enqueue_normal(job)
        invalidate_song_cache(f"song:{vid}")
        enqueued += 1

    invalidate_album_cache(f"album:{browse_id}")

    if client is not None:
        await client.close()

    return {
        "ok": True,
        "total": len(tracks),
        "enqueued": enqueued,
        "skipped_existing": skipped,
        "unresolved": unresolved,
        "unresolved_tracks": unresolved_tracks,
        "skip_existing_enabled": _skip_existing_album_tracks_enabled(),
    }
