from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException

from ..auth import get_current_user
from ..db import SessionLocal
from ..models import QueueItem, User
from ..api_schemas.player import PlayerPlayAlbumRequest, PlayerQueueAppendAlbumRequest
from ..player import engine as player_engine


def _clean(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _duration_ms(song: dict[str, Any]) -> int:
    try:
        duration = int(song.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0
    return max(0, duration) * 1000


def _subsonic_art_url(cover_id: str, size: int = 768) -> str:
    cid = _clean(cover_id)
    return f"/api/art/subsonic/{cid}?size={size}" if cid else ""


async def _load_subsonic_album(
    album_id: str,
    settings: dict[str, Any],
    *,
    title: str = "",
    artist: str = "",
) -> dict[str, Any] | None:
    """Return a Subsonic album when album_id belongs to the local library.

    Album detail pages can carry a Subsonic album id in the same `browse_id`
    field used by YTMusic album pages. The legacy album playback endpoints
    assumed every value was a YouTube Music browse id, which caused a 500 when
    a complete local album was played or queued.

    Probe the configured Subsonic server first. A miss simply falls back to the
    existing YTMusic implementation.
    """
    if not album_id or not player_engine._is_subsonic_configured(settings):
        return None

    client = None
    try:
        client = await player_engine._subsonic_client_from_settings(settings)
        album = None
        if album_id:
            try:
                album = await client.get_album(album_id)
            except Exception:
                album = None

        # Album detail pages opened through YTMusic can still be completely
        # present in Subsonic. In that case the visible browse_id is not the
        # local album id, so resolve the local album from the metadata already
        # present in the request before falling back to YTMusic.
        if not isinstance(album, dict) and title:
            try:
                match = await client.search_album_best(album=title, artist=artist)
            except Exception:
                match = None
            local_id = _clean((match or {}).get("id") or "") if isinstance(match, dict) else ""
            if local_id:
                try:
                    album = await client.get_album(local_id)
                except Exception:
                    album = None

        if not isinstance(album, dict):
            return None
        songs = album.get("song") or []
        if not isinstance(songs, list) or not songs:
            return None
        return album
    except Exception:
        return None
    finally:
        if client is not None:
            try:
                await client.close()
            except Exception:
                pass


def _queue_items_from_subsonic_album(
    album: dict[str, Any],
    *,
    payload_art_url: str = "",
) -> list[QueueItem]:
    album_title = _clean(album.get("name") or album.get("title") or "")
    album_artist = _clean(album.get("artist") or "")
    album_cover_id = _clean(album.get("coverArt") or "")
    album_art = _clean(payload_art_url) or _subsonic_art_url(album_cover_id, 768)

    rows = album.get("song") or []
    items: list[QueueItem] = []
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            continue
        title = _clean(raw.get("title") or "")
        song_id = _clean(raw.get("id") or "")
        if not title or not song_id:
            continue

        cover_id = _clean(raw.get("coverArt") or album_cover_id)
        art_url = _subsonic_art_url(cover_id, 512) or album_art
        try:
            track_no = int(raw.get("track") or raw.get("song") or index + 1)
        except (TypeError, ValueError):
            track_no = index + 1

        item = QueueItem(
            kind="albumtrack",
            title=title,
            artist=_clean(raw.get("artist") or album_artist),
            album=_clean(raw.get("album") or album_title),
            duration_ms=_duration_ms(raw),
            art_url=art_url,
        )
        item.track_no = max(1, track_no)
        item.source = "subsonic"
        item.subsonic_song_id = song_id
        item.yt_video_id = ""
        item.is_playable = True
        item.error = ""
        items.append(item)

    return items


async def play_album(
    payload: PlayerPlayAlbumRequest,
    user: User = Depends(get_current_user),
):
    album_id = _clean(payload.subsonic_album_id or payload.browse_id)
    settings = player_engine._load_settings_short()

    local_album = await _load_subsonic_album(
        album_id,
        settings,
        title=_clean(payload.title or ""),
        artist=_clean(payload.artist or ""),
    )
    if local_album is None:
        if album_id and not album_id.startswith("MPRE"):
            raise HTTPException(status_code=404, detail="Could not resolve this album in Subsonic or YouTube Music.")
        return await player_engine.play_album(payload, user)

    queue_items = _queue_items_from_subsonic_album(
        local_album,
        payload_art_url=_clean(payload.art_url or ""),
    )
    if not queue_items:
        raise HTTPException(status_code=404, detail="No tracks found for this Subsonic album.")

    db = SessionLocal()
    try:
        player_engine._clear_queue(db, user.id, settings=settings, log_current=True, played_ms=0)

        for position, item in enumerate(queue_items):
            item.session_user_id = user.id
            item.position = position
            db.add(item)

        sess = player_engine._get_or_create_session(db, user.id)
        sess.active_station_id = ""
        sess.autoplay_enabled = False
        sess.current_index = 0
        sess.is_playing = True
        db.commit()
        return player_engine._changed_state(db=db, user=user)
    finally:
        db.close()


async def queue_append_album(
    payload: PlayerQueueAppendAlbumRequest,
    user: User = Depends(get_current_user),
):
    album_id = _clean(payload.subsonic_album_id or payload.browse_id)
    settings = player_engine._load_settings_short()

    local_album = await _load_subsonic_album(
        album_id,
        settings,
        title=_clean(payload.title or ""),
        artist=_clean(payload.artist or ""),
    )
    if local_album is None:
        if album_id and not album_id.startswith("MPRE"):
            raise HTTPException(status_code=404, detail="Could not resolve this album in Subsonic or YouTube Music.")
        return await player_engine.queue_append_album(payload, user)

    queue_items = _queue_items_from_subsonic_album(
        local_album,
        payload_art_url=_clean(payload.art_url or ""),
    )
    if not queue_items:
        raise HTTPException(status_code=404, detail="No tracks found for this Subsonic album.")

    db = SessionLocal()
    try:
        if player_engine.queue_add_position_for_user(db, user.id) == "next":
            player_engine._insert_queue_items_after_current_with_sqlite_lock(db, user.id, queue_items)
        else:
            player_engine._append_queue_items_with_sqlite_lock(db, user.id, queue_items)
        return player_engine._changed_state(db=db, user=user)
    finally:
        db.close()
