from __future__ import annotations

import re
import os
import logging
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import select, delete

from ..auth import get_current_user
from ..db import get_db, SessionLocal
from ..models import User, PlaybackSession, QueueItem, ListenHistoryItem, Station
from ..schemas import PlayerPlayAlbumRequest, PlayerPlayTrackRequest, PlayerJumpRequest, PlayerQueueItem, PlayerStateResponse, PlayerQueueAppendTrackRequest, PlayerQueueAppendAlbumRequest, PlayerRemoveQueueItemResponse, PlayerHistoryItem, PlayerHistoryResponse, PlayerActionRequest, PlayerReplayRequest, AutoplaySetRequest
from ..settings_store import get_settings
from ..integrations.subsonic import SubsonicClient
from ..integrations.ytmusic_api import get_album_full
from ..integrations.ytmusic_search import find_track
from ..download_manager import DOWNLOAD_MANAGER, DownloadJob
from ..stations_engine import generate_and_append_station_track


router = APIRouter(prefix="/api/player", tags=["player"])

LOG = logging.getLogger("helix.player")

# Prevent duplicate background fills per (user_id, browse_id)
_ALBUM_FILLING: set[tuple[str, str]] = set()


def _clean(s: str) -> str:
    return " ".join((s or "").strip().split())

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




