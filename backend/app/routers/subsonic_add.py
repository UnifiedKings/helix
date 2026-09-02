from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, List, Optional, Set

import re

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from ..auth import get_current_user
from ..db import SessionLocal
from ..models import User, Playlist, PlaylistTrack, LikedTrack
from ..settings_store import get_settings
from ..download_manager import DOWNLOAD_MANAGER, DownloadJob
from ..integrations import ytmusic as ytmusic_integration
from ..integrations.subsonic import SubsonicClient
from ..rate_limit import RATE_LIMITER, make_key
from ..subsonic_permissions import can_import_to_subsonic
from ..quality_upgrade_service import create_upgrade_job

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
        raise HTTPException(status_code=403, detail="Your account is not allowed to import tracks into the Subsonic library")


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


def _track_artist(track: Dict[str, Any], fallback: str = "") -> str:
    artist = str(track.get("artist") or "").strip()
    if artist:
        return artist
    artists = track.get("artists") or []
    if isinstance(artists, list):
        for item in artists:
            if isinstance(item, dict):
                value = str(item.get("name") or "").strip()
            else:
                value = str(item or "").strip()
            if value:
                return value
    return (fallback or "").strip()


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


def _candidate_song_matches(
    candidate: Dict[str, Any],
    *,
    video_id: str,
    title: str,
    artist: str,
    album: str,
    duration_ms: int,
) -> bool:
    candidate_vid = str(
        candidate.get("video_id")
        or candidate.get("videoId")
        or candidate.get("yt_video_id")
        or ""
    ).strip()
    if video_id and candidate_vid and candidate_vid == video_id:
        return True

    if _norm_text(str(candidate.get("title") or "")) != _norm_text(title):
        return False

    candidate_artist = _norm_text(_track_artist(candidate))
    wanted_artist = _norm_text(artist)
    if candidate_artist and wanted_artist and candidate_artist != wanted_artist:
        return False

    candidate_album = _norm_text(str(candidate.get("album") or ""))
    wanted_album = _norm_text(album)
    if candidate_album and wanted_album and candidate_album != wanted_album:
        return False

    candidate_duration = _track_duration_ms(candidate)
    if duration_ms and candidate_duration and not _duration_close_ms(duration_ms, candidate_duration, 4000):
        return False

    return True


async def _resolve_import_track_metadata(
    *,
    video_id: str,
    title: str,
    artist: str,
    album: str = "",
    album_artist: str = "",
    browse_id: str = "",
    art_url: str = "",
    duration_ms: int = 0,
    track_no: int = 0,
) -> Dict[str, Any]:
    """Resolve canonical album context for an individual YTMusic track import.

    Album imports already know their order. Single-track and playlist imports
    historically used track_no=0, which meant TRACKNUMBER was left unset and
    Navidrome displayed #0. Resolve the track back to its YTMusic album and use
    that album's ordered track list before creating the DownloadJob.
    """

    resolved: Dict[str, Any] = {
        "video_id": (video_id or "").strip(),
        "title": (title or "").strip(),
        "artist": (artist or "").strip(),
        "album": (album or "").strip(),
        "album_artist": (album_artist or "").strip(),
        "browse_id": (browse_id or "").strip(),
        "art_url": (art_url or "").strip(),
        "duration_ms": int(duration_ms or 0),
        "track_no": int(track_no or 0),
    }

    # Search results already expose album_browse_id, but not every caller
    # currently forwards it. When it is missing, recover album context by
    # finding the exact YTMusic song (video id is the strongest identity key).
    if not resolved["browse_id"]:
        try:
            result = await asyncio.to_thread(
                ytmusic_integration.search_ytmusic,
                " ".join(part for part in (artist, title) if part).strip(),
                song_limit=10,
                album_limit=0,
            )
        except Exception:
            result = {}

        songs = result.get("songs") or []
        chosen: Optional[Dict[str, Any]] = None
        for candidate in songs:
            if not isinstance(candidate, dict):
                continue
            candidate_vid = str(
                candidate.get("video_id")
                or candidate.get("videoId")
                or candidate.get("yt_video_id")
                or ""
            ).strip()
            if resolved["video_id"] and candidate_vid == resolved["video_id"]:
                chosen = candidate
                break

        if chosen is None:
            for candidate in songs:
                if isinstance(candidate, dict) and _candidate_song_matches(
                    candidate,
                    video_id=resolved["video_id"],
                    title=resolved["title"],
                    artist=resolved["artist"],
                    album=resolved["album"],
                    duration_ms=resolved["duration_ms"],
                ):
                    chosen = candidate
                    break

        if chosen:
            resolved["browse_id"] = str(
                chosen.get("album_browse_id")
                or chosen.get("yt_browse_id")
                or chosen.get("browse_id")
                or ""
            ).strip()
            if not resolved["album"]:
                resolved["album"] = str(chosen.get("album") or "").strip()
            if not resolved["duration_ms"]:
                resolved["duration_ms"] = _track_duration_ms(chosen)
            if not resolved["art_url"]:
                resolved["art_url"] = str(
                    chosen.get("art_url")
                    or chosen.get("thumbnail_url")
                    or ""
                ).strip()

    if not resolved["browse_id"]:
        # Do not invent a track number when album context could not be proven.
        return resolved

    try:
        full = await asyncio.to_thread(
            ytmusic_integration.get_album_full,
            resolved["browse_id"],
        )
    except Exception:
        full = {}

    if not isinstance(full, dict) or not full:
        return resolved

    album_title = str(full.get("title") or "").strip()
    album_artist_value = str(full.get("artist") or "").strip()
    album_art = str(
        full.get("thumbnail_url")
        or full.get("thumbnail")
        or ""
    ).strip()

    if album_title:
        resolved["album"] = album_title
    if album_artist_value:
        resolved["album_artist"] = album_artist_value
    elif not resolved["album_artist"]:
        resolved["album_artist"] = resolved["artist"]
    if album_art:
        resolved["art_url"] = album_art

    tracks = full.get("tracks") or []
    exact: Optional[tuple[int, Dict[str, Any]]] = None
    fallback: Optional[tuple[int, Dict[str, Any]]] = None

    for index, row in enumerate(tracks, start=1):
        if not isinstance(row, dict):
            continue
        row_vid = str(row.get("videoId") or row.get("video_id") or "").strip()
        if resolved["video_id"] and row_vid and row_vid == resolved["video_id"]:
            exact = (index, row)
            break
        if fallback is None and _candidate_song_matches(
            row,
            video_id=resolved["video_id"],
            title=resolved["title"],
            artist=resolved["artist"],
            album="",
            duration_ms=resolved["duration_ms"],
        ):
            fallback = (index, row)

    matched = exact or fallback
    if not matched:
        return resolved

    index, row = matched
    real_track_no = _track_number(row, index)
    if real_track_no > 0:
        resolved["track_no"] = real_track_no

    row_artist = _track_artist(row, resolved["artist"])
    if row_artist:
        resolved["artist"] = row_artist
    if not resolved["duration_ms"]:
        resolved["duration_ms"] = _track_duration_ms(row)

    return resolved


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


