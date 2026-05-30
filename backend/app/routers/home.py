from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..db import get_db
from ..models import ListenHistoryItem, QueueItem, User
from ..lobby_models import SharedLobby, SharedLobbyMember
from ..settings_store import get_settings

router = APIRouter(prefix="/api/home", tags=["home"])


def _iso(dt: datetime | None = None) -> str:
    return (dt or datetime.utcnow()).isoformat() + "Z"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _activity_art_url(row: ListenHistoryItem) -> str:
    art_url = _clean(getattr(row, "art_url", ""))
    if art_url:
        return art_url
    subsonic_song_id = _clean(getattr(row, "subsonic_song_id", ""))
    if subsonic_song_id:
        return f"/api/art/subsonic/{quote(subsonic_song_id, safe='')}?size=128"
    yt_video_id = _clean(getattr(row, "yt_video_id", ""))
    if yt_video_id:
        return f"https://i.ytimg.com/vi/{yt_video_id}/hqdefault.jpg"
    return ""


@router.get("/summary")
def home_summary(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Small home-page summary for status, attention, and recent activity.

    This intentionally reuses existing Helix state instead of introducing a full
    event log. A richer activity feed can be added later without changing the
    home page contract.
    """
    settings = get_settings(db)
    attention: list[dict[str, str]] = []

    subsonic_base = _clean(settings.get("subsonic_base_url"))
    subsonic_user = _clean(settings.get("subsonic_username"))
    subsonic_pass = _clean(settings.get("subsonic_password"))
    if not (subsonic_base and subsonic_user and subsonic_pass):
        attention.append({
            "id": "subsonic-settings",
            "severity": "warning",
            "title": "Subsonic is not fully configured",
            "detail": "Library playback and station fulfillment may be limited.",
            "href": "/settings",
        })

    errored_items = db.execute(
        select(QueueItem)
        .where(QueueItem.session_user_id == user.id, QueueItem.error != "")
        .order_by(QueueItem.created_at.desc())
        .limit(5)
    ).scalars().all()
    for item in errored_items:
        attention.append({
            "id": f"queue-error-{item.id}",
            "severity": "error",
            "title": f"Queue item failed: {item.title or 'Unknown track'}",
            "detail": item.error or "Playback or fulfillment failed.",
            "href": "/search",
        })

    cutoff = datetime.utcnow() - timedelta(minutes=15)
    stuck_downloads = db.execute(
        select(QueueItem)
        .where(
            QueueItem.session_user_id == user.id,
            QueueItem.download_status == "DOWNLOADING",
            QueueItem.created_at < cutoff,
        )
        .order_by(QueueItem.created_at.asc())
        .limit(5)
    ).scalars().all()
    for item in stuck_downloads:
        attention.append({
            "id": f"stuck-download-{item.id}",
            "severity": "warning",
            "title": f"Download may be stuck: {item.title or 'Unknown track'}",
            "detail": "This queue item has been downloading for more than 15 minutes.",
            "href": "/search",
        })

    open_lobbies = db.execute(
        select(SharedLobby)
        .where(SharedLobby.host_user_id == user.id, SharedLobby.is_open == True)  # noqa: E712
        .order_by(SharedLobby.updated_at.desc())
        .limit(3)
    ).scalars().all()

    active_lobby_counts: dict[str, int] = {}
    if open_lobbies:
        rows = db.execute(
            select(SharedLobbyMember.lobby_id, func.count(SharedLobbyMember.id))
            .where(
                SharedLobbyMember.lobby_id.in_([lobby.id for lobby in open_lobbies]),
                SharedLobbyMember.is_active == True,  # noqa: E712
            )
            .group_by(SharedLobbyMember.lobby_id)
        ).all()
        active_lobby_counts = {str(lobby_id): int(count or 0) for lobby_id, count in rows}

    recent_activity: list[dict[str, str]] = []
    history_rows = db.execute(
        select(ListenHistoryItem)
        .where(ListenHistoryItem.user_id == user.id)
        .order_by(ListenHistoryItem.created_at.desc())
        .limit(5)
    ).scalars().all()
    for row in history_rows:
        verb = "Played" if (row.event or "") == "completed" else "Played"
        if (row.reason or "") in {"next", "prev", "jump", "removed_current", "replaced_queue"}:
            verb = "Moved past"
        detail_bits = [row.artist or "Unknown artist"]
        if row.station_id:
            detail_bits.append("from station")
        recent_activity.append({
            "id": f"history-{row.id}",
            "kind": "playback",
            "title": f"{verb} {row.title or 'Unknown track'}",
            "detail": " • ".join(detail_bits),
            "icon": "♪",
            "art_url": _activity_art_url(row),
            "source": _clean(row.source),
            "created_at": _iso(row.created_at),
        })

    for lobby in open_lobbies:
        count = active_lobby_counts.get(lobby.id, 0)
        recent_activity.append({
            "id": f"lobby-{lobby.id}",
            "kind": "lobby",
            "title": f"Lobby open: {lobby.name or 'Shared Lobby'}",
            "detail": f"{count} active member{'' if count == 1 else 's'}",
            "icon": "◎",
            "created_at": _iso(lobby.updated_at),
        })

    recent_activity.sort(key=lambda item: item.get("created_at") or "", reverse=True)

    return {
        "generated_at": _iso(),
        "health": {
            "status": "attention" if attention else "ok",
            "label": "Needs attention" if attention else "Everything looks good",
        },
        "attention": attention[:8],
        "recent_activity": recent_activity[:6],
    }
