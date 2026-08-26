from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import string
import time
import urllib.parse

import httpx
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select, delete, update
from sqlalchemy.orm import Session
from yt_dlp import YoutubeDL

from ..auth import SESSION_COOKIE, cookie_secure, get_current_user
from ..db import get_db, SessionLocal
from ..models import SessionToken, User, Station
from ..lobby_models import SharedLobby, SharedLobbyHistoryItem, SharedLobbyMember, SharedLobbyQueueItem
from ..api_schemas.lobbies import (
    LobbyCreateRequest,
    LobbyEndedRequest,
    LobbyJoinRequest,
    LobbyJoinResponse,
    LobbyHistoryItemResponse,
    LobbyListResponse,
    LobbyMemberResponse,
    LobbyMemberUpdateRequest,
    LobbySelfUpdateRequest,
    LobbyOkResponse,
    LobbyPermissions,
    LobbyQueueAddRequest,
    LobbyQueueItemResponse,
    LobbyQueueReorderRequest,
    LobbySeekRequest,
    LobbyStateResponse,
    LobbyUpdateRequest,
 )
from ..settings_store import get_settings
from ..security import hash_password, verify_password
from ..art_sources import is_allowed_art_url
from ..integrations.ytmusic import find_track
from ..routers.search import (
    _RATE_LIMITER as SEARCH_RATE_LIMITER,
    _SEARCH_CACHE as SEARCH_CACHE,
    _load_settings_short as _search_load_settings_short,
    _make_key as search_make_key,
    _search_album_key,
    _search_client_ip,
    _search_mark_ytmusic,
    _search_song_key,
    _search_subsonic_only,
    _ytmusic_search,
)
from ..realtime import schedule_lobby_state_broadcast
from ..lobby_station import fill_lobby_station, schedule_lobby_station_fill
from ..stations_engine import StationGenerationError, StationSeedArtistNotFound
from ..player.engine import (
    _ensure_playable_for_stream,
    _stream_inbound_progressive,
    _stream_inbound_with_range,
    _stream_subsonic_with_range,
)

LOG = logging.getLogger(__name__)

router = APIRouter(prefix="/api/lobbies", tags=["shared lobbies"])

LOBBY_TOKEN_HEADER = "x-helix-lobby-token"
LOBBY_TOKEN_COOKIE = "helix_lobby_token"


def _now() -> datetime:
    return datetime.utcnow()


def _iso(dt: datetime) -> str:
    return dt.isoformat() + "Z"


def _server_time_ms() -> int:
    return int(time.time() * 1000)


def _load_permissions(raw: str | None, *, host: bool = False) -> LobbyPermissions:
    if host:
        return LobbyPermissions(
            can_add_to_queue=True,
            can_remove_own_queue_items=True,
            can_remove_any_queue_item=True,
            can_control_playback=True,
            can_skip=True,
            can_seek=True,
        )
    try:
        data = json.loads(raw or "{}")
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    return LobbyPermissions(**data)


def _dump_permissions(perms: LobbyPermissions) -> str:
    return perms.model_dump_json()


def _clean_text(value: str | None, max_len: int = 240) -> str:
    return (value or "").strip()[:max_len]


def _guess_image_content_type(data: bytes, fallback: str = "") -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    fallback = (fallback or "").split(";", 1)[0].strip().lower()
    return fallback if fallback.startswith("image/") else "application/octet-stream"


def _lobby_art_url(lobby_id: str, item: SharedLobbyQueueItem | None) -> str:
    if not item:
        return ""
    if item.art_url or item.yt_video_id or item.subsonic_song_id:
        return f"/api/lobbies/{urllib.parse.quote(lobby_id, safe='')}/art/{urllib.parse.quote(item.id, safe='')}"
    return ""


def _new_invite_code() -> str:
    return "".join(secrets.choice(string.ascii_uppercase) for _ in range(5))


def _new_guest_token() -> str:
    return secrets.token_urlsafe(48)


def _extract_youtube_video_id(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", raw):
        return raw
    try:
        parsed = urllib.parse.urlparse(raw)
    except Exception:
        return ""
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host in {"youtube.com", "music.youtube.com", "m.youtube.com"}:
        qs = urllib.parse.parse_qs(parsed.query or "")
        vid = (qs.get("v") or [""])[0]
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", vid):
            return vid
        parts = [p for p in (parsed.path or "").split("/") if p]
        if len(parts) >= 2 and parts[0] in {"shorts", "embed", "live"} and re.fullmatch(r"[A-Za-z0-9_-]{11}", parts[1]):
            return parts[1]
    if host == "youtu.be":
        vid = (parsed.path or "").strip("/").split("/")[0]
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", vid):
            return vid
    return ""


def _is_allowed_youtube_url(value: str | None) -> bool:
    raw = (value or "").strip()
    if not raw:
        return False
    try:
        parsed = urllib.parse.urlparse(raw)
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    return parsed.scheme in {"http", "https"} and host in {"youtube.com", "www.youtube.com", "music.youtube.com", "m.youtube.com", "youtu.be"}


def _youtube_collection_hint(value: str | None) -> bool:
    raw = (value or "").strip()
    if not raw:
        return False
    try:
        parsed = urllib.parse.urlparse(raw)
    except Exception:
        return False
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host not in {"youtube.com", "music.youtube.com", "m.youtube.com", "youtu.be"}:
        return False
    qs = urllib.parse.parse_qs(parsed.query or "")
    parts = [p for p in (parsed.path or "").split("/") if p]
    if qs.get("list"):
        return True
    if parts and parts[0] in {"playlist", "browse"}:
        return True
    return False


def _safe_int_ms_from_seconds(value: Any) -> int:
    try:
        duration = float(value or 0)
    except Exception:
        return 0
    return int(duration * 1000) if duration > 0 else 0


def _metadata_from_youtube_info(info: dict[str, Any], *, fallback_video_id: str = "", collection_title: str = "", collection_artist: str = "") -> dict[str, Any]:
    if not isinstance(info, dict):
        return {}
    video_id = str(info.get("id") or info.get("url") or fallback_video_id or "").strip()
    # Flat playlist extraction sometimes returns url as the video id.
    if video_id and not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        extracted = _extract_youtube_video_id(video_id)
        video_id = extracted or video_id
    title = str(info.get("track") or info.get("title") or "").strip()
    artist = str(info.get("artist") or info.get("creator") or info.get("uploader") or info.get("channel") or collection_artist or "").strip()
    album = str(info.get("album") or collection_title or "").strip()
    thumb = str(info.get("thumbnail") or "").strip()
    if not thumb:
        thumbs = info.get("thumbnails") or []
        if isinstance(thumbs, list) and thumbs:
            best = next((t for t in reversed(thumbs) if isinstance(t, dict) and t.get("url")), None)
            if best:
                thumb = str(best.get("url") or "").strip()
    return {
        "title": title,
        "artist": artist,
        "album": album,
        "duration_ms": _safe_int_ms_from_seconds(info.get("duration")),
        "art_url": thumb,
        "yt_video_id": video_id if re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id or "") else str(fallback_video_id or "").strip(),
    }


def _youtube_entries_from_url(url: str, video_id: str = "") -> list[dict[str, Any]]:
    target = (url or "").strip()
    if not target and video_id:
        target = f"https://music.youtube.com/watch?v={video_id}"
    if not target:
        return []
    if not _is_allowed_youtube_url(target):
        raise ValueError("Only YouTube or YouTube Music links are allowed")

    is_collection = _youtube_collection_hint(target)
    max_items = max(1, min(500, int(os.getenv("HELIX_LOBBY_YT_LINK_MAX_ITEMS", "100") or "100")))
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        # For playlist/album URLs, keep extraction light enough for a lobby request.
        # For single tracks, use full metadata as before.
        "noplaylist": not is_collection,
        "extract_flat": "in_playlist" if is_collection else False,
        "playlistend": max_items if is_collection else None,
    }
    opts = {k: v for k, v in opts.items() if v is not None}
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(target, download=False) or {}

    if not isinstance(info, dict):
        return []

    if is_collection and isinstance(info.get("entries"), list):
        collection_title = str(info.get("album") or info.get("title") or "").strip()
        collection_artist = str(info.get("artist") or info.get("creator") or info.get("uploader") or info.get("channel") or "").strip()
        entries = [entry for entry in (info.get("entries") or []) if isinstance(entry, dict)]
        out: list[dict[str, Any]] = []
        for entry in entries[:max_items]:
            meta = _metadata_from_youtube_info(entry, collection_title=collection_title, collection_artist=collection_artist)
            if meta.get("yt_video_id") or meta.get("title"):
                out.append(meta)
        return out

    if "entries" in info:
        entries = [entry for entry in (info.get("entries") or []) if isinstance(entry, dict)]
        if entries:
            return [_metadata_from_youtube_info(entries[0], fallback_video_id=video_id)]

    return [_metadata_from_youtube_info(info, fallback_video_id=video_id)]


