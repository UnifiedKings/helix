from __future__ import annotations

import asyncio
import anyio
import re
import time
import os
import random
import logging
from types import SimpleNamespace
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import select, delete, func, or_, update
from sqlalchemy.exc import IntegrityError, OperationalError

from ..auth import get_current_user
from ..db import get_db, SessionLocal
from ..models import User, PlaybackSession, QueueItem, ListenHistoryItem, Station, Playlist, PlaylistTrack, LikedTrack
from ..api_schemas.player import PlayerPlayAlbumRequest, PlayerPlayPlaylistRequest, PlayerPlayTrackRequest, PlayerJumpRequest, PlayerQueueItem, PlayerStateResponse, PlayerQueueAppendTrackRequest, PlayerQueueAppendAlbumRequest, PlayerQueueReorderRequest, PlayerRemoveQueueItemResponse, PlayerHistoryItem, PlayerHistoryResponse, PlayerActionRequest, PlayerReplayRequest, AutoplaySetRequest
from ..settings_store import get_settings
from ..user_settings_store import station_queue_ahead_for_user, queue_add_position_for_user
from ..integrations.subsonic import SubsonicClient
from ..integrations.ytmusic import get_album_full, find_track
from ..download_manager import DOWNLOAD_MANAGER, DownloadJob
from ..stations_engine import generate_and_append_station_track, generate_and_append_station_tracks
from ..validators import is_valid_yt_video_id



# Module logger
LOG = logging.getLogger("helix.player")

HELIX_PROGRESSIVE_MIN_BYTES = int(os.getenv("HELIX_PROGRESSIVE_MIN_BYTES", "262144"))
HELIX_PROGRESSIVE_STREAMING = str(os.getenv("HELIX_PROGRESSIVE_STREAMING", "false") or "").strip().lower() in {"1", "true", "yes", "on"}


def _browser_audio_media_type(path: str) -> str:
    """Return a browser-friendly audio MIME type for downloaded YT files.

    yt-dlp often writes Opus-in-WebM as ``.webm.part`` while the file is still
    downloading. Python's mimetype guessing treats ``.part`` as unknown, and
    some browsers reject ``application/octet-stream`` for an ``<audio>`` source.
    Use the underlying audio container extension instead.
    """
    import mimetypes

    guess_path = (path or "").strip()
    if guess_path.endswith(".part"):
        guess_path = guess_path[:-5]
    lower = guess_path.lower()
    if lower.endswith(".webm"):
        return "audio/webm; codecs=opus"
    if lower.endswith(".opus") or lower.endswith(".ogg") or lower.endswith(".oga"):
        return "audio/ogg; codecs=opus"
    if lower.endswith(".mp3"):
        return "audio/mpeg"
    if lower.endswith(".m4a") or lower.endswith(".mp4"):
        return "audio/mp4"
    return mimetypes.guess_type(guess_path)[0] or "audio/webm"



def _is_video_thumbnail_art(url: str) -> bool:
    value = _clean(url).lower()
    return "i.ytimg.com/vi/" in value or "img.youtube.com/vi/" in value


def _prefer_album_art(payload_art: str, resolved_art: str) -> str:
    """Never replace known music artwork with a YouTube video frame.

    The frontend may already have the correct yt3.googleusercontent.com cover
    from search. Keep that artwork when get_album_full() falls back to
    hqdefault.jpg.
    """
    payload_clean = _clean(payload_art)
    resolved_clean = _clean(resolved_art)
    if payload_clean and not _is_video_thumbnail_art(payload_clean):
        return payload_clean
    if resolved_clean and not _is_video_thumbnail_art(resolved_clean):
        return resolved_clean
    return payload_clean or resolved_clean

def _load_settings_short() -> dict:
    db = SessionLocal()
    try:
        return get_settings(db)
    finally:
        db.close()


async def _ytmusic_album_full_with_timeout(browse_id: str, timeout_s: float) -> dict:
    # ytmusicapi calls are sync and can hang; run in a thread and bound runtime.
    try:
        return await asyncio.wait_for(asyncio.to_thread(get_album_full, browse_id), timeout=timeout_s) or {}
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Timed out fetching album details from YouTube Music.")

# Prevent duplicate background fills per (user_id, browse_id)
_ALBUM_FILLING: set[tuple[str, str]] = set()



# Prevent duplicate station prefetch per user while a track is playing.
_STATION_PREFETCH_TASKS: dict[str, asyncio.Task] = {}
_STATION_PREFETCH_LOCKS: dict[str, asyncio.Lock] = {}

# Background download prefetch tasks per user
_DOWNLOAD_PREFETCH_TASKS: dict[str, asyncio.Task] = {}
_LAST_STARTSCAN_TS: float = 0.0


async def _background_fill_album_tracks(user_id: str) -> None:
    """Best-effort background work after enqueueing an album.

    Historically, Helix tried to "fill" unresolved album tracks in the background.
    The main safety goal is *not* to block playback or hold DB sessions open.

    For now, we rely on the normal download prefetcher (and /stream fulfillment)
    to make upcoming tracks available. This is intentionally lightweight.
    """
    try:
        _schedule_download_prefetch(user_id)
    except Exception:
        # Never let background scheduling break the request path.
        return

def _prefetch_ahead_count() -> int:
    # Default prefetch is 1 (next track). Set HELIX_PREFETCH_AHEAD=2/3 to download further ahead.
    try:
        return max(0, int(os.getenv("HELIX_PREFETCH_AHEAD", "1")))
    except Exception:
        return 1

