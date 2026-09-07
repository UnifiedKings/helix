from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from fastapi import Depends, HTTPException, Request

from .auth import get_current_user
from .models import User
from .download_manager import DOWNLOAD_MANAGER, DownloadJob
from .integrations import ytmusic as ytmusic_integration
from .quality_upgrade_service import create_upgrade_job
from .rate_limit import RATE_LIMITER, make_key


_INSTALLED = False


def _norm_text(value: str) -> str:
    import re

    value = (value or "").strip().casefold()
    value = value.replace("’", "'").replace("`", "'").replace("´", "'")
    value = value.replace("–", "-").replace("—", "-")
    value = value.replace("'", "")
    value = re.sub(r"[^0-9a-z\s]+", " ", value)
    return " ".join(value.split())


async def _same_album_same_title_exists(
    client,
    *,
    title: str,
    album: str,
) -> bool:
    """Return True when Subsonic already has this exact track on this album.

    This deliberately does NOT compare duration, bitrate, codec, file extension,
    YTMusic id, or Subsonic id. Album import duplicate prevention should be the
    simple rule users expect: same normalized track title + same normalized album
    title means the track is already present.
    """
    wanted_title = _norm_text(title)
    wanted_album = _norm_text(album)
    if not wanted_title or not wanted_album:
        return False

    queries: List[str] = []
    for query in (
        f"{title} {album}".strip(),
        title.strip(),
        album.strip(),
    ):
        if query and query not in queries:
            queries.append(query)

    seen = set()
    for query in queries:
        result = await client.search3(query, song_count=200)
        for song in (result.get("song") or []):
            if not isinstance(song, dict):
                continue

            song_id = str(song.get("id") or "").strip()
            dedupe = song_id or (
                f"{_norm_text(str(song.get('title') or ''))}|"
                f"{_norm_text(str(song.get('album') or ''))}"
            )
            if dedupe in seen:
                continue
            seen.add(dedupe)

            if _norm_text(str(song.get("title") or "")) != wanted_title:
                continue
            if _norm_text(str(song.get("album") or "")) != wanted_album:
                continue

            return True

    return False


async def _add_album_exact_duplicate_guard(
    request: Request,
    user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """Album import endpoint with direct per-track Subsonic duplicate checks."""
    # Import the existing router module lazily so we reuse all of Helix's current
    # settings, permission, metadata, cache, and download helpers.
    from .routers import subsonic_add as existing

    existing._require_import_permission(user)

    ip = getattr(getattr(request, "client", None), "host", "") or ""
    if not RATE_LIMITER.allow(
        make_key(
            scope="subsonic_add_album",
            user_id=str(user.id),
            ip=ip,
        ),
        limit=8,
        window_s=10,
    ):
        raise HTTPException(status_code=429, detail="Too many requests")

    body = await request.json()
    browse_id = (body.get("browse_id") or "").strip()
    if not browse_id:
        raise HTTPException(status_code=400, detail="browse_id is required")

    album = await asyncio.to_thread(
        ytmusic_integration.get_album_full,
        browse_id,
    )
    tracks: List[Dict[str, Any]] = album.get("tracks") or []
    if not tracks:
        return {
            "ok": True,
            "total": 0,
            "enqueued": 0,
            "skipped_existing": 0,
        }

    album_title = (
        body.get("title")
        or album.get("title")
        or ""
    ).strip()
    album_artist = (
        body.get("artist")
        or album.get("artist")
        or ""
    ).strip()
    art_url = (
        body.get("art_url")
        or album.get("thumbnail_url")
        or album.get("thumbnail")
        or ""
    ).strip()

    settings = existing._load_settings_short()
    client = existing._subsonic_client_from_settings(settings)
    if client is None:
        raise HTTPException(
            status_code=409,
            detail="Subsonic is not configured. Add-to-library is disabled.",
        )

    enqueued = 0
    skipped = 0
    unresolved = 0
    lookup_failed = 0
    unresolved_tracks: List[str] = []
    lookup_failed_tracks: List[str] = []

    try:
        for index, track in enumerate(tracks, start=1):
            title = (track.get("title") or "").strip()
            track_artist = (
                track.get("artist")
                or album_artist
                or ""
            ).strip()
            track_album = (
                track.get("album")
                or album_title
                or album.get("title")
                or ""
            ).strip()
            duration_ms = existing._track_duration_ms(track)
            track_no = existing._track_number(track, index)

            if not title or not track_album:
                unresolved += 1
                unresolved_tracks.append(title or f"Track {index}")
                continue

            # The duplicate rule is intentionally direct:
            # SAME ALBUM + SAME TRACK NAME = DO NOT DOWNLOAD.
            try:
                if await _same_album_same_title_exists(
                    client,
                    title=title,
                    album=track_album,
                ):
                    skipped += 1
                    continue
            except Exception:
                # Do not silently claim a track is missing when the duplicate
                # lookup itself failed. Skipping this request is safer than
                # creating another local copy.
                lookup_failed += 1
                lookup_failed_tracks.append(title)
                continue

            vid = existing._resolve_album_track_video_id(
                track,
                album_title=track_album,
                album_artist=album_artist or track_artist,
            )
            if not vid:
                unresolved += 1
                unresolved_tracks.append(title)
                continue

            job = DownloadJob(
                video_id=vid,
                url=f"https://music.youtube.com/watch?v={vid}",
                title=title,
                artist=track_artist or album_artist,
                album=track_album,
                album_artist=album_artist or track_artist,
                browse_id=browse_id,
                art_url=(track.get("art_url") or art_url or "").strip(),
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
                    album=track_album,
                    album_artist=album_artist or track_artist,
                    duration_ms=duration_ms,
                    track_no=track_no,
                    art_url=(track.get("art_url") or art_url or "").strip(),
                )

            existing.invalidate_song_cache(f"song:{vid}")
            enqueued += 1

        existing.invalidate_album_cache(f"album:{browse_id}")
    finally:
        await client.close()

    return {
        "ok": True,
        "total": len(tracks),
        "enqueued": enqueued,
        "skipped_existing": skipped,
        "unresolved": unresolved,
        "unresolved_tracks": unresolved_tracks,
        "lookup_failed": lookup_failed,
        "lookup_failed_tracks": lookup_failed_tracks,
        "duplicate_rule": "same_album_same_title",
    }


def install_album_duplicate_guard() -> None:
    """Replace only the /api/subsonic/add/album handler.

    main.py calls this before it imports subsonic_add_router. Importing the router
    here creates its normal APIRoute, then we replace the callable used by that
    route's dependency graph. No GitHub/repository changes are required.
    """
    global _INSTALLED
    if _INSTALLED:
        return

    from .routers import subsonic_add

    for route in subsonic_add.router.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        if path == "/api/subsonic/add/album" and "POST" in methods:
            route.endpoint = _add_album_exact_duplicate_guard
            if getattr(route, "dependant", None) is not None:
                route.dependant.call = _add_album_exact_duplicate_guard
            _INSTALLED = True
            return

    raise RuntimeError("Could not locate Helix album import route")