def _metadata_from_youtube_url(url: str, video_id: str = "") -> dict[str, Any]:
    entries = _youtube_entries_from_url(url, video_id)
    return entries[0] if entries else {}

def _optional_account_user(db: Session, request: Request) -> User | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    session = db.execute(select(SessionToken).where(SessionToken.token == token)).scalar_one_or_none()
    if not session:
        return None
    user = db.get(User, session.user_id)
    if not user or not user.is_active:
        return None
    session.last_seen_at = _now()
    db.add(session)
    return user


def _lobby_token(request: Request) -> str:
    return (request.headers.get(LOBBY_TOKEN_HEADER) or request.cookies.get(LOBBY_TOKEN_COOKIE) or "").strip()


def _get_lobby(db: Session, lobby_id: str) -> SharedLobby:
    lobby = db.get(SharedLobby, lobby_id)
    if not lobby:
        raise HTTPException(status_code=404, detail="Lobby not found")
    return lobby


def _actor_for_lobby(db: Session, request: Request, lobby: SharedLobby) -> tuple[User | None, SharedLobbyMember]:
    user = _optional_account_user(db, request)
    if user and user.id == lobby.host_user_id:
        member = db.execute(
            select(SharedLobbyMember).where(
                SharedLobbyMember.lobby_id == lobby.id,
                SharedLobbyMember.user_id == user.id,
                SharedLobbyMember.role == "host",
            )
        ).scalar_one_or_none()
        if not member:
            member = SharedLobbyMember(
                lobby_id=lobby.id,
                user_id=user.id,
                nickname=user.username,
                role="host",
                token=None,
                permissions_json=_dump_permissions(_load_permissions(None, host=True)),
                is_active=True,
            )
            db.add(member)
            db.flush()
        member.last_seen_at = _now()
        member.is_active = True
        db.add(member)
        return user, member

    token = _lobby_token(request)
    if token:
        member = db.execute(
            select(SharedLobbyMember).where(
                SharedLobbyMember.lobby_id == lobby.id,
                SharedLobbyMember.token == token,
                SharedLobbyMember.role == "guest",
            )
        ).scalar_one_or_none()
        if member and member.is_active:
            member.last_seen_at = _now()
            db.add(member)
            return None, member

    raise HTTPException(status_code=401, detail="Lobby authentication required")


def _require_host(lobby: SharedLobby, user: User) -> None:
    if lobby.host_user_id != user.id:
        raise HTTPException(status_code=403, detail="Lobby host only")


def _member_permissions(member: SharedLobbyMember) -> LobbyPermissions:
    return _load_permissions(member.permissions_json, host=(member.role == "host"))


def _effective_position_ms(lobby: SharedLobby, now: datetime | None = None) -> int:
    pos = max(0, int(lobby.position_ms or 0))
    if not lobby.is_playing:
        return pos
    now = now or _now()
    anchor = lobby.position_updated_at or now
    elapsed = max(0, int((now - anchor).total_seconds() * 1000))
    return pos + elapsed


def _queue_rows(db: Session, lobby_id: str) -> list[SharedLobbyQueueItem]:
    return db.execute(
        select(SharedLobbyQueueItem)
        .where(SharedLobbyQueueItem.lobby_id == lobby_id)
        .order_by(SharedLobbyQueueItem.position.asc(), SharedLobbyQueueItem.created_at.asc())
    ).scalars().all()


def _history_rows(db: Session, lobby_id: str, limit: int = 25) -> list[SharedLobbyHistoryItem]:
    return db.execute(
        select(SharedLobbyHistoryItem)
        .where(SharedLobbyHistoryItem.lobby_id == lobby_id)
        .order_by(SharedLobbyHistoryItem.played_at.desc())
        .limit(max(1, min(200, int(limit or 25))))
    ).scalars().all()


def _to_history_response(item: SharedLobbyHistoryItem) -> LobbyHistoryItemResponse:
    return LobbyHistoryItemResponse(
        id=item.id,
        queue_item_id=item.queue_item_id or "",
        title=item.title or "",
        artist=item.artist or "",
        album=item.album or "",
        duration_ms=int(item.duration_ms or 0),
        art_url=item.art_url or "",
        source=item.source or "",
        subsonic_song_id=item.subsonic_song_id or "",
        yt_video_id=item.yt_video_id or "",
        added_by_member_id=item.added_by_member_id or "",
        added_by_nickname=item.added_by_nickname or "",
        played_at=_iso(item.played_at),
    )


def _record_now_playing_history(db: Session, lobby: SharedLobby) -> None:
    """Record a snapshot when a different queue item becomes active playback."""
    if not lobby.is_playing:
        return
    rows = _queue_rows(db, lobby.id)
    index = int(lobby.current_index or 0)
    if not (0 <= index < len(rows)):
        return
    item = rows[index]
    if (lobby.last_history_queue_item_id or "") == item.id:
        return

    nickname = ""
    if item.added_by_member_id:
        member = db.get(SharedLobbyMember, item.added_by_member_id)
        if member:
            nickname = member.nickname or "Guest"

    db.add(SharedLobbyHistoryItem(
        lobby_id=lobby.id,
        queue_item_id=item.id,
        added_by_member_id=item.added_by_member_id,
        added_by_nickname=nickname,
        title=item.title or "",
        artist=item.artist or "",
        album=item.album or "",
        duration_ms=int(item.duration_ms or 0),
        art_url=_lobby_art_url(lobby.id, item),
        source=item.source or "",
        subsonic_song_id=item.subsonic_song_id or "",
        yt_video_id=item.yt_video_id or "",
        played_at=_now(),
    ))
    lobby.last_history_queue_item_id = item.id
    db.add(lobby)


def _pending_queue_count_for_member(
    rows: list[SharedLobbyQueueItem],
    lobby: SharedLobby,
    member_id: str,
) -> int:
    """Count tracks still waiting for a member, excluding current/played tracks."""
    current_index = max(0, int(lobby.current_index or 0))
    return sum(
        1
        for item in rows
        if item.added_by_member_id == member_id and int(item.position or 0) > current_index
    )


def _renumber_queue(db: Session, lobby_id: str) -> list[SharedLobbyQueueItem]:
    rows = _queue_rows(db, lobby_id)

    # SQLite checks UNIQUE(lobby_id, position) row-by-row during UPDATE. If we
    # directly change 4->3 while another row still has position 3, renumbering can
    # fail even though the final positions would be unique. Move rows into a
    # temporary negative range first, flush, then assign final compact positions.
    for index, item in enumerate(rows):
        item.position = -100000 - index
        db.add(item)
    db.flush()

    for index, item in enumerate(rows):
        item.position = index
        db.add(item)
    db.flush()
    return rows

def _to_queue_item_response(item: SharedLobbyQueueItem, member_names: dict[str, str] | None = None) -> LobbyQueueItemResponse:
    member_names = member_names or {}
    added_by_id = item.added_by_member_id or ""
    return LobbyQueueItemResponse(
        id=item.id,
        position=int(item.position or 0),
        title=item.title or "",
        artist=item.artist or "",
        album=item.album or "",
        duration_ms=int(item.duration_ms or 0),
        art_url=_lobby_art_url(item.lobby_id, item),
        source=item.source or "",
        subsonic_song_id=item.subsonic_song_id or "",
        yt_video_id=item.yt_video_id or "",
        yt_browse_id=item.yt_browse_id or "",
        mb_recording_id=item.mb_recording_id or "",
        mb_artist_id=item.mb_artist_id or "",
        station_id=getattr(item, "station_id", "") or "",
        station_name=getattr(item, "station_name", "") or "",
        added_by_member_id=added_by_id,
        added_by_nickname=member_names.get(added_by_id, ""),
        created_at=_iso(item.created_at),
    )