def _playlist_rows_for_user(user_id: str, playlist_id: str) -> tuple[Playlist, List[Any]]:
    """Load one Helix playlist and its tracks using a short-lived DB session."""
    db = SessionLocal()
    try:
        pid = (playlist_id or "").strip()
        if pid == "liked":
            playlist = db.execute(
                select(Playlist).where(
                    Playlist.user_id == user_id,
                    Playlist.system_key == "liked",
                )
            ).scalar_one_or_none()
        else:
            playlist = db.execute(
                select(Playlist).where(
                    Playlist.id == pid,
                    Playlist.user_id == user_id,
                )
            ).scalar_one_or_none()

        if playlist is None:
            raise HTTPException(status_code=404, detail="Playlist not found")

        if (playlist.system_key or "") == "liked":
            rows = db.execute(
                select(LikedTrack)
                .where(LikedTrack.user_id == user_id)
                .order_by(LikedTrack.created_at.desc())
            ).scalars().all()
        else:
            rows = db.execute(
                select(PlaylistTrack)
                .where(
                    PlaylistTrack.playlist_id == playlist.id,
                    PlaylistTrack.user_id == user_id,
                )
                .order_by(PlaylistTrack.position.asc(), PlaylistTrack.created_at.asc())
            ).scalars().all()

        return playlist, list(rows)
    finally:
        db.close()


def _playlist_track_video_id(track: Any) -> str:
    """Use the stored YTMusic id, or resolve one from playlist metadata."""
    vid = str(getattr(track, "yt_video_id", "") or "").strip()
    if vid:
        return vid

    title = str(getattr(track, "title", "") or "").strip()
    artist = str(getattr(track, "artist", "") or "").strip()
    album = str(getattr(track, "album", "") or "").strip()
    duration_ms = int(getattr(track, "duration_ms", 0) or 0)
    if not title or not artist:
        return ""

    try:
        found = ytmusic_integration.find_track(
            title=title,
            artist=artist,
            album=album,
            duration_seconds=int(duration_ms / 1000) or None,
        )
    except Exception:
        found = None

    return (
        str(getattr(found, "video_id", "") or "").strip()
        if getattr(found, "found", False)
        else ""
    )