def _is_queue_position_conflict(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "queue_items.session_user_id, queue_items.position" in msg and "unique constraint failed" in msg


def _append_queue_items_with_sqlite_lock(db: Session, user_id: str, items: list[QueueItem], max_attempts: int = 6) -> None:
    """Append queue items while holding a SQLite write lock before computing positions."""
    for attempt in range(max_attempts):
        try:
            db.rollback()
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")

            max_pos = db.execute(
                select(QueueItem.position)
                .where(QueueItem.session_user_id == user_id)
                .order_by(QueueItem.position.desc())
                .limit(1)
            ).scalar_one_or_none()
            base_pos = int(max_pos if max_pos is not None else -1) + 1

            for offset, item in enumerate(items):
                item.position = base_pos + offset
                item.session_user_id = user_id
                db.add(item)

            db.commit()
            return
        except IntegrityError as exc:
            db.rollback()
            if attempt >= max_attempts - 1 or not _is_queue_position_conflict(exc):
                raise
            time.sleep(0.03 * (attempt + 1))
        except OperationalError as exc:
            db.rollback()



def _insert_queue_items_after_current_with_sqlite_lock(
    db: Session,
    user_id: str,
    items: list[QueueItem],
    max_attempts: int = 6,
) -> None:
    """Insert items directly after the currently playing item.

    Queue positions have a per-user UNIQUE constraint. Shift the tail out of the
    way one row at a time while holding SQLite's write lock, then place the new
    items and compact the shifted tail back down. The current item itself never
    moves, so PlaybackSession.current_index remains valid.
    """
    if not items:
        return

    for attempt in range(max_attempts):
        try:
            db.rollback()
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")

            existing = db.execute(
                select(QueueItem)
                .where(QueueItem.session_user_id == user_id)
                .order_by(QueueItem.position.asc())
            ).scalars().all()

            if not existing:
                insert_at = 0
                tail: list[QueueItem] = []
            else:
                sess = db.execute(
                    select(PlaybackSession).where(PlaybackSession.user_id == user_id)
                ).scalar_one_or_none()
                current_index = int(getattr(sess, "current_index", 0) or 0)
                current_index = max(0, min(current_index, len(existing) - 1))
                insert_at = current_index + 1
                tail = existing[insert_at:]

            # Move the tail to collision-free temporary positions. Explicit
            # descending UPDATEs avoid SQLite's row-by-row UNIQUE collisions.
            temporary_offset = max(1_000_000, len(existing) + len(items) + 1024)
            for row in reversed(tail):
                db.execute(
                    update(QueueItem)
                    .where(QueueItem.id == row.id)
                    .values(position=int(row.position) + temporary_offset)
                )

            for offset, item in enumerate(items):
                item.position = insert_at + offset
                item.session_user_id = user_id
                db.add(item)
            db.flush()

            tail_start = insert_at + len(items)
            for offset, row in enumerate(tail):
                db.execute(
                    update(QueueItem)
                    .where(QueueItem.id == row.id)
                    .values(position=tail_start + offset)
                )

            db.commit()
            return
        except IntegrityError as exc:
            db.rollback()
            if attempt >= max_attempts - 1 or not _is_queue_position_conflict(exc):
                raise
            time.sleep(0.03 * (attempt + 1))
        except OperationalError:
            db.rollback()
            if attempt >= max_attempts - 1:
                raise
            time.sleep(0.03 * (attempt + 1))

            if attempt >= max_attempts - 1 or "database is locked" not in str(exc).lower():
                raise
            time.sleep(0.03 * (attempt + 1))

    raise HTTPException(status_code=503, detail="Queue is busy. Please try again.")


async def _prefetch_next_downloads(user_id: str) -> None:
    """Prefetch downloads for upcoming queue items (FIFO) so playback doesn't stall.

    IMPORTANT: this function intentionally does not hold a DB session while
    awaiting Subsonic or DownloadManager work. Holding SQLite connections across
    awaits is what makes the db watchdog report long-held connections during
    frequent /api/playback/state polling.
    """
    try:
        # DB burst only: snapshot settings/current queue, then close the session.
        db = SessionLocal()
        try:
            settings = get_settings(db)
            sess = db.get(PlaybackSession, user_id)
            if not sess:
                return

            all_items = db.execute(
                select(QueueItem)
                .where(QueueItem.session_user_id == user_id)
                .order_by(QueueItem.position.asc())
            ).scalars().all()

            try:
                ahead = max(0, min(20, int(settings.get("download_prefetch_ahead", _prefetch_ahead_count()) or 0)))
            except Exception:
                ahead = _prefetch_ahead_count()
            if ahead <= 0 or not all_items:
                return

            cur_idx = int(sess.current_index or 0)
            start = cur_idx + 1
            end = min(len(all_items), start + ahead)

            # Keep detached ORM objects out of the async section. Snapshot only
            # the primitive fields needed for matching/download decisions.
            candidates = [
                {
                    "id": item.id,
                    "position": int(item.position or 0),
                    "title": str(item.title or ""),
                    "artist": str(item.artist or ""),
                    "album": str(item.album or ""),
                    "art_url": str(item.art_url or ""),
                    "duration_ms": int(item.duration_ms or 0),
                    "source": str(item.source or ""),
                    "subsonic_song_id": str(item.subsonic_song_id or ""),
                    "yt_video_id": str(item.yt_video_id or ""),
                }
                for item in all_items[start:end]
            ]
        finally:
            db.close()

        if not candidates:
            return

        client = None
        if _is_subsonic_configured(settings):
            try:
                client = await _subsonic_client_from_settings(settings)
            except Exception:
                client = None

        if client is not None:
            # Best-effort: trigger a scan occasionally so new imports become searchable quickly.
            global _LAST_STARTSCAN_TS
            now = time.time()
            if (now - _LAST_STARTSCAN_TS) > 30:
                try:
                    await client.start_scan()
                    _LAST_STARTSCAN_TS = now
                except Exception:
                    pass

        for offset, qi in enumerate(candidates):
            # If already playable in Subsonic, skip.
            if qi["source"] == "subsonic" and qi["subsonic_song_id"]:
                continue

            found = None
            if client is not None:
                # Re-check Subsonic by metadata without holding the DB connection.
                try:
                    want_ms = int(qi["duration_ms"] or 0) or None
                    found = await client.search_song_best(
                        title=qi["title"],
                        artist=qi["artist"],
                        duration_ms=want_ms,
                    )
                except Exception:
                    found = None

            if found and found.get("id"):
                # DB burst only: persist the match after the await finishes.
                db = SessionLocal()
                try:
                    current = db.get(QueueItem, qi["id"])
                    if current and current.session_user_id == user_id:
                        current.source = "subsonic"
                        current.subsonic_song_id = str(found.get("id"))
                        current.is_playable = True
                        current.error = ""
                        db.commit()
                        from ..realtime import schedule_player_state_broadcast
                        schedule_player_state_broadcast(user_id)
                finally:
                    db.close()
                continue

            vid = _clean(qi["yt_video_id"])
            if not vid:
                continue

            if DOWNLOAD_MANAGER.is_ready(vid):
                continue

            # Lower priority number == sooner. Keep prefetch behind "play now".
            prio = 20 + offset
            await DOWNLOAD_MANAGER.enqueue_normal(DownloadJob(
                video_id=vid,
                url=f"https://music.youtube.com/watch?v={vid}",
                title=qi["title"],
                artist=qi["artist"],
                album=qi["album"],
                art_url=qi["art_url"],
                track_no=0,
                duration_ms=int(qi["duration_ms"] or 0),
                user_id=user_id,
                priority=prio,
            ))
    except Exception as e:
        LOG.warning("[download-prefetch] failed user=%s err=%r", user_id, e)
    finally:
        try:
            if "client" in locals() and client is not None:
                await client.close()
        except Exception:
            pass
        _DOWNLOAD_PREFETCH_TASKS.pop(user_id, None)

async def _schedule_download_prefetch_async(user_id: str) -> None:
    t = _DOWNLOAD_PREFETCH_TASKS.get(user_id)
    if t and not t.done():
        return
    _DOWNLOAD_PREFETCH_TASKS[user_id] = asyncio.create_task(_prefetch_next_downloads(user_id))

def _schedule_download_prefetch(user_id: str) -> None:
    """Schedule download prefetch from both sync and async request contexts."""
    try:
        # If we're already in the event loop (async endpoint), schedule directly.
        asyncio.get_running_loop()
        # Create task inside the loop context.
        try:
            t = _DOWNLOAD_PREFETCH_TASKS.get(user_id)
            if t and not t.done():
                return
            _DOWNLOAD_PREFETCH_TASKS[user_id] = asyncio.create_task(_prefetch_next_downloads(user_id))
        except Exception:
            return
    except RuntimeError:
        # Sync endpoint (threadpool): hop into the main loop safely.
        try:
            anyio.from_thread.run(_schedule_download_prefetch_async, user_id)
        except Exception:
            return

async def _prefetch_next_station_item(user_id: str, station_id: str) -> None:
    """Ensure there are N queued items after the current index for the active station.

    IMPORTANT: Never holds a DB session across awaits.
    """
    try:
        # DB burst: determine what we need to prefetch
        db = SessionLocal()
        try:
            sess = db.get(PlaybackSession, user_id)
            if not sess or not sess.is_playing or sess.active_station_id != station_id:
                return

            ahead = station_queue_ahead_for_user(db, user_id)
            if ahead <= 0:
                return

            settings = get_settings(db)
            cur_idx = int(sess.current_index or 0)

            # Defensive safety cap:
            # Only ever prefetch into the fixed ahead window (cur_idx+1 .. cur_idx+ahead).
            cap_max_pos = cur_idx + ahead
            existing_positions = set(
                db.execute(
                    select(QueueItem.position).where(
                        QueueItem.session_user_id == user_id,
                        QueueItem.position > cur_idx,
                        QueueItem.position <= cap_max_pos,
                    )
                ).scalars().all()
            )
            missing_positions: List[int] = [
                pos for pos in range(cur_idx + 1, cap_max_pos + 1) if pos not in existing_positions
            ]
        finally:
            db.close()

        # External I/O: fill the entire ahead window in one provider call. This
        # avoids resolving the station seed/related pool again for each missing slot.
        if missing_positions:
            appended = await generate_and_append_station_tracks(
                user_id,
                station_id,
                settings=settings,
                count=len(missing_positions),
                advance_to_new_item=False,
                positions=missing_positions,
            )
            if appended:
                from ..realtime import schedule_player_state_broadcast
                schedule_player_state_broadcast(user_id)
    except Exception as e:
        LOG.warning("[station-prefetch] failed user=%s station=%s err=%r", user_id, station_id, e)
    finally:
        _STATION_PREFETCH_TASKS.pop(user_id, None)



def _clean(s: str) -> str:
    return " ".join((s or "").strip().split())

def _yt_video_id_from_key(key: Any) -> str:
    """Recover a YouTube video id from legacy stable identity keys.

    Older liked-song rows may have been keyed as ``yt:<video_id>`` before the
    explicit ``yt_video_id`` column was populated reliably. Liked Songs are not
    guaranteed to exist in Subsonic anymore, so preserving/recovering this YT id
    is required for playback fulfillment.
    """
    raw = _clean(str(key or ""))
    for prefix in ("yt:", "ytmusic:", "youtube:"):
        if raw.startswith(prefix):
            candidate = raw[len(prefix):].strip()
            return candidate if is_valid_yt_video_id(candidate) else ""
    return raw if is_valid_yt_video_id(raw) else ""


def _find_track_safe(*, title: str, artist: str, album: str | None = None, duration_seconds: int | None = None):
    """Call the YTMusic resolver across older/newer helper signatures."""
    try:
        return find_track(title=title, artist=artist, album=album, duration_seconds=duration_seconds, limit=9)
    except TypeError:
        return find_track(title=title, artist=artist, album=album, duration_seconds=duration_seconds)


def _liked_row_for_queue_item(db: Session, user_id: str, cur: QueueItem) -> LikedTrack | None:
    """Best-effort match from a queue item back to its liked-track row."""
    subsonic_id = _clean(getattr(cur, "subsonic_song_id", "") or "")
    if subsonic_id:
        row = db.execute(
            select(LikedTrack).where(
                LikedTrack.user_id == user_id,
                LikedTrack.subsonic_song_id == subsonic_id,
            )
        ).scalars().first()
        if row:
            return row
        row = db.execute(
            select(LikedTrack).where(
                LikedTrack.user_id == user_id,
                LikedTrack.key == f"subsonic:{subsonic_id}",
            )
        ).scalar_one_or_none()
        if row:
            return row

    title = _clean(getattr(cur, "title", "") or "")
    artist = _clean(getattr(cur, "artist", "") or "")
    if title and artist:
        return db.execute(
            select(LikedTrack)
            .where(
                LikedTrack.user_id == user_id,
                LikedTrack.title == title,
                LikedTrack.artist == artist,
            )
            .order_by(LikedTrack.created_at.desc())
            .limit(1)
        ).scalars().first()
    return None


def _playlist_track_row_for_queue_item(db: Session, user_id: str, cur: QueueItem) -> PlaylistTrack | None:
    """Best-effort match from a queue item back to its playlist-track row.

    Queue items intentionally do not own playlist mutation state, so stale-source
    repair matches by user + source identity first, then by title/artist as a
    fallback. This is used only when Subsonic playback already failed.
    """
    subsonic_id = _clean(getattr(cur, "subsonic_song_id", "") or "")
    if subsonic_id:
        row = db.execute(
            select(PlaylistTrack)
            .where(
                PlaylistTrack.user_id == user_id,
                PlaylistTrack.subsonic_song_id == subsonic_id,
            )
            .order_by(PlaylistTrack.created_at.desc())
            .limit(1)
        ).scalars().first()
        if row:
            return row
        row = db.execute(
            select(PlaylistTrack)
            .where(
                PlaylistTrack.user_id == user_id,
                PlaylistTrack.key == f"subsonic:{subsonic_id}",
            )
            .order_by(PlaylistTrack.created_at.desc())
            .limit(1)
        ).scalars().first()
        if row:
            return row

    title = _clean(getattr(cur, "title", "") or "")
    artist = _clean(getattr(cur, "artist", "") or "")
    if title and artist:
        return db.execute(
            select(PlaylistTrack)
            .where(
                PlaylistTrack.user_id == user_id,
                PlaylistTrack.title == title,
                PlaylistTrack.artist == artist,
            )
            .order_by(PlaylistTrack.created_at.desc())
            .limit(1)
        ).scalars().first()
    return None


async def _recover_stale_subsonic_playlist_track(db: Session, user_id: str, cur: QueueItem) -> bool:
    """Mark a stale Subsonic playlist track and persist a recovered YTMusic id.

    Normal playlist rows can point to Subsonic items that existed when the track
    was added but were later deleted from Navidrome/the filesystem. Preserve the
    historical Subsonic id, mark the row stale, and store a YTMusic fallback so
    future playlist playback can use temporary YT fulfillment without importing
    into Subsonic.
    """
    row = _playlist_track_row_for_queue_item(db, user_id, cur)
    existing_vid = _clean(getattr(cur, "yt_video_id", "") or "")
    if row:
        existing_vid = existing_vid or _clean(row.yt_video_id or "") or _yt_video_id_from_key(getattr(row, "key", ""))
        row.stale_subsonic = True

    vid = existing_vid
    if not vid:
        try:
            want_dur_s = int((cur.duration_ms or 0) / 1000) if (cur.duration_ms or 0) else None
            found = await asyncio.to_thread(
                _find_track_safe,
                title=cur.title or "",
                artist=cur.artist or "",
                album=cur.album or None,
                duration_seconds=want_dur_s,
            )
            if getattr(found, "found", False) and getattr(found, "video_id", ""):
                candidate = str(found.video_id or "").strip()
                if is_valid_yt_video_id(candidate):
                    vid = candidate
                    LOG.info(
                        "[stream] recovered stale Subsonic playlist track via YTMusic vid=%s conf=%.2f title=%r artist=%r",
                        vid,
                        float(getattr(found, "confidence", 0.0) or 0.0),
                        cur.title,
                        cur.artist,
                    )
        except Exception as e:
            LOG.warning("[stream] stale Subsonic playlist-track YT recovery failed: %r", e)

    if row:
        row.stale_subsonic = True
        if vid:
            row.yt_video_id = vid
            row.source = "ytmusic"
            row.ytmusic_recovered_at = datetime.utcnow()
            if not _clean(row.art_url or ""):
                row.art_url = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
        db.commit()

    if vid:
        cur.source = "ytmusic"
        cur.yt_video_id = vid
        cur.is_playable = False
        cur.error = "STALE_SUBSONIC_PLAYLIST_RECOVERED_YT"
        if not _clean(getattr(cur, "art_url", "") or ""):
            cur.art_url = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
        db.commit()
        return True

    cur.is_playable = False
    cur.error = "STALE_SUBSONIC_PLAYLIST_MISSING_YT"
    db.commit()
    return False


async def _recover_stale_subsonic_liked_track(db: Session, user_id: str, cur: QueueItem) -> bool:
    """Mark a stale Subsonic-liked song and persist a recovered YTMusic id.

    This handles songs that were liked while present in Subsonic/Navidrome but
    whose library file was later deleted. The liked row keeps its historical
    Subsonic id, gets stale_subsonic=True, and stores a YT video id so future
    playback does not need to rediscover the source.
    """
    row = _liked_row_for_queue_item(db, user_id, cur)
    existing_vid = _clean(getattr(cur, "yt_video_id", "") or "")
    if row:
        existing_vid = existing_vid or _clean(row.yt_video_id or "") or _yt_video_id_from_key(getattr(row, "key", ""))
        row.stale_subsonic = True

    vid = existing_vid
    if not vid:
        try:
            want_dur_s = int((cur.duration_ms or 0) / 1000) if (cur.duration_ms or 0) else None
            found = await asyncio.to_thread(
                _find_track_safe,
                title=cur.title or "",
                artist=cur.artist or "",
                album=cur.album or None,
                duration_seconds=want_dur_s,
            )
            if getattr(found, "found", False) and getattr(found, "video_id", ""):
                candidate = str(found.video_id or "").strip()
                if is_valid_yt_video_id(candidate):
                    vid = candidate
                    LOG.info(
                        "[stream] recovered stale Subsonic liked song via YTMusic vid=%s conf=%.2f title=%r artist=%r",
                        vid,
                        float(getattr(found, "confidence", 0.0) or 0.0),
                        cur.title,
                        cur.artist,
                    )
        except Exception as e:
            LOG.warning("[stream] stale Subsonic liked-song YT recovery failed: %r", e)

    if row:
        row.stale_subsonic = True
        if vid:
            row.yt_video_id = vid
            row.source = "ytmusic"
            row.ytmusic_recovered_at = datetime.utcnow()
            if not _clean(row.art_url or ""):
                row.art_url = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
        db.commit()

    if vid:
        cur.source = "ytmusic"
        cur.yt_video_id = vid
        cur.is_playable = False
        cur.error = "STALE_SUBSONIC_RECOVERED_YT"
        if not _clean(getattr(cur, "art_url", "") or ""):
            cur.art_url = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
        db.commit()
        return True

    cur.is_playable = False
    cur.error = "STALE_SUBSONIC_MISSING_YT"
    db.commit()
    return False




def _artist_is_suspicious(s: str) -> bool:
    t = (s or "").strip().lower()
    if not t:
        return True
    if "view" in t:
        return True
    if re.fullmatch(r"[0-9][0-9,\.\s]*", t or ""):
        return True
    if re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", t):
        return True
    return False


def _safe_artist(primary: str, fallback: str) -> str:
    a = _clean(primary)
    if _artist_is_suspicious(a):
        return _clean(fallback)
    return a

def _album_artist_default(full_artist: str, payload_artist: str, track0_artist: str) -> str:
    """Choose a reasonable album-artist value.

    Prefer YT Music album artist (full_artist), then the frontend payload artist,
    then track0_artist. Any value that looks like a viewcount/duration is rejected.
    """
    for cand in (_clean(full_artist), _clean(payload_artist), _clean(track0_artist)):
        if cand and (not _artist_is_suspicious(cand)):
            return cand
    return ""


def _is_views_album(s: str) -> bool:
    ss = _clean(s).lower()
    if not ss:
        return False
    return ("views" in ss) and bool(re.search(r"\bviews\b", ss))

def _pick_best_song_result(songs, *, want_title: str, want_artist: str, want_vid: str = ""):
    wt = _clean(want_title).lower()
    wa = _clean(want_artist).lower()
    wv = _clean(want_vid)
    best = None
    best_score = -1.0
    for s in songs or []:
        title = _clean(str(s.get("title") or ""))
        artist = _clean(str(s.get("artist") or ""))
        album = _clean(str(s.get("album") or ""))
        vid = _clean(str(s.get("video_id") or ""))
        if _is_views_album(album):
            continue
        sc = 0.0
        if wv and vid and vid == wv:
            sc += 5.0
        if title and wt and title.lower() == wt:
            sc += 2.0
        if artist and wa and artist.lower() == wa:
            sc += 2.0
        if album:
            sc += 0.5
        if sc > best_score:
            best_score = sc
            best = s
    return best

def _pick_best_album_result(albums, *, want_album: str, want_artist: str):
    wa = _clean(want_album).lower()
    wr = _clean(want_artist).lower()
    best = None
    best_score = -1.0
    for a in albums or []:
        title = _clean(str(a.get("title") or ""))
        artist = _clean(str(a.get("artist") or ""))
        if _is_views_album(title):
            continue
        sc = 0.0
        if title and wa and title.lower() == wa:
            sc += 2.0
        if artist and wr and artist.lower() == wr:
            sc += 2.0
        if _clean(str(a.get("browse_id") or "")):
            sc += 0.5
        if sc > best_score:
            best_score = sc
            best = a
    return best


def _norm_text(s: str) -> str:
    """Normalize text for Subsonic matching: strip, collapse whitespace, normalize unicode apostrophes, and lowercase."""
    s = _clean(s)
    # normalize common curly apostrophes/quotes and dashes
    s = s.replace("’", "'").replace("‘", "'").replace("“", """).replace("”", """).replace("–", "-").replace("—", "-")
    s = s.lower()
    # remove excessive punctuation spacing
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _norm_for_subsonic(title: str, artist: str, album: str = "") -> tuple[str, str, str]:
    return (_norm_text(title), _norm_text(artist), _norm_text(album))



def _infer_int(s: Any, default: int = 0) -> int:
    try:
        return int(s)
    except Exception:
        return default


def _looks_like_views(s: str) -> bool:
    ss = _clean(s).lower()
    if not ss:
        return True
    if "view" in ss or "play" in ss:
        return True
    if re.fullmatch(r"\d+[\d,\.]*\s*[kmb]?", ss):
        return True
    return False


def _repair_from_album_full(cur: QueueItem) -> None:
    """Best-effort: use structured album metadata to fill missing/bad fields on a queue item."""
    bid = _clean(getattr(cur, "yt_browse_id", "") or "")
    vid = _clean(getattr(cur, "yt_video_id", "") or "")
    if not bid:
        return
    full = get_album_full(bid) or {}
    alb_title = _clean(full.get("title") or "")
    alb_artist = _clean(full.get("artist") or "")
    if alb_title and not _clean(cur.album or ""):
        cur.album = alb_title
    # Always prefer album artist as a fallback if track artist is missing/bad.
    if alb_artist and (_looks_like_views(cur.artist) or not _clean(cur.artist or "")):
        cur.artist = alb_artist
    # If we can find the matching track entry, prefer its structured artist/title.
    if vid:
        for t in (full.get("tracks") or []):
            if _clean(t.get("video_id") or "") == vid:
                t_title = _clean(t.get("title") or "")
                t_artist = _clean(t.get("artist") or "")
                if t_title and not _clean(cur.title or ""):
                    cur.title = t_title
                if t_artist and (_looks_like_views(cur.artist) or not _clean(cur.artist or "")):
                    cur.artist = t_artist or alb_artist or cur.artist
                # also fill duration if missing
                if (not int(cur.duration_ms or 0)) and t.get("lengthMs"):
                    try:
                        cur.duration_ms = int(t.get("lengthMs") or 0)
                    except Exception:
                        pass
                break


def _to_item(q: QueueItem) -> PlayerQueueItem:
    return PlayerQueueItem(
        id=q.id,
        position=q.position,
        title=q.title,
        artist=q.artist,
        album=q.album or "",
        duration_ms=q.duration_ms or 0,
        art_url=q.art_url or "",
        source=q.source,
        subsonic_song_id=getattr(q, "subsonic_song_id", "") or "",
        yt_video_id=getattr(q, "yt_video_id", "") or "",
        yt_browse_id=getattr(q, "yt_browse_id", "") or "",
        mb_recording_id=getattr(q, "mb_recording_id", "") or "",
        mb_artist_id=getattr(q, "mb_artist_id", "") or "",
        is_playable=q.is_playable,
        error=q.error or "",
    )


def _can_play(it: QueueItem) -> bool:
    """A queue item is playable if it's in Subsonic OR it can be fulfilled on-demand.

    For station-discovered items we may not have a YT id yet; we can still resolve one
    lazily at stream time using the title/artist intent.
    """
    if bool(it.is_playable):
        return True
    if getattr(it, "source", "") == "subsonic":
        return False
    return bool((it.title or "").strip()) and bool((it.artist or "").strip())




def _history_retention(settings: Dict[str, Any]) -> int:
    try:
        return max(0, min(50000, int(settings.get("listen_history_retention") or 10000)))
    except Exception:
        return 10000


def _parse_history_datetime(value: str | None, *, end_of_day: bool = False) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        # HTML date inputs arrive as YYYY-MM-DD. Make the upper bound inclusive.
        if len(raw) == 10:
            parsed = datetime.fromisoformat(raw)
            return parsed + timedelta(days=1) if end_of_day else parsed
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone().replace(tzinfo=None)
        return parsed
    except Exception:
        return None


def _to_history(h: ListenHistoryItem) -> PlayerHistoryItem:
    return PlayerHistoryItem(
        id=h.id,
        queue_item_id=h.queue_item_id,
        title=h.title,
        artist=h.artist,
        album=h.album or "",
        duration_ms=h.duration_ms or 0,
        art_url=h.art_url or "",
        subsonic_song_id=getattr(h, "subsonic_song_id", "") or "",
        yt_video_id=getattr(h, "yt_video_id", "") or "",
        yt_browse_id=getattr(h, "yt_browse_id", "") or "",
        mb_recording_id=getattr(h, "mb_recording_id", "") or "",
        mb_artist_id=getattr(h, "mb_artist_id", "") or "",
        station_id=getattr(h, "station_id", "") or "",
        source=h.source or "subsonic",
        event=h.event,
        reason=h.reason or "",
        played_ms=h.played_ms or 0,
        created_at=h.created_at.isoformat() + "Z",
    )


def _push_history(db: Session, user_id: str, item: Optional[QueueItem], event: str, reason: str, played_ms: int, settings: Dict[str, Any]):
    if not item:
        return
    lim = _history_retention(settings)
    if lim <= 0:
        return

    # Station-scoped history: bind entries to the currently active station (if any).
    sess = _get_or_create_session(db, user_id)
    station_id = str(getattr(sess, "active_station_id", "") or "")

    last = db.execute(
        select(ListenHistoryItem)
        .where(ListenHistoryItem.user_id == user_id)
        .where(ListenHistoryItem.station_id == station_id)
        .order_by(ListenHistoryItem.created_at.desc())
        .limit(1)
    ).scalars().first()
    if last and last.queue_item_id == item.id:
        return

    h = ListenHistoryItem(
        user_id=user_id,
        station_id=station_id,
        queue_item_id=item.id,
        title=item.title,
        artist=item.artist,
        album=item.album or "",
        duration_ms=item.duration_ms or 0,
        art_url=item.art_url or "",
        source=item.source or "subsonic",
        subsonic_song_id=getattr(item, "subsonic_song_id", "") or "",
        yt_video_id=getattr(item, "yt_video_id", "") or "",
        yt_browse_id=getattr(item, "yt_browse_id", "") or "",
        mb_recording_id=getattr(item, "mb_recording_id", "") or "",
        mb_artist_id=getattr(item, "mb_artist_id", "") or "",
        event=event,
        reason=reason,
        played_ms=max(0, int(played_ms or 0)),
    )
    db.add(h)
    db.commit()

    # Retention is per user, not per station, so one busy station cannot keep an
    # unbounded global history while other station histories are preserved.
    ids = db.execute(
        select(ListenHistoryItem.id)
        .where(ListenHistoryItem.user_id == user_id)
        .order_by(ListenHistoryItem.created_at.desc())
        .offset(lim)
    ).scalars().all()
    if ids:
        db.execute(delete(ListenHistoryItem).where(ListenHistoryItem.id.in_(ids)))
        db.commit()


def _get_or_create_session(db: Session, user_id: str) -> PlaybackSession:
    sess = db.get(PlaybackSession, user_id)
    if sess:
        return sess
    sess = PlaybackSession(user_id=user_id, current_index=0, is_playing=False)
    db.add(sess)
    db.commit()
    db.refresh(sess)
    return sess


async def _subsonic_client_from_settings(settings: Dict[str, Any]) -> SubsonicClient:
    base_url = str(settings.get("subsonic_base_url") or "").strip()
    username = str(settings.get("subsonic_username") or "").strip()
    password = str(settings.get("subsonic_password") or "").strip()
    if not base_url or not username or not password:
        raise HTTPException(status_code=400, detail="Subsonic settings incomplete. Set base_url, username, password in Admin Settings.")
    client_name = str(settings.get("subsonic_client_name") or "Helix")
    api_version = str(settings.get("subsonic_api_version") or "1.16.1")
    timeout_s = _infer_int(settings.get("subsonic_timeout_s"), 20) or 20
    return SubsonicClient(base_url=base_url, username=username, password=password, client_name=client_name, api_version=api_version, timeout_s=timeout_s)


def _is_subsonic_configured(settings: Dict[str, Any]) -> bool:
    return bool(
        str(settings.get("subsonic_base_url") or "").strip()
        and str(settings.get("subsonic_username") or "").strip()
        and str(settings.get("subsonic_password") or "").strip()
    )


async def _try_match_subsonic_song(settings: Dict[str, Any], *, title: str, artist: str, duration_ms: int | None) -> Optional[Dict[str, Any]]:
    if not _is_subsonic_configured(settings):
        return None
    client = None
    try:
        client = await _subsonic_client_from_settings(settings)
        return await asyncio.wait_for(
            client.search_song_best(title=title, artist=artist, duration_ms=duration_ms or None),
            timeout=float(os.getenv("HELIX_SUBSONIC_SEARCH_TIMEOUT_S", "10")),
        )
    except Exception:
        return None
    finally:
        if client is not None:
            try:
                await client.close()
            except Exception:
                pass


def _rep_release(releases: List[Dict[str, Any]], preferred_country: str = "US") -> Optional[Dict[str, Any]]:
    if not releases:
        return None
    pref = (preferred_country or "US").upper().strip()

    def score(r: Dict[str, Any]) -> Tuple[int, int, str]:
        c = (r.get("country") or "").upper()
        date = r.get("date") or ""
        status = (r.get("status") or "").lower()
        # Prefer official-ish status, then preferred country, then earliest date.
        s = 0
        if status == "official":
            s += 50
        if c == pref:
            s += 40
        elif c:
            s += 10
        # earlier date higher: invert by sorting date string
        return (s, 0, date)

    best = None
    best_s = -1
    best_date = "9999-99-99"
    for r in releases:
        c = (r.get("country") or "").upper()
        status = (r.get("status") or "").lower()
        s = 0
        if status == "official":
            s += 50
        if c == pref:
            s += 40
        elif c:
            s += 10
        date = r.get("date") or "9999-99-99"
        if s > best_s or (s == best_s and date < best_date):
            best_s = s
            best_date = date
            best = r
    return best


def state(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    sess = _get_or_create_session(db, user.id)
    items = db.execute(select(QueueItem).where(QueueItem.session_user_id == user.id).order_by(QueueItem.position.asc())).scalars().all()
    queue = [_to_item(i) for i in items]
    now = queue[sess.current_index] if 0 <= sess.current_index < len(queue) else None

    # Include station info for "station mode" UI.
    active_station_id = str(getattr(sess, "active_station_id", "") or "")
    active_station = None
    if active_station_id:
        st = db.get(Station, active_station_id)
        if st and st.user_id == user.id:
            try:
                active_station = {
                    "id": st.id,
                    "name": st.name,
                    "seed_type": st.seed_type,
                    "seed_title": st.seed_title,
                    "seed_artist": st.seed_artist,
                    "mb_artist_id": st.mb_artist_id or "",
                    "mb_recording_id": st.mb_recording_id or "",
                    "discovery": float(st.discovery or 0.35),
                    "temperature": float(getattr(st, "temperature", 0.9) or 0.9),
                    "created_at": st.created_at.isoformat() + "Z",
                    "updated_at": st.updated_at.isoformat() + "Z",
                }
            except Exception:
                active_station = None
    # Do not schedule download prefetch from the high-frequency state polling
    # endpoint. Scheduling can block sync request threads when the event loop is
    # busy, and the request-scoped DB session remains open while that happens.
    # Prefetch is already triggered from playback/queue-changing paths and from
    # stream fulfillment.
    return PlayerStateResponse(
        is_playing=bool(sess.is_playing),
        current_index=int(sess.current_index),
        now_playing=now,
        queue=queue,
        autoplay_enabled=bool(getattr(sess, "autoplay_enabled", True)),
        active_station_id=active_station_id,
        active_station=active_station,
    )


def _changed_state(db: Session, user: User):
    from ..realtime import schedule_player_state_broadcast
    snapshot = state(db=db, user=user)
    schedule_player_state_broadcast(user.id)
    return snapshot


def set_autoplay(payload: AutoplaySetRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    sess = _get_or_create_session(db, user.id)
    sess.autoplay_enabled = bool(payload.enabled)
    db.commit()
    return _changed_state(db=db, user=user)


def _clear_queue(db: Session, user_id: str, *, settings: Dict[str, Any], log_current: bool = False, played_ms: int = 0):
    # If requested, log ONLY the currently playing item (do not log future queued items).
    sess = _get_or_create_session(db, user_id)
    items = db.execute(select(QueueItem).where(QueueItem.session_user_id == user_id).order_by(QueueItem.position.asc())).scalars().all()
    cur = items[sess.current_index] if 0 <= sess.current_index < len(items) else None
    if log_current:
        _push_history(db, user_id, cur, event="skipped", reason="replaced_queue", played_ms=played_ms, settings=settings)

    db.execute(delete(QueueItem).where(QueueItem.session_user_id == user_id))
    sess.current_index = 0
    sess.is_playing = False
    db.commit()


async def play_track(payload: PlayerPlayTrackRequest, user: User = Depends(get_current_user)):
    settings = _load_settings_short()

    title = _clean(payload.title)
    artist = _clean(payload.artist)
    album = _clean(payload.album or "")
    duration_ms = _infer_int(payload.duration_ms, 0)
    art_url = _clean(payload.art_url or "")
    yt_video_id = _clean(getattr(payload, "yt_video_id", None) or "")

    # External I/O first (bounded): resolve against Subsonic only when configured.
    song = await _try_match_subsonic_song(settings, title=title, artist=artist, duration_ms=duration_ms or None)

    db = SessionLocal()
    try:
        # DB burst: clear queue + write new item + update playback session
        _clear_queue(db, user.id, settings=settings, log_current=True, played_ms=0)

        item = QueueItem(
            session_user_id=user.id,
            position=0,
            kind="song",
            title=title,
            artist=artist,
            album=album,
            duration_ms=duration_ms or 0,
            art_url=art_url,
        )

        item.yt_video_id = yt_video_id

        if song and song.get("id"):
            item.source = "subsonic"
            item.subsonic_song_id = str(song.get("id"))
            item.is_playable = True
            item.error = ""
        else:
            # Mark as YT-backed (fulfillable) missing.
            item.source = "ytmusic"
            item.subsonic_song_id = ""
            item.is_playable = False
            item.error = "NOT_IN_LIBRARY"

        db.add(item)
        sess = _get_or_create_session(db, user.id)
        # Switching to an explicit queue clears station mode.
        sess.active_station_id = ""
        # Explicit queue playback should disable station autoplay.
        sess.autoplay_enabled = False
        sess.current_index = 0
        # We mark as playing even if currently missing; the stream endpoint will fulfill ASAP.
        sess.is_playing = True
        db.commit()

        return _changed_state(db=db, user=user)
    finally:
        db.close()


async def play_album(payload: PlayerPlayAlbumRequest, user: User = Depends(get_current_user)):
    """Play an album using the *same semantics as clicking a single track*.

    This endpoint must never hold a DB session across slow external calls.
    """
    settings = _load_settings_short()

    browse_id = _clean(payload.browse_id)
    if not browse_id:
        raise HTTPException(status_code=400, detail="browse_id is required")

    # External I/O first (bounded).
    full = await _ytmusic_album_full_with_timeout(
        browse_id,
        timeout_s=float(os.getenv("HELIX_YTMUSIC_ALBUM_TIMEOUT_S", "12")),
    )
    album_title = _clean(full.get("title") or "") or "(YouTube Music Album)"
    payload_album_art = _clean(payload.art_url or "")
    resolved_album_art = _clean((full.get("thumbnail_url") or "") if isinstance(full, dict) else "")
    album_art = _prefer_album_art(payload_album_art, resolved_album_art)
    tracks = full.get("tracks") or []
    LOG.warning(
        "[album-art-debug] play_album browse_id=%s payload_art=%r resolved_art=%r chosen_art=%r first_track_video_id=%r",
        browse_id,
        payload_album_art,
        resolved_album_art,
        album_art,
        _clean((tracks[0].get("video_id") or tracks[0].get("videoId") or "") if tracks else ""),
    )
    album_artist = _album_artist_default(full.get("artist") or "", getattr(payload, "artist", "") or "", (tracks[0].get("artist") or "") if tracks else "")
    if not tracks:
        raise HTTPException(status_code=404, detail="No tracks found for this album on YouTube Music.")

    # Resolve ONLY track 1 against Subsonic (bounded) so the response is fast.
    t0 = tracks[0]
    t0_title = _clean(t0.get("title") or "")
    t0_artist = album_artist
    t0_len_ms = _infer_int(t0.get("lengthMs"), 0) or (_infer_int(t0.get("duration_seconds"), 0) * 1000)
    t0_vid = _clean(t0.get("video_id") or "")
    t0_track_no = _infer_int(t0.get("pos"), 1) or 1
    if not t0_artist:
        t0_artist = _safe_artist(t0.get("artist") or "", "")

    song0 = await _try_match_subsonic_song(settings, title=t0_title, artist=t0_artist, duration_ms=t0_len_ms or None)

    # DB burst: clear + insert queue items.
    db = SessionLocal()
    try:
        _clear_queue(db, user.id, settings=settings, log_current=True, played_ms=0)

        queue_items: List[QueueItem] = []
        # Track 1 item
        qi0 = QueueItem(
            session_user_id=user.id,
            position=0,
            kind="albumtrack",
            title=t0_title,
            artist=t0_artist,
            album=album_title,
            duration_ms=t0_len_ms or 0,
            art_url=album_art,
        )
        qi0.track_no = t0_track_no
        qi0.yt_video_id = t0_vid
        if song0 and song0.get("id"):
            qi0.source = "subsonic"
            qi0.subsonic_song_id = str(song0.get("id"))
            qi0.is_playable = True
            qi0.error = ""
        else:
            qi0.source = "ytmusic"
            qi0.subsonic_song_id = ""
            qi0.is_playable = False
            qi0.error = "NOT_IN_LIBRARY"

        queue_items.append(qi0)

        # Remaining tracks (unresolved; fulfilled later by /stream / background filler).
        for i, t in enumerate(tracks[1:], start=1):
            title = _clean(t.get("title") or "")
            if not title:
                continue
            ln_ms = _infer_int(t.get("lengthMs"), 0) or (_infer_int(t.get("duration_seconds"), 0) * 1000)
            vid = _clean(t.get("video_id") or "")
            track_no = _infer_int(t.get("pos"), i + 1) or (i + 1)

            qi = QueueItem(
                session_user_id=user.id,
                position=i,
                kind="albumtrack",
                title=title,
                artist=album_artist,
                album=album_title,
                duration_ms=ln_ms or 0,
                art_url=album_art,
            )
            qi.track_no = track_no
            qi.yt_video_id = vid
            qi.source = "ytmusic"
            qi.subsonic_song_id = ""
            qi.is_playable = False
            qi.error = "NOT_IN_LIBRARY"
            queue_items.append(qi)

        for qi in queue_items:
            db.add(qi)

        sess = _get_or_create_session(db, user.id)
        sess.active_station_id = ""
        sess.autoplay_enabled = False
        sess.current_index = 0
        sess.is_playing = True
        db.commit()

        # Trigger background filler (bounded) without holding DB.
        # Best-effort: if it fails, playback still works via /stream fulfillment.
        try:
            asyncio.create_task(_background_fill_album_tracks(user.id))
        except Exception:
            LOG.exception("[album] failed to schedule background fill")

        return _changed_state(db=db, user=user)
    finally:
        db.close()



async def play_playlist(payload: PlayerPlayPlaylistRequest, user: User = Depends(get_current_user)):
    """Play a playlist as a single atomic operation (server-side expansion).

    The Android app previously implemented playlist playback by:
      1) calling /api/player/play/track for the first item, then
      2) calling /api/player/queue/append/track for the remaining items.

    That approach is fragile (partial failures yield a 1-track queue). This endpoint
    expands the playlist on the backend, clears the queue, and writes the full queue
    in one DB transaction so playback reliably advances beyond track 1.
    """
    settings = _load_settings_short()
    pid = _clean(payload.playlist_id)

    db = SessionLocal()
    try:
        # Resolve tracks from either a normal playlist or the system liked playlist.
        # The liked playlist may arrive as either the literal sentinel "liked" or as
        # the real Playlist UUID returned by /api/playlists. Treat both forms as the
        # same system playlist so the web UI can play the visible Liked Songs card.
        items: list[dict[str, Any]] = []

        playlist: Playlist | None = None
        is_liked_playlist = pid == "liked"

        if not is_liked_playlist:
            playlist = (
                db.execute(select(Playlist).where(Playlist.id == pid, Playlist.user_id == user.id))
                .scalar_one_or_none()
            )
            if not playlist:
                raise HTTPException(status_code=404, detail="Playlist not found")
            is_liked_playlist = (playlist.system_key or "") == "liked"

        if is_liked_playlist:
            rows = (
                db.execute(
                    select(LikedTrack)
                    .where(LikedTrack.user_id == user.id)
                    .order_by(LikedTrack.created_at.desc())
                    .limit(5000)
                )
                .scalars()
                .all()
            )
            recovered_any = False
            for r in rows:
                subsonic_song_id = _clean(r.subsonic_song_id or "")
                yt_video_id = _clean(r.yt_video_id or "") or _yt_video_id_from_key(getattr(r, "key", ""))
                is_stale_subsonic = bool(getattr(r, "stale_subsonic", False))
                source = _clean(r.source or "")

                if yt_video_id and not _clean(r.yt_video_id or ""):
                    r.yt_video_id = yt_video_id
                    recovered_any = True
                if is_stale_subsonic and yt_video_id:
                    # This liked song was originally Subsonic-backed, but that item/file
                    # has gone stale. Queue it as YTMusic-backed while preserving the old
                    # subsonic_song_id on the liked row for history.
                    source = "ytmusic"
                    if r.source != "ytmusic":
                        r.source = "ytmusic"
                        recovered_any = True
                elif not source:
                    source = "subsonic" if subsonic_song_id else ("ytmusic" if yt_video_id else "")
                    if source:
                        r.source = source
                        recovered_any = True

                items.append(
                    {
                        "title": r.title or "",
                        "artist": r.artist or "",
                        "album": r.album or "",
                        "duration_ms": int(r.duration_ms or 0),
                        "art_url": r.art_url or "",
                        "source": source,
                        "subsonic_song_id": subsonic_song_id,
                        "yt_video_id": yt_video_id,
                        "yt_browse_id": r.yt_browse_id or "",
                        "mb_recording_id": r.mb_recording_id or "",
                        "mb_artist_id": r.mb_artist_id or "",
                    }
                )
            if recovered_any:
                db.commit()
        else:
            assert playlist is not None
            rows = (
                db.execute(
                    select(PlaylistTrack)
                    .where(PlaylistTrack.playlist_id == playlist.id, PlaylistTrack.user_id == user.id)
                    .order_by(PlaylistTrack.position.asc(), PlaylistTrack.created_at.asc())
                )
                .scalars()
                .all()
            )
            recovered_any = False
            for r in rows:
                subsonic_song_id = _clean(r.subsonic_song_id or "")
                yt_video_id = _clean(r.yt_video_id or "") or _yt_video_id_from_key(getattr(r, "key", ""))
                is_stale_subsonic = bool(getattr(r, "stale_subsonic", False))
                source = _clean(r.source or "")

                if yt_video_id and not _clean(r.yt_video_id or ""):
                    r.yt_video_id = yt_video_id
                    recovered_any = True
                if is_stale_subsonic and yt_video_id:
                    source = "ytmusic"
                    if r.source != "ytmusic":
                        r.source = "ytmusic"
                        recovered_any = True
                elif not source:
                    source = "subsonic" if subsonic_song_id else ("ytmusic" if yt_video_id else "")
                    if source:
                        r.source = source
                        recovered_any = True

                items.append(
                    {
                        "title": r.title or "",
                        "artist": r.artist or "",
                        "album": r.album or "",
                        "duration_ms": int(r.duration_ms or 0),
                        "art_url": r.art_url or "",
                        "source": source,
                        "subsonic_song_id": subsonic_song_id,
                        "yt_video_id": yt_video_id,
                        "yt_browse_id": r.yt_browse_id or "",
                        "mb_recording_id": r.mb_recording_id or "",
                        "mb_artist_id": r.mb_artist_id or "",
                    }
                )
            if recovered_any:
                db.commit()

        if not items:
            raise HTTPException(status_code=400, detail="Playlist is empty")

        if bool(getattr(payload, "shuffle", False)) and len(items) > 1:
            random.SystemRandom().shuffle(items)

        # Clear the existing queue and write the full expanded playlist.
        _clear_queue(db, user.id, settings=settings, log_current=True, played_ms=0)

        for idx, it in enumerate(items):
            qi = QueueItem(
                session_user_id=user.id,
                position=idx,
                kind="song",
                title=_clean(it.get("title") or ""),
                artist=_clean(it.get("artist") or ""),
                album=_clean(it.get("album") or ""),
                duration_ms=_infer_int(it.get("duration_ms"), 0) or 0,
                art_url=_clean(it.get("art_url") or ""),
            )

            qi.source = _clean(it.get("source") or "") or "ytmusic"

            qi.subsonic_song_id = _clean(it.get("subsonic_song_id") or "")
            qi.yt_video_id = _clean(it.get("yt_video_id") or "")
            qi.yt_browse_id = _clean(it.get("yt_browse_id") or "")

            qi.mb_recording_id = _clean(it.get("mb_recording_id") or "")
            qi.mb_artist_id = _clean(it.get("mb_artist_id") or "")

            # Mark playable when we have a live Subsonic library id. Stale liked
            # rows may still preserve subsonic_song_id but intentionally queue as
            # YTMusic so stream fulfillment uses the recovered temporary-download path.
            if qi.subsonic_song_id and qi.source == "subsonic":
                qi.is_playable = True
                qi.error = ""
            else:
                qi.is_playable = False
                qi.error = "NOT_IN_LIBRARY"
                qi.source = "ytmusic"

            db.add(qi)

        sess = _get_or_create_session(db, user.id)
        sess.active_station_id = ""
        sess.autoplay_enabled = False
        sess.current_index = 0
        sess.is_playing = True

        db.commit()
        return _changed_state(db=db, user=user)
    finally:
        db.close()


async def queue_append_track(payload: PlayerQueueAppendTrackRequest, user: User = Depends(get_current_user)):
    settings = _load_settings_short()

    title = _clean(payload.title)
    artist = _clean(payload.artist)
    album = _clean(payload.album or "")
    duration_ms = _infer_int(payload.duration_ms, 0)
    art_url = _clean(payload.art_url or "")
    yt_video_id = _clean(getattr(payload, "yt_video_id", None) or "")

    # External I/O first (bounded): resolve against Subsonic only when configured.
    song = await _try_match_subsonic_song(settings, title=title, artist=artist, duration_ms=duration_ms or None)

    db = SessionLocal()
    try:
        item = QueueItem(
            kind="song",
            title=title,
            artist=artist,
            album=album,
            duration_ms=duration_ms or 0,
            art_url=art_url,
        )
        item.yt_video_id = yt_video_id

        if song and song.get("id"):
            item.source = "subsonic"
            item.subsonic_song_id = str(song.get("id"))
            item.is_playable = True
            item.error = ""
        else:
            item.source = "ytmusic"
            item.subsonic_song_id = ""
            item.is_playable = False
            item.error = "NOT_IN_LIBRARY"

        if queue_add_position_for_user(db, user.id) == "next":
            _insert_queue_items_after_current_with_sqlite_lock(db, user.id, [item])
        else:
            _append_queue_items_with_sqlite_lock(db, user.id, [item])
        return _changed_state(db=db, user=user)
    finally:
        db.close()


async def queue_append_album(payload: PlayerQueueAppendAlbumRequest, user: User = Depends(get_current_user)):
    settings = _load_settings_short()
    browse_id = _clean(payload.browse_id)
    if not browse_id:
        raise HTTPException(status_code=400, detail="browse_id is required")

    full = await _ytmusic_album_full_with_timeout(
        browse_id,
        timeout_s=float(os.getenv("HELIX_YTMUSIC_ALBUM_TIMEOUT_S", "12")),
    )
    album_title = _clean(full.get("title") or "") or "(YouTube Music Album)"
    album_art = _prefer_album_art(_clean(payload.art_url or ""), _clean((full.get("thumbnail_url") or "") if isinstance(full, dict) else ""))
    tracks = full.get("tracks") or []
    album_artist = _album_artist_default(full.get("artist") or "", getattr(payload, "artist", "") or "", (tracks[0].get("artist") or "") if tracks else "")
    if not tracks:
        raise HTTPException(status_code=404, detail="No tracks found for this album on YouTube Music.")

    db = SessionLocal()
    try:
        items_to_add: list[QueueItem] = []

        for i, t in enumerate(tracks):
            title = _clean(t.get("title") or "")
            if not title:
                continue
            ln_ms = _infer_int(t.get("lengthMs"), 0) or (_infer_int(t.get("duration_seconds"), 0) * 1000)
            vid = _clean(t.get("video_id") or "")
            track_no = _infer_int(t.get("pos"), i + 1) or (i + 1)

            qi = QueueItem(
                kind="albumtrack",
                title=title,
                artist=album_artist,
                album=album_title,
                duration_ms=ln_ms or 0,
                art_url=album_art,
            )
            qi.track_no = track_no
            qi.yt_video_id = vid
            qi.source = "ytmusic"
            qi.subsonic_song_id = ""
            qi.is_playable = False
            qi.error = "NOT_IN_LIBRARY"
            items_to_add.append(qi)

        if not items_to_add:
            raise HTTPException(status_code=404, detail="No playable tracks found for this album on YouTube Music.")

        if queue_add_position_for_user(db, user.id) == "next":
            _insert_queue_items_after_current_with_sqlite_lock(db, user.id, items_to_add)
        else:
            _append_queue_items_with_sqlite_lock(db, user.id, items_to_add)
        return _changed_state(db=db, user=user)
    finally:
        db.close()



def queue_clear(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Clear the user's main queue and stop station playback/autoplay.

    This is used by the queue panel Clear button. If a station is active, clearing
    the queue must also leave station mode so prefetch does not immediately refill
    the queue.
    """
    settings = get_settings(db)
    sess = _get_or_create_session(db, user.id)
    items = db.execute(
        select(QueueItem)
        .where(QueueItem.session_user_id == user.id)
        .order_by(QueueItem.position.asc())
    ).scalars().all()
    cur = items[sess.current_index] if 0 <= int(sess.current_index or 0) < len(items) else None
    if cur:
        _push_history(db, user.id, cur, event="skipped", reason="cleared_queue", played_ms=0, settings=settings)

    db.execute(delete(QueueItem).where(QueueItem.session_user_id == user.id))
    sess.current_index = 0
    sess.is_playing = False
    sess.autoplay_enabled = False
    sess.active_station_id = ""
    db.commit()
    return _changed_state(db=db, user=user)


def queue_remove_item(queue_item_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    settings = get_settings(db)
    sess = _get_or_create_session(db, user.id)
    items = db.execute(select(QueueItem).where(QueueItem.session_user_id == user.id).order_by(QueueItem.position.asc())).scalars().all()
    idx = next((i for i, it in enumerate(items) if it.id == queue_item_id), None)
    if idx is None:
        return PlayerRemoveQueueItemResponse(ok=True)

    # If removing currently playing item, log as skipped and then advance to next item (same index after removal).
    if idx == sess.current_index:
        _push_history(db, user.id, items[idx], event="skipped", reason="removed_current", played_ms=0, settings=settings)

    db.execute(delete(QueueItem).where(QueueItem.id == queue_item_id))
    db.commit()

    # Re-fetch and reindex positions
    items2 = db.execute(select(QueueItem).where(QueueItem.session_user_id == user.id).order_by(QueueItem.position.asc())).scalars().all()
    for p, it in enumerate(items2):
        it.position = p
    db.commit()

    if idx < sess.current_index:
        sess.current_index = max(0, sess.current_index - 1)
    elif idx == sess.current_index:
        # Keep current_index as-is; it now points at the next song that slid into this slot.
        if sess.current_index >= len(items2):
            sess.current_index = max(0, len(items2) - 1)
            sess.is_playing = False
    db.commit()
    from ..realtime import schedule_player_state_broadcast
    schedule_player_state_broadcast(user.id)
    return PlayerRemoveQueueItemResponse(ok=True)



def queue_reorder(payload: PlayerQueueReorderRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Reorder the queue while keeping playback anchored to the current item.

    The client may reorder items on either side of the currently-playing track.
    Playback stays attached to the same QueueItem id, and current_index is
    recalculated after the reorder. Items appended concurrently (for example by
    station prefetch) are preserved at the end if they were not present in the
    client's payload.
    """
    sess = _get_or_create_session(db, user.id)

    requested_ids: list[str] = []
    seen: set[str] = set()
    for raw_id in getattr(payload, "item_ids", []) or []:
        item_id = _clean(raw_id)
        if not item_id or item_id in seen:
            continue
        requested_ids.append(item_id)
        seen.add(item_id)

    if not requested_ids:
        raise HTTPException(status_code=400, detail="Reorder payload must include queue item ids")

    try:
        db.rollback()
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")

        rows = db.execute(
            select(QueueItem)
            .where(QueueItem.session_user_id == user.id)
            .order_by(QueueItem.position.asc(), QueueItem.created_at.asc())
        ).scalars().all()

        if not rows:
            db.rollback()
            return _changed_state(db=db, user=user)

        by_id = {row.id: row for row in rows}
        unknown = [item_id for item_id in requested_ids if item_id not in by_id]
        if unknown:
            db.rollback()
            raise HTTPException(status_code=400, detail="Reorder payload contains an unknown queue item")

        current_index = max(0, min(int(sess.current_index or 0), len(rows) - 1))
        current_row = rows[current_index]

        requested_rows = [by_id[item_id] for item_id in requested_ids]
        requested_row_ids = {row.id for row in requested_rows}

        # Preserve anything appended after the client began dragging. Those
        # items were not in the submitted list, so retain them at the end rather
        # than dropping them from the queue.
        ordered_rows = requested_rows + [row for row in rows if row.id not in requested_row_ids]

        # QueueItem has UNIQUE(session_user_id, position), so first move every
        # row to a collision-free range before assigning contiguous positions.
        temporary_base = 1_000_000 + len(rows)
        for offset, row in enumerate(rows):
            row.position = temporary_base + offset
            db.add(row)
        db.flush()

        for position, row in enumerate(ordered_rows):
            row.position = position
            db.add(row)

        # The playing track remains the same queue item even if surrounding
        # entries cross from one side of it to the other.
        sess.current_index = next(
            (index for index, row in enumerate(ordered_rows) if row.id == current_row.id),
            current_index,
        )
        db.add(sess)
        db.commit()
        db.expire_all()

        # Verify the database really contains the requested order. A failed
        # persistence must be visible to the client instead of silently looking
        # like a successful drag followed by a snap-back.
        persisted = db.execute(
            select(QueueItem)
            .where(QueueItem.session_user_id == user.id)
            .order_by(QueueItem.position.asc(), QueueItem.created_at.asc())
        ).scalars().all()
        if [row.id for row in persisted] != [row.id for row in ordered_rows]:
            raise HTTPException(status_code=500, detail="Queue reorder did not persist")

    except HTTPException:
        raise
    except (IntegrityError, OperationalError) as exc:
        db.rollback()
        if isinstance(exc, OperationalError) and "database is locked" in str(exc).lower():
            raise HTTPException(status_code=503, detail="Queue is busy. Please try again.") from exc
        raise

    return _changed_state(db=db, user=user)

def history(
    station_id: str | None = None,
    q: str | None = None,
    artist: str | None = None,
    album: str | None = None,
    source: str | None = None,
    event: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    limit = max(1, min(250, int(limit or 100)))
    offset = max(0, int(offset or 0))

    filters = [ListenHistoryItem.user_id == user.id]
    if station_id is not None:
        station_value = str(station_id).strip()
        if station_value == "__none__":
            filters.append(ListenHistoryItem.station_id == "")
        else:
            filters.append(ListenHistoryItem.station_id == station_value)

    search = str(q or "").strip()
    if search:
        pattern = f"%{search}%"
        filters.append(or_(
            ListenHistoryItem.title.ilike(pattern),
            ListenHistoryItem.artist.ilike(pattern),
            ListenHistoryItem.album.ilike(pattern),
        ))

    artist_value = str(artist or "").strip()
    if artist_value:
        filters.append(ListenHistoryItem.artist.ilike(f"%{artist_value}%"))

    album_value = str(album or "").strip()
    if album_value:
        filters.append(ListenHistoryItem.album.ilike(f"%{album_value}%"))

    source_value = str(source or "").strip().lower()
    if source_value:
        if source_value == "ytmusic":
            filters.append(func.lower(ListenHistoryItem.source).in_(["ytmusic", "youtube"]))
        else:
            filters.append(func.lower(ListenHistoryItem.source) == source_value)

    event_value = str(event or "").strip().lower()
    if event_value:
        filters.append(func.lower(ListenHistoryItem.event) == event_value)

    start = _parse_history_datetime(date_from)
    end = _parse_history_datetime(date_to, end_of_day=True)
    if start is not None:
        filters.append(ListenHistoryItem.created_at >= start)
    if end is not None:
        filters.append(ListenHistoryItem.created_at < end)

    total = int(db.execute(select(func.count(ListenHistoryItem.id)).where(*filters)).scalar_one() or 0)
    items = db.execute(
        select(ListenHistoryItem)
        .where(*filters)
        .order_by(ListenHistoryItem.created_at.desc())
        .offset(offset)
        .limit(limit)
    ).scalars().all()
    return PlayerHistoryResponse(
        limit=limit,
        offset=offset,
        total=total,
        has_more=(offset + len(items)) < total,
        items=[_to_history(h) for h in items],
    )


def history_set_limit(payload: Dict[str, Any], db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    # Backward-compatible endpoint: old clients used this to control both display
    # length and retention. It now controls retention only; API display is paginated.
    try:
        retention = int(payload.get("limit") or payload.get("retention") or 10000)
    except Exception:
        retention = 10000
    retention = max(0, min(50000, retention))
    from ..settings_store import patch_settings
    patch_settings(db, {"listen_history_retention": retention})
    return history(limit=100, offset=0, db=db, user=user)


async def ended(payload: Optional[PlayerActionRequest] = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    settings = get_settings(db)
    sess = _get_or_create_session(db, user.id)
    items = db.execute(select(QueueItem).where(QueueItem.session_user_id == user.id).order_by(QueueItem.position.asc())).scalars().all()
    cur = items[sess.current_index] if 0 <= sess.current_index < len(items) else None
    played_ms = 0
    if payload and payload.position_ms is not None:
        played_ms = int(payload.position_ms or 0)
    _push_history(db, user.id, cur, event="completed", reason="ended", played_ms=played_ms, settings=settings)

    # advance
    if sess.current_index + 1 < len(items):
        sess.current_index += 1
        sess.is_playing = True
        db.commit()
        return _changed_state(db=db, user=user)

    # End of queue: optionally autoplay from the active station.
    sess.is_playing = False
    db.commit()

    if bool(getattr(sess, "autoplay_enabled", True)) and (getattr(sess, "active_station_id", "") or ""):
        try:
            await generate_and_append_station_track(
                user.id,
                str(getattr(sess, "active_station_id", "") or ""),
                settings=settings,
                advance_to_new_item=True,
            )
        except Exception as e:
            LOG.warning("autoplay append failed: %s", e)
    return _changed_state(db=db, user=user)


def jump_to(payload: PlayerJumpRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    settings = get_settings(db)
    sess = _get_or_create_session(db, user.id)
    items = db.execute(select(QueueItem).where(QueueItem.session_user_id == user.id).order_by(QueueItem.position.asc())).scalars().all()
    cur = items[sess.current_index] if 0 <= sess.current_index < len(items) else None
    _push_history(db, user.id, cur, event="skipped", reason="jump", played_ms=0, settings=settings)

    if not items:
        raise HTTPException(status_code=400, detail="Queue is empty.")
    idx = int(payload.index)
    if idx < 0 or idx >= len(items):
        raise HTTPException(status_code=400, detail="Index out of range.")
    sess.current_index = idx
    sess.is_playing = _can_play(items[idx])
    db.commit()
    return _changed_state(db=db, user=user)


async def replay_from_history(payload: PlayerReplayRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Replay a song from listen history.

    Behavior:
      - current track is marked skipped (reason=replay)
      - the selected history track is inserted to play next (front-of-queue)
      - playback advances immediately to the inserted item
    """
    settings = get_settings(db)
    sess = _get_or_create_session(db, user.id)
    items = db.execute(select(QueueItem).where(QueueItem.session_user_id == user.id).order_by(QueueItem.position.asc())).scalars().all()
    cur = items[sess.current_index] if 0 <= sess.current_index < len(items) else None
    played_ms = int(payload.position_ms or 0) if payload and payload.position_ms is not None else 0
    _push_history(db, user.id, cur, event="skipped", reason="replay", played_ms=played_ms, settings=settings)

    hid = (payload.history_id or "").strip()
    if not hid:
        raise HTTPException(status_code=400, detail="history_id required")
    h = db.get(ListenHistoryItem, hid)
    if not h or h.user_id != user.id:
        raise HTTPException(status_code=404, detail="History item not found")

    # Determine insert position (play next).
    insert_idx = 0
    if items and 0 <= sess.current_index < len(items):
        insert_idx = sess.current_index + 1

    # Shift positions for existing items after insert_idx.
    for i, it in enumerate(items):
        if i >= insert_idx:
            it.position = int(it.position or 0) + 1

    qi = QueueItem(
        session_user_id=user.id,
        position=int(insert_idx),
        title=h.title,
        artist=h.artist,
        album=h.album or "",
        duration_ms=int(h.duration_ms or 0),
        art_url=h.art_url or "",
        source=h.source or "subsonic",
        subsonic_song_id=getattr(h, "subsonic_song_id", "") or "",
        yt_video_id=getattr(h, "yt_video_id", "") or "",
        yt_browse_id=getattr(h, "yt_browse_id", "") or "",
        mb_recording_id=getattr(h, "mb_recording_id", "") or "",
        mb_artist_id=getattr(h, "mb_artist_id", "") or "",
        is_playable=bool(getattr(h, "subsonic_song_id", "")) or bool(getattr(h, "yt_video_id", "")),
        error="",
    )
    db.add(qi)

    # Advance immediately to the inserted item.
    sess.current_index = insert_idx
    sess.is_playing = _can_play(qi)

    db.commit()
    return _changed_state(db=db, user=user)

async def next_track(payload: Optional[PlayerActionRequest] = None, user: User = Depends(get_current_user)):
    # DB burst: advance index, snapshot autoplay inputs
    db = SessionLocal()
    try:
        settings = get_settings(db)
        sess = _get_or_create_session(db, user.id)
        items = db.execute(select(QueueItem).where(QueueItem.session_user_id == user.id).order_by(QueueItem.position.asc())).scalars().all()
        cur = items[sess.current_index] if 0 <= sess.current_index < len(items) else None
        played_ms = int(payload.position_ms or 0) if payload and payload.position_ms is not None else 0
        _push_history(db, user.id, cur, event="skipped", reason="next", played_ms=played_ms, settings=settings)

        if not items:
            sess.is_playing = False
            sess.current_index = 0
            db.commit()
            return _changed_state(db=db, user=user)

        active_station_id = str(getattr(sess, "active_station_id", "") or "")
        autoplay_enabled = bool(getattr(sess, "autoplay_enabled", True))

        if sess.current_index < len(items) - 1:
            sess.current_index += 1
            sess.is_playing = _can_play(items[sess.current_index])
            db.commit()
            return _changed_state(db=db, user=user)

        # End of queue
        sess.is_playing = False
        db.commit()
    finally:
        db.close()

    # External I/O: station autoplay without holding DB.
    if autoplay_enabled and active_station_id:
        try:
            await generate_and_append_station_track(
                user.id,
                active_station_id,
                settings=settings,
                advance_to_new_item=True,
            )
        except Exception as e:
            LOG.warning("autoplay append failed: %s", e)

    db = SessionLocal()
    try:
        return _changed_state(db=db, user=user)
    finally:
        db.close()


def prev_track(payload: Optional[PlayerActionRequest] = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    settings = get_settings(db)
    sess = _get_or_create_session(db, user.id)
    items = db.execute(select(QueueItem).where(QueueItem.session_user_id == user.id).order_by(QueueItem.position.asc())).scalars().all()
    cur = items[sess.current_index] if 0 <= sess.current_index < len(items) else None
    played_ms = int(payload.position_ms or 0) if payload and payload.position_ms is not None else 0
    _push_history(db, user.id, cur, event="skipped", reason="prev", played_ms=played_ms, settings=settings)
    if not items:
        sess.is_playing = False
        sess.current_index = 0
        db.commit()
        return _changed_state(db=db, user=user)

    if sess.current_index > 0:
        sess.current_index -= 1
    sess.is_playing = _can_play(items[sess.current_index])
    db.commit()
    return _changed_state(db=db, user=user)


def pause(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    sess = _get_or_create_session(db, user.id)
    sess.is_playing = False
    db.commit()
    return _changed_state(db=db, user=user)


def resume(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    sess = _get_or_create_session(db, user.id)
    items = db.execute(select(QueueItem).where(QueueItem.session_user_id == user.id).order_by(QueueItem.position.asc())).scalars().all()
    if not items:
        raise HTTPException(status_code=400, detail="Queue is empty.")
    if not _can_play(items[sess.current_index]):
        raise HTTPException(status_code=400, detail="Current queue item is not playable.")
    sess.is_playing = True
    db.commit()
    return _changed_state(db=db, user=user)


def _stream_queue_item_snapshot(cur: QueueItem) -> SimpleNamespace:
    """Copy queue item fields needed by stream generators before closing DB.

    FastAPI cleans dependency sessions up after the whole response completes. Audio
    responses can remain open for a long time, so stream endpoints must not return
    a StreamingResponse that still references a request-scoped SQLAlchemy session or
    ORM object. This snapshot is intentionally plain data.
    """
    return SimpleNamespace(
        id=cur.id,
        source=cur.source,
        title=cur.title,
        artist=cur.artist,
        album=cur.album,
        duration_ms=cur.duration_ms,
        position=cur.position,
        is_playable=cur.is_playable,
        yt_video_id=cur.yt_video_id,
        yt_browse_id=getattr(cur, "yt_browse_id", ""),
        subsonic_song_id=cur.subsonic_song_id,
        inbound_path=cur.inbound_path,
        download_status=cur.download_status,
        error=cur.error,
        art_url=cur.art_url,
    )


async def stream_item(
    queue_item_id: str,
    request: Request,
    user: User = Depends(get_current_user),
):
    """Stream audio for a queue item.

    Keep database work short-lived. Audio responses and upstream Subsonic
    requests can stay open for a long time, so this endpoint snapshots the queue
    item and closes the SQLAlchemy session before returning/creating a streaming
    response. If a stale Subsonic item needs fallback recovery, a fresh short
    session is opened only for that recovery work.
    """
    settings: dict = {}
    cur_snapshot: SimpleNamespace | None = None
    source = ""

    db = SessionLocal()
    try:
        settings = dict(get_settings(db) or {})

        cur = db.execute(
            select(QueueItem)
            .where(QueueItem.session_user_id == user.id, QueueItem.id == queue_item_id)
        ).scalar_one_or_none()
        if not cur:
            raise HTTPException(status_code=404, detail="Queue item not found.")

        sess = db.get(PlaybackSession, user.id)
        _maybe_prefetch_station(user_id=user.id, sess=sess, cur=cur)

        # Best-effort: fill missing yt id, repair meta.
        await _maybe_lazy_resolve_yt_id(db, cur)
        await _maybe_repair_from_mb_recording(db, cur)

        # Ensure the item is playable (Subsonic id or inbound file) *before* streaming.
        # For stream requests, do not block on Subsonic scanning/polling. If not found quickly,
        # start inbound download and stream progressively.
        await _ensure_playable_for_stream(db, cur, settings=settings, allow_subsonic_wait=False, progressive_inbound=HELIX_PROGRESSIVE_STREAMING)

        inbound_exists = bool((cur.inbound_path or "").strip()) and os.path.exists(cur.inbound_path)
        LOG.warning(
            "[stream-debug] id=%s source=%s playable=%s yt=%s sub=%s inbound_path=%r exists=%s status=%s error=%s",
            cur.id,
            cur.source,
            cur.is_playable,
            cur.yt_video_id,
            cur.subsonic_song_id,
            cur.inbound_path,
            inbound_exists,
            cur.download_status,
            cur.error,
        )

        source = cur.source or ""
        cur_snapshot = _stream_queue_item_snapshot(cur)
    finally:
        db.close()

    if cur_snapshot is None:
        raise HTTPException(status_code=404, detail="Queue item not found.")

    if source != "subsonic":
        # If the file is still downloading (often endswith .part) use progressive streaming.
        if (cur_snapshot.inbound_path or "").endswith('.part'):
            return _stream_inbound_progressive(request, cur_snapshot)
        return await _stream_inbound_with_range(request, cur_snapshot)

    try:
        return await _stream_subsonic_with_range(request, cur_snapshot, settings=settings)
    except HTTPException as exc:
        # Liked songs can point at Subsonic items that existed when they were liked
        # but were later deleted from Navidrome/the filesystem. Treat that as a stale
        # Subsonic reference, recover/persist a YTMusic id on the liked row, then
        # fulfill this playback as a temporary download. Keep this recovery DB session
        # short too, then stream from a plain snapshot.
        if exc.status_code not in {404, 409, 410, 415, 422, 502, 503}:
            raise

        db = SessionLocal()
        try:
            cur = db.execute(
                select(QueueItem)
                .where(QueueItem.session_user_id == user.id, QueueItem.id == queue_item_id)
            ).scalar_one_or_none()
            if not cur:
                raise HTTPException(status_code=404, detail="Queue item not found.")

            recovered = await _recover_stale_subsonic_liked_track(db, user.id, cur)
            if not recovered:
                recovered = await _recover_stale_subsonic_playlist_track(db, user.id, cur)
            if not recovered:
                raise exc

            await _ensure_playable_for_stream(db, cur, settings=settings, allow_subsonic_wait=False, progressive_inbound=HELIX_PROGRESSIVE_STREAMING)
            cur_snapshot = _stream_queue_item_snapshot(cur)
        finally:
            db.close()

        if (cur_snapshot.inbound_path or "").endswith('.part'):
            return _stream_inbound_progressive(request, cur_snapshot)
        return await _stream_inbound_with_range(request, cur_snapshot)





async def _ensure_station_prefetch_task(user_id: str, station_id: str) -> None:
    """Atomically ensure exactly one station-prefetch task per user is running.

    This avoids a check-then-set race where concurrent stream requests schedule multiple prefetch tasks,
    which can overfill the queue ahead window.
    """
    lock = _STATION_PREFETCH_LOCKS.setdefault(user_id, asyncio.Lock())
    async with lock:
        t = _STATION_PREFETCH_TASKS.get(user_id)
        if t and not t.done():
            return
        _STATION_PREFETCH_TASKS[user_id] = asyncio.create_task(_prefetch_next_station_item(user_id, station_id))

def _maybe_prefetch_station(*, user_id: str, sess: PlaybackSession | None, cur: QueueItem) -> None:
    """While a station track is playing, prefetch the next pick in the background."""
    try:
        if not sess or not sess.is_playing or not sess.active_station_id:
            return
        if cur.position != int(sess.current_index or 0):
            return
        # Schedule an atomic task-ensure to avoid racing concurrent stream requests.
        asyncio.create_task(_ensure_station_prefetch_task(user_id, sess.active_station_id))
    except Exception:
        return


async def _maybe_lazy_resolve_yt_id(db: Session, cur: QueueItem) -> None:
    """If we don't have a yt_video_id for a non-Subsonic item, try to find one lazily."""
    if _clean(getattr(cur, "yt_video_id", "") or ""):
        return
    if cur.source == "subsonic" and cur.subsonic_song_id:
        return
    if cur.is_playable and cur.subsonic_song_id:
        return

    try:
        want_dur_s = int((cur.duration_ms or 0) / 1000) if (cur.duration_ms or 0) else None
    except Exception:
        want_dur_s = None

    try:
        r = find_track(
            title=cur.title or "",
            artist=cur.artist or "",
            album=cur.album or None,
            duration_seconds=want_dur_s,
            limit=9,
        )
        if r.found and r.video_id:
            cur.yt_video_id = r.video_id
            if not _clean(getattr(cur, "art_url", "") or ""):
                cur.art_url = f"https://i.ytimg.com/vi/{r.video_id}/hqdefault.jpg"
            db.commit()
            LOG.info(
                "[stream] resolved yt id lazily vid=%s conf=%.2f title=%r artist=%r",
                r.video_id,
                r.confidence,
                cur.title,
                cur.artist,
            )
    except Exception as e:
        LOG.warning("[stream] lazy yt id search failed: %r", e)


async def _maybe_repair_from_mb_recording(db: Session, cur: QueueItem) -> None:
    """Best-effort: enrich metadata via MusicBrainz recording id."""
    try:
        mbid = _clean(getattr(cur, "mb_recording_id", "") or "")
        if not mbid:
            return
        rec = await lookup_recording_full(mbid)
        t_title, t_artist, t_album, t_dur_ms, t_year, t_rel = simplify_recording(rec)
        changed = False
        if t_title and t_title != (cur.title or ""):
            cur.title = t_title
            changed = True
        if t_artist and t_artist != (cur.artist or ""):
            cur.artist = t_artist
            changed = True
        if t_album and t_album != (cur.album or ""):
            cur.album = t_album
            changed = True
        if t_dur_ms and (not cur.duration_ms or cur.duration_ms <= 0):
            cur.duration_ms = int(t_dur_ms)
            changed = True
        if changed:
            db.commit()
    except Exception:
        return


async def _ensure_playable_for_stream(db: Session, cur: QueueItem, *, settings: dict, allow_subsonic_wait: bool = True, progressive_inbound: bool = False) -> None:
    """Resolve to a Subsonic track or ensure inbound file exists."""

    # Already playable via Subsonic.
    if cur.source == "subsonic" and cur.subsonic_song_id:
        return

    # Already playable inbound.
    if cur.source != "subsonic" and cur.inbound_path and os.path.exists(cur.inbound_path):
        # Older progressive stream requests could leave a completed/cached file marked
        # as DOWNLOADING. Only real .part files should be treated as progressive;
        # finalized/cache files need normal Range support so refresh/seek resumes work.
        if not str(cur.inbound_path or "").endswith(".part") and getattr(cur, "download_status", "") == "DOWNLOADING":
            cur.download_status = "DOWNLOADED"
            try:
                db.commit()
            except Exception:
                db.rollback()
        return

    vid = _clean(getattr(cur, "yt_video_id", "") or "")

    # Repair metadata if needed (structured YT album metadata). Only possible when a
    # YouTube Music video id is known; otherwise we still try Subsonic by metadata below.
    if vid and ((not _clean(cur.artist or "") or _looks_like_views(cur.artist)) or (not _clean(cur.album or ""))):
        try:
            _repair_from_album_full(cur)
            db.commit()
        except Exception as e:
            LOG.warning("[stream] metadata repair failed: %r", e)

    # Try Subsonic lookup before requiring a YouTube id. This is important for old
    # liked-playlist rows that may have title/artist metadata but no stored source id.
    song = None
    client = None
    try:
        client = await _subsonic_client_from_settings(settings)
        t_title_n, t_artist_n, _ = _norm_for_subsonic(cur.title or "", cur.artist or "", cur.album or "")
        song = await client.search_song_best(
            title=t_title_n,
            artist=t_artist_n,
            duration_ms=int(cur.duration_ms or 0) or None,
        )
        if (not song) and allow_subsonic_wait:
            try:
                await client.start_scan()
                song = await client.wait_for_song_best(
                    title=t_title_n,
                    artist=t_artist_n,
                    duration_ms=int(cur.duration_ms or 0) or None,
                    timeout_s=10,
                    poll_s=2.0,
                )
            except Exception:
                pass
    except Exception as e:
        LOG.warning("[stream] subsonic lookup error: %r", e)
        song = None
    finally:
        if client:
            try:
                await client.close()
            except Exception:
                pass

    if song and song.get("id"):
        cur.source = "subsonic"
        cur.subsonic_song_id = str(song.get("id"))
        cur.is_playable = True
        cur.error = ""
        db.commit()
        from ..realtime import schedule_player_state_broadcast
        schedule_player_state_broadcast(cur.session_user_id)
        return

    # If the queue item came from an old liked-song row without a stored YT id,
    # try one final YTMusic lookup by metadata before failing. This keeps liked
    # tracks playable even when they were never explicitly added to Subsonic.
    if not vid:
        try:
            want_dur_s = int((cur.duration_ms or 0) / 1000) if (cur.duration_ms or 0) else None
            found = await asyncio.to_thread(
                _find_track_safe,
                title=cur.title or "",
                artist=cur.artist or "",
                album=cur.album or None,
                duration_seconds=want_dur_s,
            )
            if getattr(found, "found", False) and getattr(found, "video_id", ""):
                candidate = str(found.video_id or "").strip()
                if is_valid_yt_video_id(candidate):
                    vid = candidate
                    cur.yt_video_id = vid
                    cur.source = "ytmusic"
                    if not _clean(getattr(cur, "art_url", "") or ""):
                        cur.art_url = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
                    db.commit()
                    LOG.info(
                        "[stream] recovered missing liked-song yt id vid=%s conf=%.2f title=%r artist=%r",
                        vid,
                        float(getattr(found, "confidence", 0.0) or 0.0),
                        cur.title,
                        cur.artist,
                    )
        except Exception as e:
            LOG.warning("[stream] fallback yt lookup for missing source failed: %r", e)

    # If we still have no video id and it isn't in Subsonic, we can't fulfill it.
    if not vid:
        cur.is_playable = False
        cur.error = "MISSING_SOURCE"
        db.commit()
        raise HTTPException(status_code=404, detail="Current item not playable (missing source).")

    # Not in Subsonic: download/import (front-of-queue streaming only).
    job = DownloadJob(
        video_id=vid,
        url=f"https://music.youtube.com/watch?v={vid}",
        title=cur.title,
        artist=_clean(cur.artist) or "Unknown Artist",
        album=cur.album or "",
        album_artist=_clean(cur.artist) or "Unknown Artist",
        browse_id=_clean(getattr(cur, "yt_browse_id", "") or ""),
        art_url=cur.art_url or "",
        track_no=_infer_int(getattr(cur, "position", 0), 0) + 1,
        duration_ms=int(cur.duration_ms or 0),
        user_id=cur.session_user_id,
        priority=0,
    )

    DOWNLOAD_MANAGER.mark_streaming(vid, True)
    try:
        if progressive_inbound:
            # Start download and stream from the growing file as soon as it exists.
            stream_path = await DOWNLOAD_MANAGER.ensure_started(job, min_bytes=HELIX_PROGRESSIVE_MIN_BYTES)
            if not stream_path:
                # Do not leave the browser pointing at a missing/unsupported media
                # resource if progressive startup could not detect a usable part file.
                inbound_path = await DOWNLOAD_MANAGER.ensure_downloaded(job)
                stream_path = DOWNLOAD_MANAGER.ensure_stream_cache(vid, inbound_path)
        else:
            inbound_path = await DOWNLOAD_MANAGER.ensure_downloaded(job)
            stream_path = DOWNLOAD_MANAGER.ensure_stream_cache(vid, inbound_path)
    except Exception:
        DOWNLOAD_MANAGER.mark_streaming(vid, False)
        raise

    cur.source = "inbound"
    cur.inbound_path = stream_path
    # Progressive mode may return either a growing .part file or an already-ready
    # cached/final file. Mark only actual .part paths as DOWNLOADING. Completed files
    # must be served with byte ranges so browser refresh/resume seeks work.
    cur.download_status = "DOWNLOADING" if str(stream_path or "").endswith(".part") else "DOWNLOADED"
    cur.is_playable = True
    cur.error = ""
    db.commit()



async def _stream_inbound_progressive(request: Request, cur: QueueItem) -> StreamingResponse:
    """Stream from an inbound file that may still be downloading (.part).

    We intentionally ignore Range during progressive download to allow instant start.
    Seeking is expected to be clamped/disabled in the client until finalized.
    """
    import mimetypes

    file_path = cur.inbound_path or ""
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Current item not playable (missing).")

    ctype = _browser_audio_media_type(file_path)

    vid = _clean(getattr(cur, "yt_video_id", "") or "")
    DOWNLOAD_MANAGER.mark_streaming(vid, True)

    async def tail_iter():
        try:
            pos = 0
            while True:
                try:
                    with open(file_path, "rb") as f:
                        f.seek(pos)
                        data = f.read(1024 * 256)
                        if data:
                            pos += len(data)
                            yield data
                            continue
                except FileNotFoundError:
                    pass

                # No new bytes available yet.
                # If download has finalized, try switching to the finalized path.
                if vid and DOWNLOAD_MANAGER.is_ready(vid):
                    final_path = DOWNLOAD_MANAGER.ready_path(vid)
                    if final_path and os.path.exists(final_path) and final_path != file_path:
                        cur.inbound_path = final_path
                        file_path_final = final_path
                        # Drain remaining bytes from final file
                        with open(file_path_final, "rb") as f2:
                            f2.seek(pos)
                            while True:
                                chunk = f2.read(1024 * 256)
                                if not chunk:
                                    break
                                pos += len(chunk)
                                yield chunk
                        break
                    break
                await asyncio.sleep(0.25)
        finally:
            DOWNLOAD_MANAGER.mark_streaming(vid, False)

    headers = {"Accept-Ranges": "none"}
    return StreamingResponse(tail_iter(), media_type=ctype, status_code=200, headers=headers)

def _parse_range_header(range_header: str | None, size: int) -> tuple[int, int] | None:
    """Return (start, end) inclusive for a single bytes range."""
    if not range_header:
        return None
    m = re.match(r"bytes=(\d*)-(\d*)", range_header.strip())
    if not m:
        return None
    start_s, end_s = m.group(1), m.group(2)
    if start_s == "" and end_s == "":
        return None
    if start_s == "":
        length = int(end_s)
        if length <= 0:
            return None
        start = max(0, size - length)
        end = size - 1
        return (start, end)
    start = int(start_s)
    end = int(end_s) if end_s else (size - 1)
    if start >= size:
        return None
    end = min(end, size - 1)
    if end < start:
        return None
    return (start, end)


async def _stream_inbound_with_range(request: Request, cur: QueueItem) -> StreamingResponse:
    import mimetypes

    file_path = cur.inbound_path or ""
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Current item not playable (missing).")

    ctype = _browser_audio_media_type(file_path)
    size = os.path.getsize(file_path)
    rng = _parse_range_header(request.headers.get("range"), size)

    async def file_iter(start: int = 0, end: int | None = None):
        try:
            with open(file_path, "rb") as f:
                f.seek(start)
                remaining = (end - start + 1) if end is not None else None
                while True:
                    chunk_size = 1024 * 256
                    if remaining is not None:
                        if remaining <= 0:
                            break
                        chunk_size = min(chunk_size, remaining)
                    data = f.read(chunk_size)
                    if not data:
                        break
                    if remaining is not None:
                        remaining -= len(data)
                    yield data
        finally:
            DOWNLOAD_MANAGER.mark_streaming(getattr(cur, "yt_video_id", None), False)

    headers = {"Accept-Ranges": "bytes"}
    if rng:
        start, end = rng
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        headers["Content-Length"] = str(end - start + 1)
        return StreamingResponse(file_iter(start, end), media_type=ctype, status_code=206, headers=headers)

    headers["Content-Length"] = str(size)
    return StreamingResponse(file_iter(0, None), media_type=ctype, status_code=200, headers=headers)


async def _stream_subsonic_with_range(request: Request, cur: QueueItem, *, settings: dict) -> StreamingResponse:
    client = await _subsonic_client_from_settings(settings)
    try:
        force_transcode_m4a = settings.get("subsonic_force_transcode_m4a")
        if force_transcode_m4a is None:
            force_transcode_m4a = True

        transcode_max_bitrate = _infer_int(settings.get("subsonic_transcode_max_bitrate"), 0)
        suffix = ""

        if force_transcode_m4a:
            try:
                info_url = f"{client.base_url}/rest/getSong.view"
                info_params = {"id": cur.subsonic_song_id, **client._auth_params()}  # type: ignore[attr-defined]
                async with httpx.AsyncClient(timeout=client.timeout) as hi:
                    ri = await hi.get(info_url, params=info_params)
                    ri.raise_for_status()
                    j = ri.json() or {}
                    song = (j.get("subsonic-response", {}) or {}).get("song", {}) or {}
                    suffix = str(song.get("suffix") or "").lower().strip()
            except Exception:
                suffix = ""

        url = f"{client.base_url}/rest/stream.view"
        params = {"id": cur.subsonic_song_id, **client._auth_params()}  # type: ignore[attr-defined]

        if force_transcode_m4a and suffix in {"m4a", "mp4"}:
            params["format"] = "mp3"
            if transcode_max_bitrate > 0:
                params["maxBitRate"] = str(transcode_max_bitrate)

        headers_in = {}
        if request.headers.get("range"):
            headers_in["Range"] = request.headers.get("range")

        # IMPORTANT:
        # Do NOT use `async with h.stream(...) as r` and return `r.aiter_bytes()` from inside
        # that context manager. The context manager would close the upstream response
        # immediately on return, causing clients (ExoPlayer) to see "unexpected end of stream".
        #
        # Instead, keep the upstream connection open for the lifetime of the downstream
        # StreamingResponse by managing close() in the generator.

        h = httpx.AsyncClient(timeout=None)
        req = h.build_request("GET", url, params=params, headers=headers_in)
        r = await h.send(req, stream=True)
        try:
            r.raise_for_status()
        except Exception:
            await r.aclose()
            await h.aclose()
            raise

        ctype = r.headers.get("content-type")
        if not ctype:
            ctype = "audio/mpeg" if params.get("format") == "mp3" else "application/octet-stream"
        ctype_l = ctype.lower()
        if (not ctype_l.startswith("audio/")) and ("application/octet-stream" not in ctype_l):
            # Subsonic/Navidrome may return an XML/JSON/text error body for a stale
            # song id even when the transport request itself succeeded. Do not proxy
            # that to the browser as media; trigger fallback recovery in stream_item.
            try:
                await r.aread()
            finally:
                await r.aclose()
                await h.aclose()
            raise HTTPException(status_code=502, detail="Subsonic item is not streamable; attempting fallback.")

        headers_out = {"Accept-Ranges": "bytes"}
        # Pass through important range/length headers when present.
        for k in ("accept-ranges", "content-range", "content-length"):
            if k in r.headers:
                headers_out["-".join([w.capitalize() for w in k.split('-')])] = r.headers[k]

        async def gen():
            try:
                async for chunk in r.aiter_bytes():
                    yield chunk
            finally:
                try:
                    await r.aclose()
                finally:
                    await h.aclose()

        return StreamingResponse(
            gen(),
            media_type=ctype,
            status_code=r.status_code,
            headers=headers_out,
        )
    finally:
        await client.close()


async def request_fulfillment(queue_item_id: str, user: User = Depends(get_current_user)):
    """Explicitly request background fulfillment for a queue item.

    IMPORTANT: Never holds DB across awaits.
    """
    # DB burst: load queue item snapshot and (optionally) resolve yt_video_id
    db = SessionLocal()
    try:
        qi = db.execute(
            select(QueueItem).where(QueueItem.id == queue_item_id, QueueItem.session_user_id == user.id)
        ).scalar_one_or_none()
        if not qi:
            raise HTTPException(status_code=404, detail="Queue item not found.")
        if qi.is_playable and qi.source == "subsonic":
            return {"ok": True, "status": "ALREADY_PLAYABLE"}

        title = qi.title or ""
        artist = qi.artist or ""
        album = qi.album or ""
        art_url = qi.art_url or ""
        duration_ms = int(qi.duration_ms or 0)
        position = int(getattr(qi, "position", 0) or 0)
        yt_video_id = _clean(qi.yt_video_id or "")

        if not yt_video_id:
            # Try to find a YouTube id lazily from the track intent (best-effort, sync).
            try:
                want_dur_s = int((duration_ms or 0) / 1000) if duration_ms else None
            except Exception:
                want_dur_s = None
            try:
                r = _find_track_safe(title=title, artist=artist, album=album or None, duration_seconds=want_dur_s)
                if r.found and r.video_id:
                    yt_video_id = _clean(r.video_id)
                    qi.yt_video_id = yt_video_id
                    db.commit()
            except Exception:
                pass

        if not yt_video_id:
            return {"ok": False, "status": "NO_YT_ID"}
    finally:
        db.close()

    # External I/O: enqueue download (bounded)
    vid = _clean(yt_video_id)
    try:
        await asyncio.wait_for(
            DOWNLOAD_MANAGER.enqueue_normal(
                DownloadJob(
                    video_id=vid,
                    url=f"https://music.youtube.com/watch?v={vid}",
                    title=title,
                    artist=artist,
                    album=album,
                    art_url=art_url,
                    track_no=position + 1,
                    duration_ms=duration_ms,
                    user_id=user.id,
                    priority=10,
                )
            ),
            timeout=float(os.getenv("HELIX_DOWNLOAD_ENQUEUE_TIMEOUT_S", "5")),
        )
    except asyncio.TimeoutError:
        return {"ok": False, "status": "ENQUEUE_TIMEOUT"}

    # DB burst: mark download requested
    db = SessionLocal()
    try:
        qi2 = db.get(QueueItem, queue_item_id)
        if qi2 and qi2.session_user_id == user.id:
            qi2.download_status = "QUEUED"
            db.commit()
            from ..realtime import schedule_player_state_broadcast
            schedule_player_state_broadcast(user.id)
    finally:
        db.close()

    return {"ok": True, "status": "QUEUED"}