def _to_member_response(member: SharedLobbyMember) -> LobbyMemberResponse:
    return LobbyMemberResponse(
        id=member.id,
        nickname=member.nickname or "Guest",
        role=member.role or "guest",
        is_active=bool(member.is_active),
        permissions=_member_permissions(member),
        joined_at=_iso(member.joined_at),
        last_seen_at=_iso(member.last_seen_at),
    )


def _to_lobby_state(db: Session, lobby: SharedLobby, actor: SharedLobbyMember | None = None, *, include_invite: bool = False) -> LobbyStateResponse:
    now = _now()
    members = db.execute(
        select(SharedLobbyMember)
        .where(SharedLobbyMember.lobby_id == lobby.id)
        .order_by(SharedLobbyMember.joined_at.asc())
    ).scalars().all()
    member_names = {m.id: (m.nickname or "Guest") for m in members}
    queue = _queue_rows(db, lobby.id)
    now_playing = queue[lobby.current_index] if 0 <= int(lobby.current_index or 0) < len(queue) else None
    actor_perms = _member_permissions(actor) if actor else LobbyPermissions()
    active_station_id = (getattr(lobby, "active_station_id", "") or "").strip()
    active_station_name = ""
    if active_station_id:
        station = db.get(Station, active_station_id)
        if station and station.user_id == lobby.host_user_id:
            active_station_name = station.name or "Station"
    return LobbyStateResponse(
        id=lobby.id,
        name=lobby.name or "Shared Lobby",
        host_user_id=lobby.host_user_id,
        invite_code=lobby.invite_code if include_invite else None,
        has_password=bool(lobby.password_hash),
        is_open=bool(lobby.is_open),
        guest_permissions=_load_permissions(lobby.permissions_json),
        guest_queue_limit=max(0, int(lobby.guest_queue_limit or 0)),
        cleanup_after_days=max(0, int(lobby.cleanup_after_days or 0)),
        active_station_id=active_station_id,
        active_station_name=active_station_name,
        self_member_id=actor.id if actor else "",
        self_role=(actor.role if actor else "guest"),
        self_permissions=actor_perms,
        is_playing=bool(lobby.is_playing),
        current_index=int(lobby.current_index or 0),
        position_ms=int(lobby.position_ms or 0),
        effective_position_ms=_effective_position_ms(lobby, now),
        server_time_ms=_server_time_ms(),
        position_updated_at=_iso(lobby.position_updated_at),
        now_playing=_to_queue_item_response(now_playing, member_names) if now_playing else None,
        queue=[_to_queue_item_response(item, member_names) for item in queue],
        members=[_to_member_response(m) for m in members],
        history=[_to_history_response(item) for item in _history_rows(db, lobby.id, 25)],
        created_at=_iso(lobby.created_at),
        updated_at=_iso(lobby.updated_at),
    )


def _touch_lobby(lobby: SharedLobby) -> None:
    lobby.updated_at = _now()


def _commit_lobby_state(db: Session, lobby: SharedLobby, actor: SharedLobbyMember, *, include_invite: bool = False) -> LobbyStateResponse:
    # Build the response before commit so SQLAlchemy's expire-on-commit behavior
    # cannot trigger lazy refreshes while rendering high-frequency lobby state.
    db.flush()
    response = _to_lobby_state(db, lobby, actor, include_invite=include_invite)
    db.commit()
    schedule_lobby_state_broadcast(lobby.id)
    return response