async def _playlist_track_exists_in_subsonic(
    client: SubsonicClient,
    track: Any,
) -> bool:
    title = str(getattr(track, "title", "") or "").strip()
    artist = str(getattr(track, "artist", "") or "").strip()
    if not title or not artist:
        return False

    wanted_title = _norm_text(title)
    wanted_artist = _norm_text(artist)
    if not wanted_title or not wanted_artist:
        return False

    result = await client.search3(f"{title} {artist}", song_count=75)
    songs = result.get("song") or []
    for song in songs:
        candidate_title = _norm_text(str(song.get("title") or ""))
        candidate_artist = _norm_text(str(song.get("artist") or ""))
        if candidate_title == wanted_title and candidate_artist == wanted_artist:
            return True

    result = await client.search3(title, song_count=75)
    for song in (result.get("song") or []):
        candidate_title = _norm_text(str(song.get("title") or ""))
        candidate_artist = _norm_text(str(song.get("artist") or ""))
        if candidate_title == wanted_title and candidate_artist == wanted_artist:
            return True

    return False


@router.post("/track", response_model=Dict[str, Any])
async def add_track(request: Request, user: User = Depends(get_current_user)) -> Dict[str, Any]:
    _require_import_permission(user)
    ip = getattr(getattr(request, "client", None), "host", "") or ""
    if not RATE_LIMITER.allow(make_key(scope="subsonic_add_track", user_id=str(user.id), ip=ip), limit=20, window_s=10):
        raise HTTPException(status_code=429, detail="Too many requests")

    body = await request.json()
    vid = (body.get("yt_video_id") or body.get("video_id") or "").strip()
    title = (body.get("title") or "").strip()
    artist = (body.get("artist") or "").strip()
    album = (body.get("album") or "").strip()
    album_artist = (body.get("album_artist") or "").strip()
    browse_id = (
        body.get("album_browse_id")
        or body.get("yt_browse_id")
        or body.get("browse_id")
        or ""
    ).strip()
    art_url = (body.get("art_url") or "").strip()
    duration_ms = int(body.get("duration_ms") or 0)
    track_no = _track_number(body, 0)

    if not vid or not title or not artist:
        raise HTTPException(status_code=400, detail="yt_video_id, title, and artist are required")

    settings = _load_settings_short()
    if _subsonic_client_from_settings(settings) is None:
        raise HTTPException(status_code=409, detail="Subsonic is not configured. Add-to-library is disabled.")

    metadata = await _resolve_import_track_metadata(
        video_id=vid,
        title=title,
        artist=artist,
        album=album,
        album_artist=album_artist,
        browse_id=browse_id,
        art_url=art_url,
        duration_ms=duration_ms,
        track_no=track_no,
    )

    job = DownloadJob(
        video_id=vid,
        url=f"https://music.youtube.com/watch?v={vid}",
        title=metadata["title"],
        artist=metadata["artist"],
        album=metadata["album"],
        album_artist=metadata["album_artist"],
        browse_id=metadata["browse_id"],
        art_url=metadata["art_url"],
        track_no=metadata["track_no"],
        duration_ms=metadata["duration_ms"],
        persist_to_subsonic=True,
        user_id=str(user.id),
        priority=30,
    )
    await DOWNLOAD_MANAGER.enqueue_normal(job)

    if settings.get("slskd_enabled"):
        create_upgrade_job(
            user_id=str(user.id),
            yt_video_id=vid,
            yt_browse_id=metadata["browse_id"],
            title=metadata["title"],
            artist=metadata["artist"],
            album=metadata["album"],
            album_artist=metadata["album_artist"],
            duration_ms=metadata["duration_ms"],
            track_no=metadata["track_no"],
            art_url=metadata["art_url"],
        )

    invalidate_song_cache(f"song:{vid}")
    return {
        "ok": True,
        "video_id": vid,
        "track_no": metadata["track_no"],
        "browse_id": metadata["browse_id"],
    }


@router.post("/album", response_model=Dict[str, Any])
async def add_album(request: Request, user: User = Depends(get_current_user)) -> Dict[str, Any]:
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
                score -= abs(len(cand_title_keys) - len(yt_title_keys)) * 5.0
                if score > best_score:
                    best_score = score
                    best_overlap = overlap
                    existing_title_keys, existing_timed_keys = cand_title_keys, cand_timed_keys

            required_overlap = max(2, int(len(yt_title_keys) * 0.65 + 0.999))
            if best_overlap < required_overlap:
                existing_title_keys = set()
                existing_timed_keys = set()
    except Exception:
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
            user_id=str(user.id),
            priority=40,
        )
        await DOWNLOAD_MANAGER.enqueue_normal(job)

        if settings.get("slskd_enabled"):
            create_upgrade_job(
                user_id=str(user.id),
                yt_video_id=vid,
                yt_browse_id=browse_id,
                title=title,
                artist=track_artist or album_artist,
                album=alb,
                album_artist=album_artist,
                duration_ms=duration_ms,
                track_no=track_no,
                art_url=(t.get("art_url") or art_url or "").strip(),
            )

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