def _history_limit(settings: Dict[str, Any]) -> int:
    try:
        return int(settings.get("listen_history_limit") or 50)
    except Exception:
        return 50


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
    lim = max(0, _history_limit(settings))
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

    # enforce limit PER STATION
    ids = db.execute(
        select(ListenHistoryItem.id)
        .where(ListenHistoryItem.user_id == user_id)
        .where(ListenHistoryItem.station_id == station_id)
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


@router.get("/state", response_model=PlayerStateResponse)
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
    return PlayerStateResponse(
        is_playing=bool(sess.is_playing),
        current_index=int(sess.current_index),
        now_playing=now,
        queue=queue,
        autoplay_enabled=bool(getattr(sess, "autoplay_enabled", True)),
        active_station_id=active_station_id,
        active_station=active_station,
    )


@router.post("/autoplay", response_model=PlayerStateResponse)
def set_autoplay(payload: AutoplaySetRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    sess = _get_or_create_session(db, user.id)
    sess.autoplay_enabled = bool(payload.enabled)
    db.commit()
    return state(db=db, user=user)


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


@router.post("/play/track", response_model=PlayerStateResponse)
async def play_track(payload: PlayerPlayTrackRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    settings = get_settings(db)
    _clear_queue(db, user.id, settings=settings, log_current=True, played_ms=0)

    title = _clean(payload.title)
    artist = _clean(payload.artist)
    album = _clean(payload.album or "")
    duration_ms = _infer_int(payload.duration_ms, 0)
    art_url = _clean(payload.art_url or "")
    yt_video_id = _clean(getattr(payload, "yt_video_id", None) or "")

    client = await _subsonic_client_from_settings(settings)
    try:
        song = await client.search_song_best(title=title, artist=artist, duration_ms=duration_ms or None)
    finally:
        await client.close()

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
    sess.current_index = 0
    # We mark as playing even if currently missing; the stream endpoint will fulfill ASAP.
    sess.is_playing = True
    db.commit()

    return state(db=db, user=user)


@router.post("/play/album", response_model=PlayerStateResponse)
async def play_album(payload: PlayerPlayAlbumRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Play an album using the *same semantics as clicking a single track*.

    - Immediately sets the queue + now playing to track 1.
    - Does NOT download inside this request.
    - Track 1 will be fulfilled by /stream exactly like a single-track click.
    - After track 1 begins streaming, Helix will background-fill the remaining tracks
      one-by-one (download -> tag -> move -> scan).
    """
    settings = get_settings(db)
    _clear_queue(db, user.id, settings=settings, log_current=True, played_ms=0)

    browse_id = _clean(payload.browse_id)
    if not browse_id:
        raise HTTPException(status_code=400, detail="browse_id is required")

    full = get_album_full(browse_id) or {}
    album_title = _clean(full.get("title") or "") or "(YouTube Music Album)"
    album_artist = _clean(full.get("artist") or "")
    album_art = _clean((full.get("thumbnail_url") or "") if isinstance(full, dict) else "") or _clean(payload.art_url or "")
    tracks = full.get("tracks") or []
    if not tracks:
        raise HTTPException(status_code=404, detail="No tracks found for this album on YouTube Music.")

    # Fallback: infer album artist from track 1 structured artist.
    if not album_artist:
        album_artist = _clean((tracks[0].get("artist") or ""))

    # Resolve ONLY track 1 against Subsonic (so the response is fast).
    t0 = tracks[0]
    t0_title = _clean(t0.get("title") or "")
    t0_artist = _clean(t0.get("artist") or "") or album_artist
    t0_len_ms = _infer_int(t0.get("lengthMs"), 0) or (_infer_int(t0.get("duration_seconds"), 0) * 1000)
    t0_vid = _clean(t0.get("video_id") or "")
    t0_track_no = _infer_int(t0.get("pos"), 1) or 1

    client = await _subsonic_client_from_settings(settings)
    try:
        song0 = await client.search_song_best(title=t0_title, artist=t0_artist, duration_ms=t0_len_ms or None)
    finally:
        await client.close()

    queue_items: List[QueueItem] = []
    # Track 1 item
    qi0 = QueueItem(
        session_user_id=user.id,
        position=0,
        kind="albumtrack",
        title=t0_title,
        artist=t0_artist or album_artist,
        album=album_title,
        duration_ms=t0_len_ms or 0,
        art_url=album_art,
    )
    qi0.yt_video_id = t0_vid
    qi0.yt_browse_id = browse_id

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
        # Note: DO NOT download here. /stream will fulfill ASAP (same as single-track click).

    queue_items.append(qi0)

    # Remaining tracks: add to queue immediately, but do not resolve/download yet.
    pos = 1
    for t in tracks[1:]:
        t_title = _clean(t.get("title") or "")
        t_artist = _clean(t.get("artist") or "") or album_artist
        length_ms = _infer_int(t.get("lengthMs"), 0) or (_infer_int(t.get("duration_seconds"), 0) * 1000)
        video_id = _clean(t.get("video_id") or "")
        qi = QueueItem(
            session_user_id=user.id,
            position=pos,
            kind="albumtrack",
            title=t_title,
            artist=t_artist or album_artist,
            album=album_title,
            duration_ms=length_ms or 0,
            art_url=album_art,
        )
        qi.yt_video_id = video_id
        qi.yt_browse_id = browse_id
        qi.source = "ytmusic"
        qi.subsonic_song_id = ""
        qi.is_playable = False
        qi.error = "NOT_IN_LIBRARY"
        queue_items.append(qi)
        pos += 1

    max_q = int(settings.get("player_max_queue_items", 500) or 500)
    queue_items = queue_items[:max_q]

    for qi in queue_items:
        db.add(qi)

    sess = _get_or_create_session(db, user.id)
    # Switching to an explicit queue clears station mode.
    sess.active_station_id = ""
    sess.current_index = 0
    sess.is_playing = True
    db.commit() 

    return state(db=db, user=user)


 

@router.post("/queue/append/track", response_model=PlayerStateResponse)
async def queue_append_track(payload: PlayerQueueAppendTrackRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    settings = get_settings(db)
    sess = _get_or_create_session(db, user.id)
    items = db.execute(select(QueueItem).where(QueueItem.session_user_id == user.id).order_by(QueueItem.position.asc())).scalars().all()
    next_pos = len(items)

    title = _clean(payload.title)
    artist = _clean(payload.artist)
    album = _clean(payload.album or "")
    duration_ms = _infer_int(payload.duration_ms, 0)
    art_url = _clean(payload.art_url or "")
    yt_video_id = _clean(getattr(payload, "yt_video_id", None) or "")

    client = await _subsonic_client_from_settings(settings)
    try:
        song = await client.search_song_best(title=title, artist=artist, duration_ms=duration_ms or None)
    finally:
        await client.close()

    item = QueueItem(
        session_user_id=user.id,
        position=next_pos,
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

        # Background download so it's ready by the time it reaches the top.
        if yt_video_id:
            await DOWNLOAD_MANAGER.enqueue_normal(DownloadJob(
                video_id=yt_video_id,
                url=f"https://music.youtube.com/watch?v={yt_video_id}",
                title=title,
                artist=artist,
                album=album,
                art_url=art_url,
                track_no=0,
                duration_ms=duration_ms or 0,
                priority=10,
            ))

    db.add(item)
    db.commit()
    return state(db=db, user=user)


@router.post("/queue/append/album", response_model=PlayerStateResponse)
async def queue_append_album(payload: PlayerQueueAppendAlbumRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    settings = get_settings(db)

    browse_id = _clean(payload.browse_id)
    if not browse_id:
        raise HTTPException(status_code=400, detail="browse_id is required")

    # Like /play/album, do not trust frontend-provided title/artist/art_url.
    full = get_album_full(browse_id) or {}
    title = _clean(full.get("title") or "") or "(YouTube Music Album)"
    artist = _clean(full.get("artist") or "")
    album_art = _clean(payload.art_url or "") or _clean(full.get("thumbnail_url") or "")
    tracks = full.get("tracks") or []
    if not tracks:
        raise HTTPException(status_code=404, detail="No tracks found for this album on YouTube Music.")

    if not artist:
        artist = _clean((tracks[0].get("artist") or ""))

    items = db.execute(select(QueueItem).where(QueueItem.session_user_id == user.id).order_by(QueueItem.position.asc())).scalars().all()
    pos = len(items)

    client = await _subsonic_client_from_settings(settings)
    queue_items: List[QueueItem] = []
    try:
        for t in tracks:
            t_title = _clean(t.get("title") or "")
            length_ms = _infer_int(t.get("lengthMs"), 0) or (_infer_int(t.get("duration_seconds"), 0) * 1000)
            t_artist = _clean(t.get("artist") or "") or artist
            video_id = _clean(t.get("video_id") or "")
            track_no = _infer_int(t.get("pos"), pos + 1)

            song = await client.search_song_best(title=t_title, artist=t_artist, duration_ms=length_ms or None)

            qi = QueueItem(
                session_user_id=user.id,
                position=pos,
                kind="albumtrack",
                title=t_title,
                artist=t_artist or artist,
                album=_clean(title),
                duration_ms=length_ms or 0,
                art_url=album_art,
            )
            qi.yt_video_id = video_id
            qi.yt_browse_id = browse_id
            if song and song.get("id"):
                qi.source = "subsonic"
                qi.subsonic_song_id = str(song.get("id"))
                qi.is_playable = True
            else:
                qi.source = "ytmusic"
                qi.subsonic_song_id = ""
                qi.is_playable = False
                qi.error = "NOT_IN_LIBRARY"

                if video_id:
                    await DOWNLOAD_MANAGER.enqueue_normal(DownloadJob(
                        video_id=video_id,
                        url=f"https://music.youtube.com/watch?v={video_id}",
                        title=t_title,
                        artist=t_artist or artist,
                        album=_clean(title),
                        art_url=album_art,
                        track_no=track_no,
                        duration_ms=length_ms or 0,
                        priority=10,
                    ))

            queue_items.append(qi)
            pos += 1
    finally:
        await client.close()

    omit_missing = bool(settings.get("player_omit_missing", False))
    if omit_missing:
        queue_items = [q for q in queue_items if q.is_playable]

    max_q = int(settings.get("player_max_queue_items", 500) or 500)
    queue_items = queue_items[:max_q]

    if not queue_items:
        raise HTTPException(status_code=404, detail="No playable tracks found for this album in Subsonic.")

    for qi in queue_items:
        db.add(qi)
    db.commit()
    return state(db=db, user=user)


@router.delete("/queue/item/{queue_item_id}", response_model=PlayerRemoveQueueItemResponse)
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
    return PlayerRemoveQueueItemResponse(ok=True)


@router.get("/history", response_model=PlayerHistoryResponse)
def history(station_id: str | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    settings = get_settings(db)
    lim = _history_limit(settings)

    q = select(ListenHistoryItem).where(ListenHistoryItem.user_id == user.id)
    if station_id is not None:
        q = q.where(ListenHistoryItem.station_id == station_id)

    items = db.execute(
        q.order_by(ListenHistoryItem.created_at.desc()).limit(lim)
    ).scalars().all()
    return PlayerHistoryResponse(limit=lim, items=[_to_history(h) for h in items])


@router.post("/history/limit", response_model=PlayerHistoryResponse)
def history_set_limit(payload: Dict[str, Any], db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    # Admin UI can call settings patch too; this is a convenience for the frontend.
    try:
        lim = int(payload.get("limit") or 50)
    except Exception:
        lim = 50
    lim = max(0, min(500, lim))
    from ..settings_store import patch_settings
    patch_settings(db, {"listen_history_limit": lim})
    settings = get_settings(db)
    return history(db=db, user=user)


@router.post("/ended", response_model=PlayerStateResponse)
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
        return state(db=db, user=user)

    # End of queue: optionally autoplay from the active station.
    sess.is_playing = False
    db.commit()

    if bool(getattr(sess, "autoplay_enabled", True)) and (getattr(sess, "active_station_id", "") or ""):
        try:
            await generate_and_append_station_track(
                db,
                user.id,
                str(getattr(sess, "active_station_id", "") or ""),
                settings=settings,
                advance_to_new_item=True,
            )
        except Exception as e:
            LOG.warning("autoplay append failed: %s", e)
    return state(db=db, user=user)


@router.post("/jump", response_model=PlayerStateResponse)
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
    return state(db=db, user=user)


@router.post("/replay", response_model=PlayerStateResponse)
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
    return state(db=db, user=user)

@router.post("/next", response_model=PlayerStateResponse)
async def next_track(payload: Optional[PlayerActionRequest] = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
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
        return state(db=db, user=user)

    if sess.current_index < len(items) - 1:
        sess.current_index += 1
        # Keep playing if next is playable
        sess.is_playing = _can_play(items[sess.current_index])
    else:
        # End of queue: optionally autoplay from the active station.
        sess.is_playing = False
        db.commit()
        if bool(getattr(sess, "autoplay_enabled", True)) and (getattr(sess, "active_station_id", "") or ""):
            try:
                await generate_and_append_station_track(
                    db,
                    user.id,
                    str(getattr(sess, "active_station_id", "") or ""),
                    settings=settings,
                    advance_to_new_item=True,
                )
            except Exception as e:
                LOG.warning("autoplay append failed: %s", e)
        return state(db=db, user=user)
    db.commit()
    return state(db=db, user=user)


@router.post("/prev", response_model=PlayerStateResponse)
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
        return state(db=db, user=user)

    if sess.current_index > 0:
        sess.current_index -= 1
    sess.is_playing = _can_play(items[sess.current_index])
    db.commit()
    return state(db=db, user=user)


@router.post("/pause", response_model=PlayerStateResponse)
def pause(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    sess = _get_or_create_session(db, user.id)
    sess.is_playing = False
    db.commit()
    return state(db=db, user=user)


@router.post("/resume", response_model=PlayerStateResponse)
def resume(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    sess = _get_or_create_session(db, user.id)
    items = db.execute(select(QueueItem).where(QueueItem.session_user_id == user.id).order_by(QueueItem.position.asc())).scalars().all()
    if not items:
        raise HTTPException(status_code=400, detail="Queue is empty.")
    if not _can_play(items[sess.current_index]):
        raise HTTPException(status_code=400, detail="Current queue item is not playable.")
    sess.is_playing = True
    db.commit()
    return state(db=db, user=user)


@router.get("/stream/{queue_item_id}")
async def stream_item(queue_item_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    settings = get_settings(db)
    items = db.execute(select(QueueItem).where(QueueItem.session_user_id == user.id).order_by(QueueItem.position.asc())).scalars().all()
    if not items:
        raise HTTPException(status_code=404, detail="Queue is empty.")
    cur = next((i for i in items if i.id == queue_item_id), None)
    if not cur:
        raise HTTPException(status_code=404, detail="Queue item not found.")
    # If the item isn't playable from Subsonic yet, try to resolve it from Subsonic first (fast path),
    # otherwise fulfill ASAP from YT Music (download) only when this item is being streamed (front of queue).
    # If we have no YT id (common for station-discovered tracks), attempt to find one lazily.
    if (not _clean(getattr(cur, "yt_video_id", "") or "")) and (not cur.is_playable or not cur.subsonic_song_id) and cur.source != "subsonic":
        try:
            want_dur_s = int((cur.duration_ms or 0) / 1000) if (cur.duration_ms or 0) else None
        except Exception:
            want_dur_s = None
        try:
            r = find_track(title=cur.title or "", artist=cur.artist or "", album=cur.album or None, duration_seconds=want_dur_s, limit=9)
            if r.found and r.video_id:
                cur.yt_video_id = r.video_id
                db.commit()
                LOG.info("[stream] resolved yt id lazily vid=%s conf=%.2f title=%r artist=%r", r.video_id, r.confidence, cur.title, cur.artist)
        except Exception as e:
            LOG.warning("[stream] lazy yt id search failed: %s", e)

    if (cur.yt_video_id and (
        (not cur.is_playable or not cur.subsonic_song_id) or (cur.source != "subsonic" and (not cur.inbound_path or not os.path.exists(cur.inbound_path)))
    )):
        vid = _clean(cur.yt_video_id)
        LOG.info("[stream] queue_item=%s title=%r artist=%r album=%r dur_ms=%s src=%s sub_id=%s yt=%s",
                 cur.id, cur.title, cur.artist, cur.album, cur.duration_ms, cur.source, cur.subsonic_song_id, vid)

        # If metadata is missing/bad, repair it using structured album metadata.
        if (not _clean(cur.artist or '') or _looks_like_views(cur.artist)) or (not _clean(cur.album or '')):
            try:
                _repair_from_album_full(cur)
                db.commit()
                LOG.info("[stream] repaired meta -> title=%r artist=%r album=%r dur_ms=%s browse_id=%s",
                         cur.title, cur.artist, cur.album, cur.duration_ms, getattr(cur, "yt_browse_id", None))
            except Exception as e:
                LOG.warning("[stream] metadata repair failed: %s", e)

        # Try Subsonic lookup before downloading (makes album playback behave like single-track play).
        try:
            client = await _subsonic_client_from_settings(settings)
            t_title_n, t_artist_n, _ = _norm_for_subsonic(cur.title or "", cur.artist or "", cur.album or "")
            LOG.info("[stream] subsonic lookup title=%r artist=%r", t_title_n, t_artist_n)
            song = await client.search_song_best(title=t_title_n, artist=t_artist_n, duration_ms=int(cur.duration_ms or 0) or None)
        except Exception as e:
            song = None
            LOG.warning("[stream] subsonic lookup error: %s", e)
        finally:
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
            LOG.info("[stream] resolved in subsonic id=%s", cur.subsonic_song_id)
        else:
            # Fulfill from YT Music (download) since it's not in Subsonic.
            job = DownloadJob(
                video_id=vid,
                url=f"https://music.youtube.com/watch?v={vid}",
                title=cur.title,
                artist=_clean(cur.artist) or "Unknown Artist",
                album=cur.album or "",
                art_url=cur.art_url or "",
                track_no=_infer_int(getattr(cur, "position", 0), 0) + 1,
                duration_ms=int(cur.duration_ms or 0),
                browse_id=_clean(getattr(cur, "yt_browse_id", "") or ""),
                priority=0,
            )
            LOG.info("[stream] downloading vid=%s title=%r artist=%r album=%r", vid, job.title, job.artist, job.album)
            inbound_path = await DOWNLOAD_MANAGER.ensure_downloaded(job)
            stream_path = DOWNLOAD_MANAGER.ensure_stream_cache(vid, inbound_path)
            cur.source = "inbound"
            cur.inbound_path = stream_path
            cur.download_status = "DOWNLOADED"
            cur.is_playable = True
            cur.error = ""
            db.commit()
            LOG.info("[stream] inbound ready stream_path=%s", stream_path)

    # If still not playable, fail.
    if cur.source != "subsonic" and (not cur.inbound_path or not os.path.exists(cur.inbound_path)):
        raise HTTPException(status_code=404, detail="Current item not playable (missing).")

    # If inbound, stream from local file.
    if cur.source != "subsonic":
        import mimetypes

        file_path = cur.inbound_path
        ctype = mimetypes.guess_type(file_path)[0] or "audio/ogg"

        async def file_iter():
            DOWNLOAD_MANAGER.mark_streaming(cur.yt_video_id, True)
            try:
                with open(file_path, "rb") as f:
                    while True:
                        chunk = f.read(1024 * 256)
                        if not chunk:
                            break
                        yield chunk
            finally:
                DOWNLOAD_MANAGER.mark_streaming(cur.yt_video_id, False)

        return StreamingResponse(file_iter(), media_type=ctype)

    client = await _subsonic_client_from_settings(settings)
    try:
        # We stream bytes from Subsonic through Helix so the browser doesn't need credentials.
        #
        # Some libraries (notably .m4a / mp4 containers) can fail to play consistently in browsers depending on codec.
        # For Navidrome/Subsonic we can ask the server to transcode to a browser-friendly format (mp3).
        force_transcode_m4a = settings.get("subsonic_force_transcode_m4a")
        if force_transcode_m4a is None:
            force_transcode_m4a = True  # safe default

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
                # If we can't determine suffix, fall back to original stream.
                suffix = ""

        url = f"{client.base_url}/rest/stream.view"
        params = {"id": cur.subsonic_song_id, **client._auth_params()}  # type: ignore[attr-defined]

        # Force transcode any .m4a/.mp4 container to mp3 to avoid browser decode issues.
        if force_transcode_m4a and suffix in {"m4a", "mp4"}:
            params["format"] = "mp3"
            if transcode_max_bitrate > 0:
                params["maxBitRate"] = str(transcode_max_bitrate)

        async with httpx.AsyncClient(timeout=None) as h:
            r = await h.get(url, params=params)
            r.raise_for_status()
            ctype = r.headers.get("content-type")
            if not ctype:
                ctype = "audio/mpeg" if params.get("format") == "mp3" else "application/octet-stream"
            return StreamingResponse(r.aiter_bytes(), media_type=ctype)
    finally:
        await client.close()


@router.get("/stream/current")
async def stream_current(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    # Back-compat convenience: stream the currently selected item.
    sess = _get_or_create_session(db, user.id)
    items = db.execute(select(QueueItem).where(QueueItem.session_user_id == user.id).order_by(QueueItem.position.asc())).scalars().all()
    if not items or not (0 <= sess.current_index < len(items)):
        raise HTTPException(status_code=404, detail="Nothing playing.")
    return await stream_item(items[sess.current_index].id, db=db, user=user)


@router.post("/request-fulfillment/{queue_item_id}")
async def request_fulfillment(queue_item_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Explicitly request background fulfillment for a queue item."""
    qi = db.execute(select(QueueItem).where(QueueItem.id == queue_item_id, QueueItem.session_user_id == user.id)).scalar_one_or_none()
    if not qi:
        raise HTTPException(status_code=404, detail="Queue item not found.")
    if qi.is_playable and qi.source == "subsonic":
        return {"ok": True, "status": "ALREADY_PLAYABLE"}
    if not qi.yt_video_id:
        # Try to find a YouTube id lazily from the track intent.
        try:
            want_dur_s = int((qi.duration_ms or 0) / 1000) if (qi.duration_ms or 0) else None
        except Exception:
            want_dur_s = None
        try:
            r = find_track(title=qi.title or "", artist=qi.artist or "", album=qi.album or None, duration_seconds=want_dur_s, limit=9)
            if r.found and r.video_id:
                qi.yt_video_id = r.video_id
                db.commit()
        except Exception:
            pass

    if not qi.yt_video_id:
        return {"ok": False, "status": "NO_YT_ID"}

    vid = _clean(qi.yt_video_id)
    await DOWNLOAD_MANAGER.enqueue_normal(DownloadJob(
        video_id=vid,
        url=f"https://music.youtube.com/watch?v={vid}",
        title=qi.title,
        artist=qi.artist,
        album=qi.album or "",
        art_url=qi.art_url or "",
        track_no=_infer_int(getattr(qi, "position", 0), 0) + 1,
        duration_ms=int(qi.duration_ms or 0),
        priority=10,
    ))
    qi.download_status = "DOWNLOADING"
    db.commit()
    return {"ok": True, "status": "QUEUED"}