@router.post("", response_model=LobbyStateResponse)
def create_lobby(payload: LobbyCreateRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    name = _clean_text(payload.name, 180) or "Shared Lobby"
    # Avoid rare invite collisions.
    for _ in range(8):
        invite = _new_invite_code()
        if not db.execute(select(SharedLobby).where(SharedLobby.invite_code == invite)).scalar_one_or_none():
            break
    else:
        raise HTTPException(status_code=500, detail="Could not generate lobby invite")

    lobby = SharedLobby(
        host_user_id=user.id,
        name=name,
        invite_code=invite,
        password_hash=hash_password(payload.password) if (payload.password or "").strip() else "",
        permissions_json=_dump_permissions(payload.guest_permissions),
        is_open=True,
        guest_queue_limit=max(0, int(payload.guest_queue_limit or 0)),
        cleanup_after_days=max(0, int(payload.cleanup_after_days or 0)),
        is_playing=False,
        position_ms=0,
        position_updated_at=_now(),
    )
    db.add(lobby)
    db.flush()
    host_member = SharedLobbyMember(
        lobby_id=lobby.id,
        user_id=user.id,
        nickname=user.username,
        role="host",
        permissions_json=_dump_permissions(_load_permissions(None, host=True)),
        is_active=True,
    )
    db.add(host_member)
    db.commit()
    db.refresh(lobby)
    return _to_lobby_state(db, lobby, host_member, include_invite=True)


@router.get("", response_model=LobbyListResponse)
def list_host_lobbies(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.execute(
        select(SharedLobby)
        .where(SharedLobby.host_user_id == user.id)
        .order_by(SharedLobby.updated_at.desc())
    ).scalars().all()
    states: list[LobbyStateResponse] = []
    for lobby in rows:
        member = db.execute(
            select(SharedLobbyMember).where(
                SharedLobbyMember.lobby_id == lobby.id,
                SharedLobbyMember.user_id == user.id,
                SharedLobbyMember.role == "host",
            )
        ).scalar_one_or_none()
        states.append(_to_lobby_state(db, lobby, member, include_invite=True))
    return LobbyListResponse(lobbies=states)


@router.post("/join", response_model=LobbyJoinResponse)
def join_lobby(payload: LobbyJoinRequest, response: Response, db: Session = Depends(get_db)):
    invite = _clean_text(payload.invite_code, 128).upper()
    nickname = _clean_text(payload.nickname, 80)
    if not re.fullmatch(r"[A-Z]{5}", invite):
        raise HTTPException(status_code=400, detail="Lobby code must be exactly 5 letters")
    if not nickname:
        raise HTTPException(status_code=400, detail="nickname is required")

    lobby = db.execute(select(SharedLobby).where(SharedLobby.invite_code == invite)).scalar_one_or_none()
    if not lobby or not lobby.is_open:
        raise HTTPException(status_code=404, detail="Lobby not found or closed")
    if lobby.password_hash:
        supplied_password = payload.password or ""
        if not supplied_password or not verify_password(supplied_password, lobby.password_hash):
            raise HTTPException(status_code=403, detail="Incorrect lobby password")

    member = SharedLobbyMember(
        lobby_id=lobby.id,
        nickname=nickname,
        role="guest",
        token=_new_guest_token(),
        permissions_json=lobby.permissions_json,
        is_active=True,
    )
    db.add(member)
    db.commit()
    db.refresh(member)
    response.set_cookie(LOBBY_TOKEN_COOKIE, member.token or "", httponly=False, samesite="lax", secure=cookie_secure())
    return LobbyJoinResponse(
        guest_token=member.token or "",
        member=_to_member_response(member),
        lobby=_to_lobby_state(db, lobby, member, include_invite=False),
    )


@router.get("/join/{invite_code}/resume", response_model=LobbyStateResponse)
def resume_joined_lobby(invite_code: str, request: Request, db: Session = Depends(get_db)):
    invite = _clean_text(invite_code, 128).upper()
    if not invite:
        raise HTTPException(status_code=400, detail="invite_code is required")

    lobby = db.execute(select(SharedLobby).where(SharedLobby.invite_code == invite)).scalar_one_or_none()
    if not lobby:
        raise HTTPException(status_code=404, detail="Lobby not found")

    # A locked lobby rejects new joins, but members who already possess a valid
    # lobby token must still be able to resume/reconnect.
    _, actor = _actor_for_lobby(db, request, lobby)
    include_invite = actor.role == "host"
    response = _to_lobby_state(db, lobby, actor, include_invite=include_invite)
    db.commit()
    return response


@router.get("/{lobby_id}/search/{mode}", response_model=dict[str, Any])
async def lobby_search(
    lobby_id: str,
    mode: str,
    request: Request,
    q: str = Query(..., description="Search query"),
    song_limit: int = Query(20, ge=1, le=50),
    album_limit: int = Query(20, ge=0, le=50),
    subsonic_limit: int = Query(3, ge=0, le=10),
    db: Session = Depends(get_db),
):
    """Lobby-safe Helix search.

    Guests do not have normal account auth, so lobby search cannot call the
    account-authenticated /api/search endpoints directly. Validate lobby access
    and add-to-queue permission here, then run the same underlying search helpers.
    """
    lobby = _get_lobby(db, lobby_id)
    _, actor = _actor_for_lobby(db, request, lobby)
    perms = _member_permissions(actor)
    if not perms.can_add_to_queue:
        raise HTTPException(status_code=403, detail="You cannot add to this lobby queue")

    qq = (q or "").strip()
    search_mode = (mode or "hybrid").strip().lower()
    if search_mode not in {"hybrid", "subsonic", "ytmusic"}:
        raise HTTPException(status_code=400, detail="Unsupported search mode")
    if not qq:
        return {"mode": search_mode, "songs": [], "albums": []}

    ip = _search_client_ip(request)
    if not SEARCH_RATE_LIMITER.allow(
        search_make_key(scope=f"lobby_search:{search_mode}", user_id=actor.id, ip=ip),
        limit=30,
        window_s=30,
    ):
        raise HTTPException(status_code=429, detail="Too many requests")

    settings = _search_load_settings_short()
    subsonic_enabled = bool(
        str(settings.get("subsonic_base_url") or "").strip()
        and str(settings.get("subsonic_username") or "").strip()
        and str(settings.get("subsonic_password") or "").strip()
    )

    if search_mode == "subsonic" and not subsonic_enabled:
        return {"mode": "subsonic", "songs": [], "albums": []}

    cache_key = (
        f"lobby_search:{search_mode}|{qq}|{song_limit}|{album_limit}|{subsonic_limit}|"
        f"sub={1 if subsonic_enabled else 0}"
    )
    hit = SEARCH_CACHE.get(cache_key)
    if hit is not None:
        return hit

    ytm_timeout_s = max(3.0, float(os.getenv("HELIX_LOBBY_YTMUSIC_SEARCH_TIMEOUT_S", "15") or "15"))
    sub_timeout_s = max(3.0, float(os.getenv("HELIX_LOBBY_SUBSONIC_SEARCH_TIMEOUT_S", "12") or "12"))

    async def _yt_search_safe(song_count: int, album_count: int) -> dict[str, Any]:
        try:
            raw = await asyncio.wait_for(
                asyncio.to_thread(_ytmusic_search, qq, song_limit=song_count, album_limit=album_count),
                timeout=ytm_timeout_s,
            )
            return _search_mark_ytmusic(raw)
        except (asyncio.TimeoutError, Exception):
            return {"songs": [], "albums": []}

    async def _sub_search_safe(song_count: int, album_count: int) -> dict[str, Any]:
        if not subsonic_enabled:
            return {"songs": [], "albums": []}
        try:
            return await asyncio.wait_for(
                _search_subsonic_only(settings, qq, song_count, album_count),
                timeout=sub_timeout_s,
            )
        except (asyncio.TimeoutError, Exception):
            return {"songs": [], "albums": []}

    if search_mode == "ytmusic":
        payload = await _yt_search_safe(song_limit, album_limit)
        payload["mode"] = "ytmusic"
    elif search_mode == "subsonic":
        payload = await _sub_search_safe(song_limit, album_limit)
        payload["mode"] = "subsonic"
    else:
        # Run both providers concurrently. A slow/down YTM or Subsonic source
        # must not leave the lobby's "All" search spinning forever.
        yt_task = _yt_search_safe(song_limit, album_limit)
        sub_task = (
            _sub_search_safe(subsonic_limit, subsonic_limit)
            if subsonic_limit > 0
            else asyncio.sleep(0, result={"songs": [], "albums": []})
        )
        yt_payload, sub_payload = await asyncio.gather(yt_task, sub_task)
        sub_song_keys = {_search_song_key(item) for item in sub_payload.get("songs") or []}
        sub_album_keys = {_search_album_key(item) for item in sub_payload.get("albums") or []}
        yt_songs = [item for item in yt_payload.get("songs") or [] if _search_song_key(item) not in sub_song_keys]
        yt_albums = [item for item in yt_payload.get("albums") or [] if _search_album_key(item) not in sub_album_keys]
        payload = {
            "mode": "hybrid",
            "songs": list(sub_payload.get("songs") or []) + yt_songs,
            "albums": list(sub_payload.get("albums") or []) + yt_albums,
        }

    SEARCH_CACHE.set(cache_key, payload, ttl_seconds=60 * 2)
    return payload


@router.get("/{lobby_id}/state", response_model=LobbyStateResponse)
def lobby_state(lobby_id: str, request: Request, db: Session = Depends(get_db)):
    lobby = _get_lobby(db, lobby_id)
    _, actor = _actor_for_lobby(db, request, lobby)
    include_invite = actor.role == "host"
    response = _to_lobby_state(db, lobby, actor, include_invite=include_invite)
    # Persist the lightweight last_seen_at touch after the response is built.
    # This avoids SQLAlchemy expiration/lazy reload during high-frequency lobby
    # polling response rendering.
    db.commit()
    return response

@router.get("/{lobby_id}/art/{item_id}")
async def lobby_item_art(lobby_id: str, item_id: str, request: Request):
    """Serve lobby artwork through a lobby-scoped endpoint.

    Guests do not have normal Helix account authentication, so queue items must
    not expose account-only artwork URLs such as /api/art/subsonic/... directly.
    This endpoint validates lobby access, then proxies the artwork using Helix's
    own backend credentials where needed.
    """
    db = SessionLocal()
    try:
        lobby = _get_lobby(db, lobby_id)
        _actor_for_lobby(db, request, lobby)
        item = db.execute(
            select(SharedLobbyQueueItem).where(
                SharedLobbyQueueItem.lobby_id == lobby.id,
                SharedLobbyQueueItem.id == item_id,
            )
        ).scalar_one_or_none()
        if not item:
            raise HTTPException(status_code=404, detail="Lobby queue item not found")

        raw_url = (item.art_url or "").strip()
        yt_video_id = (item.yt_video_id or "").strip()
        subsonic_id = (item.subsonic_song_id or "").strip()
        settings = dict(get_settings(db) or {})
    finally:
        db.close()

    # If a YT fallback is known, use the public thumbnail when no explicit art is stored.
    if not raw_url and yt_video_id:
        raw_url = f"https://i.ytimg.com/vi/{yt_video_id}/hqdefault.jpg"

    # Proxy Helix's authenticated Subsonic art route for lobby guests.
    parsed_relative = urllib.parse.urlparse(raw_url) if raw_url else None
    if parsed_relative and (parsed_relative.path or "").startswith("/api/art/subsonic/"):
        cover_id = urllib.parse.unquote((parsed_relative.path or "").rsplit("/", 1)[-1])
        query = urllib.parse.parse_qs(parsed_relative.query or "")
        try:
            size = int((query.get("size") or ["512"])[0] or 512)
        except Exception:
            size = 512
        client = None
        try:
            base_url = str(settings.get("subsonic_base_url") or "").strip()
            username = str(settings.get("subsonic_username") or "").strip()
            password = str(settings.get("subsonic_password") or "").strip()
            if not base_url or not username or not password:
                raise HTTPException(status_code=404, detail="Artwork unavailable")
            from ..integrations.subsonic import SubsonicClient
            client = SubsonicClient(
                base_url=base_url,
                username=username,
                password=password,
                client_name=str(settings.get("subsonic_client_name") or "Helix"),
                api_version=str(settings.get("subsonic_api_version") or "1.16.1"),
                timeout_s=int(settings.get("subsonic_timeout_s") or 20),
            )
            data = await client.fetch_cover_art_bytes(cover_id, size=max(32, min(2048, size)))
            if not data:
                raise HTTPException(status_code=404, detail="Artwork unavailable")
            return Response(content=data, media_type=_guess_image_content_type(data), headers={"Cache-Control": "public, max-age=86400"})
        finally:
            if client is not None:
                try:
                    await client.close()
                except Exception:
                    pass

    # Last resort for Subsonic-only items with no saved cover URL: try using the song id as cover id.
    if not raw_url and subsonic_id:
        client = None
        try:
            base_url = str(settings.get("subsonic_base_url") or "").strip()
            username = str(settings.get("subsonic_username") or "").strip()
            password = str(settings.get("subsonic_password") or "").strip()
            if base_url and username and password:
                from ..integrations.subsonic import SubsonicClient
                client = SubsonicClient(
                    base_url=base_url,
                    username=username,
                    password=password,
                    client_name=str(settings.get("subsonic_client_name") or "Helix"),
                    api_version=str(settings.get("subsonic_api_version") or "1.16.1"),
                    timeout_s=int(settings.get("subsonic_timeout_s") or 20),
                )
                data = await client.fetch_cover_art_bytes(subsonic_id, size=512)
                if data:
                    return Response(content=data, media_type=_guess_image_content_type(data), headers={"Cache-Control": "public, max-age=86400"})
        finally:
            if client is not None:
                try:
                    await client.close()
                except Exception:
                    pass

    if not raw_url:
        raise HTTPException(status_code=404, detail="Artwork unavailable")

    parsed = urllib.parse.urlparse(raw_url)
    if parsed.scheme not in {"https"} or not is_allowed_art_url(raw_url):
        raise HTTPException(status_code=404, detail="Artwork unavailable")

    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            resp = await client.get(raw_url)
            resp.raise_for_status()
            data = resp.content
            return Response(
                content=data,
                media_type=_guess_image_content_type(data, resp.headers.get("content-type", "")),
                headers={"Cache-Control": "public, max-age=86400"},
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Failed to fetch lobby artwork") from exc


@router.get("/{lobby_id}/stream/{item_id}")
async def stream_lobby_item(lobby_id: str, item_id: str, request: Request):
    """Stream a lobby queue item without requiring a full Helix account for guests.

    The DB session is intentionally opened and closed before returning the
    StreamingResponse so a listening client does not hold a SQLAlchemy
    connection for the life of the audio stream.
    """
    db = SessionLocal()
    try:
        lobby = _get_lobby(db, lobby_id)
        _user, actor = _actor_for_lobby(db, request, lobby)
        item = db.execute(
            select(SharedLobbyQueueItem).where(
                SharedLobbyQueueItem.lobby_id == lobby.id,
                SharedLobbyQueueItem.id == item_id,
            )
        ).scalar_one_or_none()
        if not item:
            raise HTTPException(status_code=404, detail="Lobby queue item not found")

        # If a manually added lobby item only has title/artist, recover a YTMusic
        # source id so guests can still listen without the item being imported.
        if not (item.subsonic_song_id or item.yt_video_id):
            try:
                duration_seconds = int((item.duration_ms or 0) / 1000) if (item.duration_ms or 0) else None
                match = find_track(
                    title=item.title or "",
                    artist=item.artist or "",
                    album=item.album or None,
                    duration_seconds=duration_seconds,
                )
                if match.found and match.video_id:
                    item.yt_video_id = match.video_id
                    item.source = item.source or "ytmusic"
                    if not item.art_url:
                        item.art_url = f"https://i.ytimg.com/vi/{match.video_id}/hqdefault.jpg"
                    db.add(item)
                    db.commit()
            except Exception:
                db.rollback()

        settings = get_settings(db)
        cur = SimpleNamespace(
            id=item.id,
            session_user_id=lobby.host_user_id,
            position=item.position or 0,
            kind="song",
            title=item.title or "",
            artist=item.artist or "",
            album=item.album or "",
            duration_ms=int(item.duration_ms or 0),
            art_url=item.art_url or "",
            source=item.source or ("subsonic" if item.subsonic_song_id else "ytmusic" if item.yt_video_id else ""),
            subsonic_song_id=item.subsonic_song_id or "",
            yt_video_id=item.yt_video_id or "",
            yt_browse_id=item.yt_browse_id or "",
            mb_recording_id=item.mb_recording_id or "",
            mb_artist_id=item.mb_artist_id or "",
            inbound_path="",
            download_status="",
            is_playable=False,
            error="",
        )

        await _ensure_playable_for_stream(db, cur, settings=settings, allow_subsonic_wait=False, progressive_inbound=True)
        if cur.source != "subsonic":
            if (cur.inbound_path or "").endswith(".part") or cur.download_status == "DOWNLOADING":
                response = await _stream_inbound_progressive(request, cur)
            else:
                response = await _stream_inbound_with_range(request, cur)
        else:
            response = await _stream_subsonic_with_range(request, cur, settings=settings)
        return response
    finally:
        db.close()


@router.patch("/{lobby_id}", response_model=LobbyStateResponse)
def update_lobby(lobby_id: str, payload: LobbyUpdateRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    lobby = _get_lobby(db, lobby_id)
    _require_host(lobby, user)
    if payload.name is not None:
        lobby.name = _clean_text(payload.name, 180) or lobby.name
    if payload.is_open is not None:
        lobby.is_open = bool(payload.is_open)
    if payload.guest_permissions is not None:
        permissions_json = _dump_permissions(payload.guest_permissions)
        lobby.permissions_json = permissions_json

        # Global guest permissions are the baseline for every guest currently
        # in the lobby. When the host changes them, keep each guest member's
        # stored effective permissions in sync as well. Individual overrides
        # can still be applied afterward through the per-member update route.
        guests = db.execute(
            select(SharedLobbyMember).where(
                SharedLobbyMember.lobby_id == lobby.id,
                SharedLobbyMember.role == "guest",
            )
        ).scalars().all()
        for guest in guests:
            guest.permissions_json = permissions_json
            db.add(guest)
    if payload.guest_queue_limit is not None:
        lobby.guest_queue_limit = max(0, int(payload.guest_queue_limit))
    if payload.cleanup_after_days is not None:
        lobby.cleanup_after_days = max(0, min(365, int(payload.cleanup_after_days)))
    if "password" in payload.model_fields_set:
        password = (payload.password or "").strip()
        lobby.password_hash = hash_password(password) if password else ""
    _touch_lobby(lobby)
    db.add(lobby)
    db.commit()
    schedule_lobby_state_broadcast(lobby.id)
    member = db.execute(select(SharedLobbyMember).where(SharedLobbyMember.lobby_id == lobby.id, SharedLobbyMember.user_id == user.id, SharedLobbyMember.role == "host")).scalar_one_or_none()
    return _to_lobby_state(db, lobby, member, include_invite=True)


@router.post("/{lobby_id}/invite/regenerate", response_model=LobbyStateResponse)
def regenerate_lobby_invite(lobby_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    lobby = _get_lobby(db, lobby_id)
    _require_host(lobby, user)
    for _ in range(8):
        invite = _new_invite_code()
        if not db.execute(select(SharedLobby).where(SharedLobby.invite_code == invite)).scalar_one_or_none():
            lobby.invite_code = invite
            break
    else:
        raise HTTPException(status_code=500, detail="Could not generate lobby invite")
    _touch_lobby(lobby)
    db.add(lobby)
    db.commit()
    schedule_lobby_state_broadcast(lobby.id)
    host_member = db.execute(select(SharedLobbyMember).where(SharedLobbyMember.lobby_id == lobby.id, SharedLobbyMember.user_id == user.id, SharedLobbyMember.role == "host")).scalar_one_or_none()
    return _to_lobby_state(db, lobby, host_member, include_invite=True)


@router.delete("/{lobby_id}", response_model=LobbyOkResponse)
def delete_lobby(lobby_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    lobby = _get_lobby(db, lobby_id)
    _require_host(lobby, user)
    db.delete(lobby)
    db.commit()
    schedule_lobby_state_broadcast(lobby_id)
    return LobbyOkResponse(ok=True)


def _lobby_queue_item_from_meta(
    *,
    lobby_id: str,
    actor_id: str,
    position: int,
    payload: LobbyQueueAddRequest,
    meta: dict[str, Any] | None = None,
) -> SharedLobbyQueueItem:
    meta = meta or {}
    yt_video_id = _clean_text(meta.get("yt_video_id") or payload.yt_video_id, 160)
    title = _clean_text(meta.get("title") or payload.title, 400)
    artist = _clean_text(meta.get("artist") or payload.artist, 400)
    album = _clean_text(meta.get("album") or payload.album, 400)
    duration_ms = max(0, int(meta.get("duration_ms") or payload.duration_ms or 0))
    art_url = _clean_text(meta.get("art_url") or payload.art_url, 1200)

    if yt_video_id and not art_url:
        art_url = f"https://i.ytimg.com/vi/{yt_video_id}/hqdefault.jpg"
    if yt_video_id and not title:
        title = "YouTube track"
    if yt_video_id and not artist:
        artist = "YouTube Music"

    if not title or not artist:
        raise HTTPException(status_code=400, detail="title and artist are required unless a playable YouTube link is provided")

    source = _clean_text(payload.source, 32) or ("subsonic" if payload.subsonic_song_id else "ytmusic" if yt_video_id else "")
    return SharedLobbyQueueItem(
        lobby_id=lobby_id,
        added_by_member_id=actor_id,
        position=position,
        title=title,
        artist=artist,
        album=album,
        duration_ms=duration_ms,
        art_url=art_url,
        source=source,
        subsonic_song_id=_clean_text(payload.subsonic_song_id, 160),
        yt_video_id=yt_video_id,
        yt_browse_id=_clean_text(payload.yt_browse_id, 240),
        mb_recording_id=_clean_text(payload.mb_recording_id, 80),
        mb_artist_id=_clean_text(payload.mb_artist_id, 80),
    )


@router.post("/{lobby_id}/queue", response_model=LobbyStateResponse)
def add_queue_item(lobby_id: str, payload: LobbyQueueAddRequest, request: Request, db: Session = Depends(get_db)):
    lobby = _get_lobby(db, lobby_id)
    _, actor = _actor_for_lobby(db, request, lobby)
    perms = _member_permissions(actor)
    if not perms.can_add_to_queue:
        raise HTTPException(status_code=403, detail="You cannot add to this lobby queue")

    yt_url = _clean_text(getattr(payload, "ytmusic_url", None), 1200)
    yt_video_id = _clean_text(payload.yt_video_id, 160) or _extract_youtube_video_id(yt_url)

    metas: list[dict[str, Any]] = []
    if yt_url:
        try:
            metas = _youtube_entries_from_url(yt_url, yt_video_id)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Could not read that YouTube link: {exc}") from exc
        if not metas:
            raise HTTPException(status_code=400, detail="Could not find playable tracks in that YouTube link")
    else:
        metas = [{
            "yt_video_id": yt_video_id,
            "title": payload.title,
            "artist": payload.artist,
            "album": payload.album,
            "duration_ms": payload.duration_ms or 0,
            "art_url": payload.art_url,
        }]

    rows = _queue_rows(db, lobby.id)
    base_pos = len(rows)
    pending_items: list[SharedLobbyQueueItem] = []
    for meta in metas:
        try:
            item = _lobby_queue_item_from_meta(
                lobby_id=lobby.id,
                actor_id=actor.id,
                position=base_pos + len(pending_items),
                payload=payload,
                meta=meta,
            )
        except HTTPException:
            if len(metas) == 1:
                raise
            continue
        pending_items.append(item)

    if not pending_items:
        raise HTTPException(status_code=400, detail="Could not find playable tracks in that YouTube link")

    if actor.role != "host":
        limit = max(0, int(lobby.guest_queue_limit or 0))
        if limit > 0:
            pending_count = _pending_queue_count_for_member(rows, lobby, actor.id)
            remaining = max(0, limit - pending_count)
            if len(pending_items) > remaining:
                if remaining <= 0:
                    detail = f"You already have {pending_count} pending track{'s' if pending_count != 1 else ''}; this lobby allows {limit} per guest"
                else:
                    detail = f"You can only add {remaining} more track{'s' if remaining != 1 else ''}; this lobby allows {limit} pending per guest"
                raise HTTPException(status_code=409, detail=detail)

    for item in pending_items:
        db.add(item)

    if len(rows) == 0:
        lobby.current_index = 0
        lobby.position_ms = 0
        lobby.position_updated_at = _now()
    _touch_lobby(lobby)
    response = _commit_lobby_state(db, lobby, actor, include_invite=(actor.role == "host"))
    if (getattr(lobby, "active_station_id", "") or "").strip():
        schedule_lobby_station_fill(lobby.id)
    return response


@router.delete("/{lobby_id}/queue", response_model=LobbyStateResponse)
def clear_queue_items(lobby_id: str, request: Request, db: Session = Depends(get_db)):
    lobby = _get_lobby(db, lobby_id)
    _, actor = _actor_for_lobby(db, request, lobby)
    perms = _member_permissions(actor)
    if not perms.can_remove_any_queue_item:
        raise HTTPException(status_code=403, detail="You cannot clear this lobby queue")

    try:
        db.execute(
            delete(SharedLobbyQueueItem)
            .where(SharedLobbyQueueItem.lobby_id == lobby.id)
            .execution_options(synchronize_session=False)
        )
        lobby.current_index = 0
        lobby.position_ms = 0
        lobby.is_playing = False
        lobby.last_history_queue_item_id = ""
        lobby.position_updated_at = _now()
        _touch_lobby(lobby)
        db.add(lobby)
        db.flush()

        # Build the response after the delete has flushed so the returned state
        # cannot include the removed rows.
        response = _to_lobby_state(db, lobby, actor, include_invite=(actor.role == "host"))
        response.queue = []
        response.now_playing = None
        response.current_index = 0
        response.is_playing = False
        response.position_ms = 0
        response.effective_position_ms = 0
        db.commit()
        schedule_lobby_state_broadcast(lobby.id)
        return response
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Could not clear lobby queue: {exc}") from exc


@router.patch("/{lobby_id}/queue/reorder", response_model=LobbyStateResponse)
@router.post("/{lobby_id}/queue/reorder", response_model=LobbyStateResponse)
def reorder_queue_items(lobby_id: str, payload: LobbyQueueReorderRequest, request: Request, db: Session = Depends(get_db)):
    """Reorder the lobby queue.

    This static route must be registered before /{lobby_id}/queue/{item_id};
    otherwise FastAPI can treat "reorder" as an item id and return 405 for PATCH.
    """
    lobby = _get_lobby(db, lobby_id)
    _, actor = _actor_for_lobby(db, request, lobby)
    perms = _member_permissions(actor)
    if not (perms.can_control_playback or actor.role == "host"):
        raise HTTPException(status_code=403, detail="You cannot reorder this lobby queue")

    requested_ids: list[str] = []
    seen: set[str] = set()
    for raw_id in getattr(payload, "item_ids", []) or []:
        item_id = _clean_text(raw_id, 80)
        if item_id and item_id not in seen:
            requested_ids.append(item_id)
            seen.add(item_id)

    if not requested_ids:
        raise HTTPException(status_code=400, detail="Reorder payload must include queue item ids")

    try:
        db.rollback()
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")

        rows = _queue_rows(db, lobby.id)
        if not rows:
            db.rollback()
            return _to_lobby_state(db, lobby, actor, include_invite=(actor.role == "host"))

        by_id = {row.id: row for row in rows}
        for item_id in requested_ids:
            if item_id not in by_id:
                db.rollback()
                raise HTTPException(status_code=400, detail="Reorder payload contains an unknown queue item")

        current_index = int(lobby.current_index or 0)
        current_item_id = rows[current_index].id if 0 <= current_index < len(rows) else ""

        ordered_rows = [by_id[item_id] for item_id in requested_ids]
        ordered_rows.extend(row for row in rows if row.id not in seen)

        # UNIQUE(lobby_id, position) is checked row-by-row in SQLite. Move all
        # rows into a temporary negative range first, then assign final positions.
        for offset, row in enumerate(rows):
            row.position = -100000 - offset
            db.add(row)
        db.flush()

        for position, row in enumerate(ordered_rows):
            row.position = position
            db.add(row)

        if current_item_id:
            lobby.current_index = next(
                (idx for idx, row in enumerate(ordered_rows) if row.id == current_item_id),
                min(current_index, len(ordered_rows) - 1),
            )
        else:
            lobby.current_index = min(current_index, max(0, len(ordered_rows) - 1))

        _touch_lobby(lobby)
        db.add(lobby)
        db.flush()
        response = _to_lobby_state(db, lobby, actor, include_invite=(actor.role == "host"))
        db.commit()
        schedule_lobby_state_broadcast(lobby.id)
        return response
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Could not reorder lobby queue: {exc}") from exc


@router.post("/{lobby_id}/queue/{item_id}/play", response_model=LobbyStateResponse)
def play_queue_item(lobby_id: str, item_id: str, request: Request, db: Session = Depends(get_db)):
    """Jump lobby playback to a specific queue item.

    The frontend uses this when someone clicks a queue row. This route was
    missing after a merge, so the browser was POSTing to a path that only had
    nearby queue routes and getting 405 Method Not Allowed.
    """
    lobby = _get_lobby(db, lobby_id)
    _, actor = _actor_for_lobby(db, request, lobby)
    perms = _member_permissions(actor)
    if not (perms.can_skip or perms.can_control_playback):
        raise HTTPException(status_code=403, detail="You cannot jump lobby playback")

    rows = _queue_rows(db, lobby.id)
    index = next((idx for idx, row in enumerate(rows) if row.id == item_id), None)
    if index is None:
        raise HTTPException(status_code=404, detail="Queue item not found")

    lobby.current_index = index
    lobby.position_ms = 0
    lobby.is_playing = True
    lobby.position_updated_at = _now()
    _record_now_playing_history(db, lobby)
    _touch_lobby(lobby)
    return _commit_lobby_state(db, lobby, actor, include_invite=(actor.role == "host"))


@router.delete("/{lobby_id}/queue/{item_id}", response_model=LobbyStateResponse)
def remove_queue_item(lobby_id: str, item_id: str, request: Request, db: Session = Depends(get_db)):
    lobby = _get_lobby(db, lobby_id)
    _, actor = _actor_for_lobby(db, request, lobby)
    perms = _member_permissions(actor)
    include_invite = actor.role == "host"

    # Read only primitive fields needed for permission/current-index math.
    # Avoid keeping a loaded queue ORM object in the session and then deleting it,
    # because that path can raise stale-object errors when multiple clients/polls
    # touch the queue around the same time.
    row = db.execute(
        select(SharedLobbyQueueItem.position, SharedLobbyQueueItem.added_by_member_id).where(
            SharedLobbyQueueItem.lobby_id == lobby.id,
            SharedLobbyQueueItem.id == item_id,
        )
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Queue item not found")

    removed_pos = int(row[0] or 0)
    added_by_member_id = row[1] or ""
    owns_item = added_by_member_id == actor.id
    if not (perms.can_remove_any_queue_item or (owns_item and perms.can_remove_own_queue_items)):
        raise HTTPException(status_code=403, detail="You cannot remove this queue item")

    try:
        result = db.execute(
            delete(SharedLobbyQueueItem)
            .where(
                SharedLobbyQueueItem.lobby_id == lobby.id,
                SharedLobbyQueueItem.id == item_id,
            )
            .execution_options(synchronize_session=False)
        )
        if getattr(result, "rowcount", 0) == 0:
            db.rollback()
            raise HTTPException(status_code=404, detail="Queue item not found")

        db.flush()
        rows = _renumber_queue(db, lobby.id)
        if not rows:
            lobby.current_index = 0
            lobby.position_ms = 0
            lobby.is_playing = False
            lobby.position_updated_at = _now()
        else:
            current_index = int(lobby.current_index or 0)
            if removed_pos < current_index:
                lobby.current_index = max(0, current_index - 1)
            elif removed_pos == current_index:
                lobby.current_index = min(current_index, len(rows) - 1)
                lobby.position_ms = 0
                lobby.position_updated_at = _now()
            elif current_index >= len(rows):
                lobby.current_index = len(rows) - 1

        _record_now_playing_history(db, lobby)
        _touch_lobby(lobby)
        db.add(lobby)
        db.flush()

        # Build the response before commit so SQLAlchemy expiration after commit
        # cannot turn response rendering into a 500.
        response = _to_lobby_state(db, lobby, actor, include_invite=include_invite)
        db.commit()
        schedule_lobby_state_broadcast(lobby.id)
        return response
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Could not remove lobby queue item: {exc}") from exc

@router.post("/{lobby_id}/station/{station_id}/play", response_model=LobbyStateResponse)
async def play_lobby_station(lobby_id: str, station_id: str, request: Request, db: Session = Depends(get_db)):
    lobby = _get_lobby(db, lobby_id)
    user, actor = _actor_for_lobby(db, request, lobby)
    if actor.role != "host" or not user or user.id != lobby.host_user_id:
        raise HTTPException(status_code=403, detail="Only the lobby host can start a station")
    station = db.get(Station, station_id)
    if not station or station.user_id != user.id:
        raise HTTPException(status_code=404, detail="Station not found")

    # Starting/changing a station must not destroy the shared queue. The
    # station becomes the auto-fill source for the existing queue. If the
    # lobby already has tracks, "Play station" should also resume playback
    # immediately; station generation is allowed to retry in the background
    # without deactivating the station just because one refill attempt failed.
    rows = _queue_rows(db, lobby.id)
    queue_was_empty = len(rows) == 0
    lobby.active_station_id = station.id
    if rows:
        lobby.current_index = max(0, min(int(lobby.current_index or 0), len(rows) - 1))
        # Changing/starting the station only changes the lobby's auto-fill
        # source.  Do not reset the timing anchor for a track that is already
        # playing: doing so makes clients seek back to the stored position_ms
        # and can appear to restart the current track.  If playback is paused,
        # Play station still resumes it from the stored paused position.
        if not lobby.is_playing:
            lobby.is_playing = True
            lobby.position_updated_at = _now()
    _touch_lobby(lobby)
    db.commit()
    schedule_lobby_state_broadcast(lobby.id)
    try:
        await fill_lobby_station(lobby.id, start_if_empty=queue_was_empty)
    except StationSeedArtistNotFound as exc:
        # An empty lobby cannot start without a generated current track.
        # Existing queues may keep playing while the station monitor retries.
        if queue_was_empty:
            failed_db = SessionLocal()
            try:
                failed = failed_db.get(SharedLobby, lobby.id)
                if failed:
                    failed.active_station_id = ""
                    failed_db.commit()
                    schedule_lobby_state_broadcast(lobby.id)
            finally:
                failed_db.close()
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        LOG.warning("Initial lobby station refill failed lobby=%s station=%s err=%s", lobby.id, station.id, exc)
    except StationGenerationError as exc:
        if queue_was_empty:
            failed_db = SessionLocal()
            try:
                failed = failed_db.get(SharedLobby, lobby.id)
                if failed:
                    failed.active_station_id = ""
                    failed_db.commit()
                    schedule_lobby_state_broadcast(lobby.id)
            finally:
                failed_db.close()
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        LOG.warning("Initial lobby station refill failed lobby=%s station=%s err=%s", lobby.id, station.id, exc)
    db.expire_all()
    lobby = _get_lobby(db, lobby_id)
    _, actor = _actor_for_lobby(db, request, lobby)
    return _to_lobby_state(db, lobby, actor, include_invite=True)


@router.post("/{lobby_id}/station/stop", response_model=LobbyStateResponse)
def stop_lobby_station(lobby_id: str, request: Request, db: Session = Depends(get_db)):
    lobby = _get_lobby(db, lobby_id)
    user, actor = _actor_for_lobby(db, request, lobby)
    if actor.role != "host" or not user or user.id != lobby.host_user_id:
        raise HTTPException(status_code=403, detail="Only the lobby host can stop a station")
    lobby.active_station_id = ""
    _touch_lobby(lobby)
    return _commit_lobby_state(db, lobby, actor, include_invite=True)


@router.post("/{lobby_id}/play", response_model=LobbyStateResponse)
def lobby_play(lobby_id: str, request: Request, db: Session = Depends(get_db)):
    lobby = _get_lobby(db, lobby_id)
    _, actor = _actor_for_lobby(db, request, lobby)
    if not _member_permissions(actor).can_control_playback:
        raise HTTPException(status_code=403, detail="You cannot control lobby playback")
    if _queue_rows(db, lobby.id):
        lobby.is_playing = True
        lobby.position_updated_at = _now()
        _record_now_playing_history(db, lobby)
    _touch_lobby(lobby)
    return _commit_lobby_state(db, lobby, actor, include_invite=(actor.role == "host"))


@router.post("/{lobby_id}/pause", response_model=LobbyStateResponse)
def lobby_pause(lobby_id: str, request: Request, db: Session = Depends(get_db)):
    lobby = _get_lobby(db, lobby_id)
    _, actor = _actor_for_lobby(db, request, lobby)
    if not _member_permissions(actor).can_control_playback:
        raise HTTPException(status_code=403, detail="You cannot control lobby playback")
    lobby.position_ms = _effective_position_ms(lobby)
    lobby.is_playing = False
    lobby.position_updated_at = _now()
    _touch_lobby(lobby)
    return _commit_lobby_state(db, lobby, actor, include_invite=(actor.role == "host"))


@router.post("/{lobby_id}/seek", response_model=LobbyStateResponse)
def lobby_seek(lobby_id: str, payload: LobbySeekRequest, request: Request, db: Session = Depends(get_db)):
    lobby = _get_lobby(db, lobby_id)
    _, actor = _actor_for_lobby(db, request, lobby)
    perms = _member_permissions(actor)
    if not (perms.can_seek or perms.can_control_playback):
        raise HTTPException(status_code=403, detail="You cannot seek lobby playback")
    lobby.position_ms = max(0, int(payload.position_ms or 0))
    lobby.position_updated_at = _now()
    _touch_lobby(lobby)
    return _commit_lobby_state(db, lobby, actor, include_invite=(actor.role == "host"))


@router.post("/{lobby_id}/ended", response_model=LobbyStateResponse)
def lobby_ended(lobby_id: str, payload: LobbyEndedRequest, request: Request, db: Session = Depends(get_db)):
    """Advance only if the reported queue item is still current and has actually ended."""
    lobby = _get_lobby(db, lobby_id)
    _, actor = _actor_for_lobby(db, request, lobby)
    rows = _queue_rows(db, lobby.id)
    current_index = int(lobby.current_index or 0)
    current = rows[current_index] if 0 <= current_index < len(rows) else None

    # This endpoint is intentionally available to any authenticated lobby member:
    # it reports completion of a specific item rather than granting skip control.
    # Stale/duplicate reports are no-ops.
    if current is None or current.id != (payload.item_id or "").strip() or not lobby.is_playing:
        return _commit_lobby_state(db, lobby, actor, include_invite=(actor.role == "host"))

    duration_ms = max(0, int(current.duration_ms or 0))
    if duration_ms > 0:
        effective_position_ms = _effective_position_ms(lobby)
        if effective_position_ms < max(0, duration_ms - 5000):
            return _commit_lobby_state(db, lobby, actor, include_invite=(actor.role == "host"))
    elif not (_member_permissions(actor).can_skip or _member_permissions(actor).can_control_playback):
        # Without a known duration the server cannot verify natural completion,
        # so fall back to the existing skip/control permission boundary.
        return _commit_lobby_state(db, lobby, actor, include_invite=(actor.role == "host"))

    if rows and current_index < len(rows) - 1:
        next_index = current_index + 1
        next_is_playing = True
    else:
        next_index = max(0, min(current_index, len(rows) - 1)) if rows else 0
        next_is_playing = False

    now = _now()
    expected_updated_at = lobby.updated_at
    result = db.execute(
        update(SharedLobby)
        .where(
            SharedLobby.id == lobby.id,
            SharedLobby.current_index == current_index,
            SharedLobby.updated_at == expected_updated_at,
        )
        .values(
            current_index=next_index,
            position_ms=0,
            is_playing=next_is_playing,
            position_updated_at=now,
            updated_at=now,
        )
    )

    # A simultaneous client may have already advanced this exact item. Re-read
    # instead of advancing again, making duplicate end reports race-safe.
    if result.rowcount != 1:
        db.rollback()
        lobby = _get_lobby(db, lobby_id)
        _, actor = _actor_for_lobby(db, request, lobby)
        return _commit_lobby_state(db, lobby, actor, include_invite=(actor.role == "host"))

    db.refresh(lobby)
    _record_now_playing_history(db, lobby)
    response = _commit_lobby_state(db, lobby, actor, include_invite=(actor.role == "host"))
    if (getattr(lobby, "active_station_id", "") or "").strip():
        schedule_lobby_station_fill(lobby.id)
    return response


@router.post("/{lobby_id}/next", response_model=LobbyStateResponse)
def lobby_next(lobby_id: str, request: Request, db: Session = Depends(get_db)):
    lobby = _get_lobby(db, lobby_id)
    _, actor = _actor_for_lobby(db, request, lobby)
    perms = _member_permissions(actor)
    if not (perms.can_skip or perms.can_control_playback):
        raise HTTPException(status_code=403, detail="You cannot skip lobby playback")
    rows = _queue_rows(db, lobby.id)
    current_index = int(lobby.current_index or 0)
    if rows and current_index < len(rows) - 1:
        lobby.current_index = current_index + 1
        lobby.position_ms = 0
        lobby.is_playing = True
    else:
        # End of the shared queue: stop instead of clamping to the final track,
        # otherwise the browser fires ended -> next -> final track again forever.
        lobby.current_index = max(0, min(current_index, len(rows) - 1)) if rows else 0
        lobby.position_ms = 0
        lobby.is_playing = False
    lobby.position_updated_at = _now()
    _record_now_playing_history(db, lobby)
    _touch_lobby(lobby)
    response = _commit_lobby_state(db, lobby, actor, include_invite=(actor.role == "host"))
    if (getattr(lobby, "active_station_id", "") or "").strip():
        schedule_lobby_station_fill(lobby.id)
    return response


@router.post("/{lobby_id}/previous", response_model=LobbyStateResponse)
def lobby_previous(lobby_id: str, request: Request, db: Session = Depends(get_db)):
    lobby = _get_lobby(db, lobby_id)
    _, actor = _actor_for_lobby(db, request, lobby)
    perms = _member_permissions(actor)
    if not (perms.can_skip or perms.can_control_playback):
        raise HTTPException(status_code=403, detail="You cannot skip lobby playback")
    lobby.current_index = max(0, int(lobby.current_index or 0) - 1)
    lobby.position_ms = 0
    lobby.position_updated_at = _now()
    _record_now_playing_history(db, lobby)
    _touch_lobby(lobby)
    return _commit_lobby_state(db, lobby, actor, include_invite=(actor.role == "host"))


@router.patch("/{lobby_id}/me", response_model=LobbyStateResponse)
def update_self_member(
    lobby_id: str,
    payload: LobbySelfUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    lobby = _get_lobby(db, lobby_id)
    _, actor = _actor_for_lobby(db, request, lobby)
    if payload.nickname is not None:
        nickname = _clean_text(payload.nickname, 80)
        if not nickname:
            raise HTTPException(status_code=400, detail="Nickname cannot be empty")
        actor.nickname = nickname
    actor.last_seen_at = _now()
    _touch_lobby(lobby)
    db.add(actor)
    return _commit_lobby_state(db, lobby, actor, include_invite=(actor.role == "host"))


@router.patch("/{lobby_id}/members/{member_id}", response_model=LobbyStateResponse)
def update_member(lobby_id: str, member_id: str, payload: LobbyMemberUpdateRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    lobby = _get_lobby(db, lobby_id)
    _require_host(lobby, user)
    member = db.execute(select(SharedLobbyMember).where(SharedLobbyMember.lobby_id == lobby.id, SharedLobbyMember.id == member_id)).scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")
    if member.role == "host":
        raise HTTPException(status_code=400, detail="Cannot edit host permissions")
    if payload.nickname is not None:
        member.nickname = _clean_text(payload.nickname, 80) or member.nickname
    if payload.is_active is not None:
        member.is_active = bool(payload.is_active)
    if payload.permissions is not None:
        member.permissions_json = _dump_permissions(payload.permissions)
    member.last_seen_at = _now()
    _touch_lobby(lobby)
    db.add(member)
    db.commit()
    schedule_lobby_state_broadcast(lobby.id)
    host_member = db.execute(select(SharedLobbyMember).where(SharedLobbyMember.lobby_id == lobby.id, SharedLobbyMember.user_id == user.id, SharedLobbyMember.role == "host")).scalar_one_or_none()
    return _to_lobby_state(db, lobby, host_member, include_invite=True)


@router.post("/{lobby_id}/leave", response_model=LobbyOkResponse)
def leave_lobby(lobby_id: str, request: Request, response: Response, db: Session = Depends(get_db)):
    lobby = _get_lobby(db, lobby_id)
    _, actor = _actor_for_lobby(db, request, lobby)
    if actor.role == "host":
        raise HTTPException(status_code=400, detail="Host cannot leave their own lobby; close the lobby instead")
    actor.is_active = False
    actor.last_seen_at = _now()
    db.add(actor)
    db.commit()
    schedule_lobby_state_broadcast(lobby.id)
    response.delete_cookie(LOBBY_TOKEN_COOKIE, path="/")
    return LobbyOkResponse(ok=True)