@router.post("/playlist/{playlist_id}", response_model=Dict[str, Any])
async def add_playlist(
    playlist_id: str,
    request: Request,
    user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """Add every missing track from a Helix playlist to the Subsonic library."""
    _require_import_permission(user)

    ip = getattr(getattr(request, "client", None), "host", "") or ""
    if not RATE_LIMITER.allow(
        make_key(scope="subsonic_add_playlist", user_id=str(user.id), ip=ip),
        limit=4,
        window_s=60,
    ):
        raise HTTPException(status_code=429, detail="Too many playlist import requests")

    playlist, tracks = _playlist_rows_for_user(str(user.id), playlist_id)

    settings = _load_settings_short()
    client = _subsonic_client_from_settings(settings)
    if client is None:
        raise HTTPException(
            status_code=409,
            detail="Subsonic is not configured. Add-to-library is disabled.",
        )

    total = len(tracks)
    skipped_existing = 0
    enqueued = 0
    unresolved = 0
    lookup_failed = 0
    unresolved_tracks: List[str] = []
    lookup_failed_tracks: List[str] = []

    semaphore = asyncio.Semaphore(4)

    async def classify(track: Any) -> tuple[Any, str]:
        if str(getattr(track, "subsonic_song_id", "") or "").strip():
            return track, "existing"

        async with semaphore:
            try:
                exists = await _playlist_track_exists_in_subsonic(client, track)
            except Exception:
                return track, "lookup_failed"
        return track, "existing" if exists else "missing"

    # Once one track resolves an album browse id, reuse it for other tracks from
    # the same album instead of performing a fresh YTMusic song search each time.
    resolved_album_ids: Dict[tuple[str, str], str] = {}

    try:
        classifications = await asyncio.gather(*(classify(track) for track in tracks))

        for track, state in classifications:
            title = str(getattr(track, "title", "") or "").strip()
            artist = str(getattr(track, "artist", "") or "").strip()
            album = str(getattr(track, "album", "") or "").strip()
            duration_ms = int(getattr(track, "duration_ms", 0) or 0)
            art_url = str(getattr(track, "art_url", "") or "").strip()
            browse_id = str(getattr(track, "yt_browse_id", "") or "").strip()

            if state == "existing":
                skipped_existing += 1
                continue

            if state == "lookup_failed":
                lookup_failed += 1
                lookup_failed_tracks.append(title or f"{artist} — unknown track")
                continue

            if not title or not artist:
                unresolved += 1
                unresolved_tracks.append(title or f"{artist} — unknown track")
                continue

            vid = await asyncio.to_thread(_playlist_track_video_id, track)
            if not vid:
                unresolved += 1
                unresolved_tracks.append(f"{artist} — {title}")
                continue

            album_key = (_norm_text(artist), _norm_text(album))
            browse_hint = browse_id or resolved_album_ids.get(album_key, "")
            metadata = await _resolve_import_track_metadata(
                video_id=vid,
                title=title,
                artist=artist,
                album=album,
                album_artist=artist,
                browse_id=browse_hint,
                art_url=art_url,
                duration_ms=duration_ms,
                track_no=0,
            )
            if metadata["browse_id"] and album_key[1]:
                resolved_album_ids[album_key] = metadata["browse_id"]

            job = DownloadJob(
                video_id=vid,
                url=f"https://music.youtube.com/watch?v={vid}",
                title=metadata["title"],
                artist=metadata["artist"],
                album=metadata["album"],
                album_artist=metadata["album_artist"],
                browse_id=metadata["browse_id"],
                art_url=metadata["art_url"],
                track_no=metadata["track_no"],
                duration_ms=metadata["duration_ms"],
                persist_to_subsonic=True,
                user_id=str(user.id),
                priority=40,
            )
            await DOWNLOAD_MANAGER.enqueue_normal(job)

            if settings.get("slskd_enabled"):
                create_upgrade_job(
                    user_id=str(user.id),
                    yt_video_id=vid,
                    yt_browse_id=metadata["browse_id"],
                    title=metadata["title"],
                    artist=metadata["artist"],
                    album=metadata["album"],
                    album_artist=metadata["album_artist"],
                    duration_ms=metadata["duration_ms"],
                    track_no=metadata["track_no"],
                    art_url=metadata["art_url"],
                )

            invalidate_song_cache(f"song:{vid}")
            enqueued += 1
    finally:
        await client.close()

    return {
        "ok": True,
        "playlist_id": str(playlist.id),
        "playlist_name": str(playlist.name or ""),
        "total": total,
        "enqueued": enqueued,
        "skipped_existing": skipped_existing,
        "unresolved": unresolved,
        "unresolved_tracks": unresolved_tracks,
        "lookup_failed": lookup_failed,
        "lookup_failed_tracks": lookup_failed_tracks,
    }
