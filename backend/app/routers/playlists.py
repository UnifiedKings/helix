from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from starlette.responses import FileResponse
from sqlalchemy import select, func, delete
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..db import get_db
from ..models import User, Playlist, PlaylistTrack, LikedTrack
from ..schemas import (
    PlaylistCreateRequest,
    PlaylistResponse,
    PlaylistDetailResponse,
    PlaylistTrackAddRequest,
    PlaylistTrackResponse,
)
from ..settings_store import get_settings
from ..integrations.subsonic import SubsonicClient
from ..playlist_covers import ensure_playlist_cover, invalidate_playlist_cover
from ..validators import is_valid_yt_video_id
from ..art_sources import yt_thumbnail_url, is_allowed_art_url

router = APIRouter(prefix="/api/playlists", tags=["playlists"])


def _cover_url(pid: str, ts: float | None = None) -> str:
    q = ''
    if ts is not None and ts > 0:
        q = f'?ts={int(ts)}'
    return f"/api/playlists/{pid}/cover{q}"


def _stable_key(payload: PlaylistTrackAddRequest) -> str:
    sid = (payload.subsonic_song_id or "").strip() if payload.subsonic_song_id else ""
    if sid:
        return f"subsonic:{sid}"
    vid = (payload.yt_video_id or "").strip() if payload.yt_video_id else ""
    if vid and is_valid_yt_video_id(vid):
        return f"yt:{vid}"
    return f"text:{(payload.title or '').strip()}|{(payload.artist or '').strip()}"


def _ensure_liked_playlist(db: Session, user_id: str) -> Playlist:
    row = db.execute(select(Playlist).where(Playlist.user_id == user_id, Playlist.system_key == "liked")).scalar_one_or_none()
    if row:
        return row
    p = Playlist(user_id=user_id, name="Liked Songs", system_key="liked")
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _to_playlist_response(p: Playlist, track_count: int) -> PlaylistResponse:
    return PlaylistResponse(
        id=p.id,
        name=p.name or "",
        system_key=p.system_key or "",
        track_count=int(track_count or 0),
        created_at=p.created_at.isoformat() + "Z",
        updated_at=p.updated_at.isoformat() + "Z",
        thumbnail_url=_cover_url(p.id, ts=p.updated_at.timestamp() if p.updated_at else None),
    )


def _to_track_response(t: Any) -> PlaylistTrackResponse:
    # Works for PlaylistTrack and LikedTrack
    return PlaylistTrackResponse(
        id=getattr(t, "id"),
        key=getattr(t, "key", "") or "",
        title=getattr(t, "title", "") or "",
        artist=getattr(t, "artist", "") or "",
        album=getattr(t, "album", "") or "",
        duration_ms=int(getattr(t, "duration_ms", 0) or 0),
        art_url=getattr(t, "art_url", "") or "",
        source=getattr(t, "source", "") or "",
        subsonic_song_id=getattr(t, "subsonic_song_id", "") or "",
        yt_video_id=getattr(t, "yt_video_id", "") or "",
        yt_browse_id=getattr(t, "yt_browse_id", "") or "",
        mb_recording_id=getattr(t, "mb_recording_id", "") or "",
        mb_artist_id=getattr(t, "mb_artist_id", "") or "",
        created_at=getattr(t, "created_at").isoformat() + "Z",
    )


async def _subsonic_client_from_settings(settings: Dict[str, Any]) -> SubsonicClient:
    base_url = (settings.get("subsonic_base_url") or "").rstrip("/")
    username = settings.get("subsonic_username") or ""
    password = settings.get("subsonic_password") or ""
    if not base_url or not username or not password:
        raise HTTPException(status_code=400, detail="Subsonic settings are not configured")
    timeout_s = int(settings.get("subsonic_timeout_s", 20) or 20)
    return SubsonicClient(base_url=base_url, username=username, password=password, timeout_s=timeout_s)


@router.get("", response_model=list[PlaylistResponse])
def list_playlists(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    liked = _ensure_liked_playlist(db, user.id)

    playlists = db.execute(select(Playlist).where(Playlist.user_id == user.id).order_by(Playlist.created_at.desc())).scalars().all()

    # Precompute counts.
    counts: Dict[str, int] = {}
    # user-created playlists
    rows = db.execute(select(PlaylistTrack.playlist_id, func.count(PlaylistTrack.id)).where(PlaylistTrack.user_id == user.id).group_by(PlaylistTrack.playlist_id)).all()
    for pid, c in rows:
        counts[str(pid)] = int(c or 0)

    liked_count = db.execute(select(func.count(LikedTrack.id)).where(LikedTrack.user_id == user.id)).scalar_one()
    counts[liked.id] = int(liked_count or 0)

    out: List[PlaylistResponse] = []
    for p in playlists:
        out.append(_to_playlist_response(p, counts.get(p.id, 0)))

    # Ensure liked is first.
    out.sort(key=lambda r: (0 if r.system_key == "liked" else 1, r.created_at), reverse=False)
    return out


@router.post("", response_model=PlaylistResponse)
def create_playlist(payload: PlaylistCreateRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    p = Playlist(user_id=user.id, name=name, system_key="")
    db.add(p)
    db.commit()
    db.refresh(p)
    return _to_playlist_response(p, 0)


@router.get("/{playlist_id}", response_model=PlaylistDetailResponse)
def playlist_detail(playlist_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    # Special: liked playlist
    if playlist_id == "liked":
        liked = _ensure_liked_playlist(db, user.id)
        rows = db.execute(select(LikedTrack).where(LikedTrack.user_id == user.id).order_by(LikedTrack.created_at.desc()).limit(5000)).scalars().all()
        pr = _to_playlist_response(liked, len(rows))
        return PlaylistDetailResponse(playlist=pr, tracks=[_to_track_response(r) for r in rows])

    p = db.execute(select(Playlist).where(Playlist.id == playlist_id, Playlist.user_id == user.id)).scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Playlist not found")

    rows = db.execute(select(PlaylistTrack).where(PlaylistTrack.playlist_id == p.id, PlaylistTrack.user_id == user.id).order_by(PlaylistTrack.position.asc(), PlaylistTrack.created_at.asc())).scalars().all()
    pr = _to_playlist_response(p, len(rows))
    return PlaylistDetailResponse(playlist=pr, tracks=[_to_track_response(r) for r in rows])


@router.post("/{playlist_id}/tracks", response_model=PlaylistDetailResponse)
def playlist_add_track(playlist_id: str, payload: PlaylistTrackAddRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    # Liked playlist: delegate to like toggle semantics (force liked)
    if playlist_id == "liked":
        # For now, just store in liked_tracks table.
        from .likes import toggle_like  # local import to avoid circular
        toggle_like(payload, db=db, user=user)
        return playlist_detail("liked", db=db, user=user)

    p = db.execute(select(Playlist).where(Playlist.id == playlist_id, Playlist.user_id == user.id)).scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Playlist not found")
    if p.system_key:
        raise HTTPException(status_code=400, detail="Cannot add tracks to system playlist")

    key = _stable_key(payload)
    existing = db.execute(select(PlaylistTrack).where(PlaylistTrack.playlist_id == p.id, PlaylistTrack.key == key)).scalar_one_or_none()
    if existing:
        return playlist_detail(p.id, db=db, user=user)

    max_pos = db.execute(select(func.max(PlaylistTrack.position)).where(PlaylistTrack.playlist_id == p.id)).scalar_one()
    next_pos = int(max_pos or -1) + 1

    yt_video_id = (payload.yt_video_id or "").strip() if payload.yt_video_id else ""
    if yt_video_id and (not is_valid_yt_video_id(yt_video_id)):
        yt_video_id = ""

    art_url = (payload.art_url or "").strip() if payload.art_url else ""
    # Do not accept arbitrary art_url from clients.
    if art_url and (not is_allowed_art_url(art_url)):
        art_url = ""
    if yt_video_id and (not art_url):
        art_url = yt_thumbnail_url(yt_video_id)

    t = PlaylistTrack(
        playlist_id=p.id,
        user_id=user.id,
        position=next_pos,
        key=key,
        title=(payload.title or "").strip(),
        artist=(payload.artist or "").strip(),
        album=(payload.album or "").strip() if payload.album else "",
        duration_ms=int(payload.duration_ms or 0),
        art_url=art_url,
        source=(payload.source or "").strip() if payload.source else "",
        subsonic_song_id=(payload.subsonic_song_id or "").strip() if payload.subsonic_song_id else "",
        yt_video_id=yt_video_id,
        yt_browse_id=(payload.yt_browse_id or "").strip() if payload.yt_browse_id else "",
        mb_recording_id=(payload.mb_recording_id or "").strip() if payload.mb_recording_id else "",
        mb_artist_id=(payload.mb_artist_id or "").strip() if payload.mb_artist_id else "",
    )
    db.add(t)
    p.updated_at = datetime.utcnow()
    db.commit()
    invalidate_playlist_cover(p.id)
    return playlist_detail(p.id, db=db, user=user)


@router.delete("/{playlist_id}/tracks/{track_id}", response_model=PlaylistDetailResponse)
def playlist_remove_track(playlist_id: str, track_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if playlist_id == "liked":
        # Removing from liked playlist means un-like.
        row = db.execute(select(LikedTrack).where(LikedTrack.user_id == user.id, LikedTrack.id == track_id)).scalar_one_or_none()
        if row:
            db.delete(row)
            db.commit()
        # ensure cover refreshes immediately
        liked = _ensure_liked_playlist(db, user.id)
        invalidate_playlist_cover(liked.id)
        return playlist_detail("liked", db=db, user=user)

    p = db.execute(select(Playlist).where(Playlist.id == playlist_id, Playlist.user_id == user.id)).scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Playlist not found")

    row = db.execute(select(PlaylistTrack).where(PlaylistTrack.id == track_id, PlaylistTrack.playlist_id == p.id, PlaylistTrack.user_id == user.id)).scalar_one_or_none()
    if row:
        db.delete(row)
        p.updated_at = datetime.utcnow()
        db.commit()

    # Re-pack positions (keep tidy)
    rows = db.execute(select(PlaylistTrack).where(PlaylistTrack.playlist_id == p.id, PlaylistTrack.user_id == user.id).order_by(PlaylistTrack.position.asc(), PlaylistTrack.created_at.asc())).scalars().all()
    for i, r in enumerate(rows):
        r.position = i
    db.commit()
    invalidate_playlist_cover(p.id)

    return playlist_detail(p.id, db=db, user=user)


@router.get("/{playlist_id}/cover")
async def playlist_cover(playlist_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    # Determine playlist + tracks.
    # NOTE: the liked playlist has a real UUID id in the DB, and clients use that id in cover URLs.
    # Treat both the literal "liked" sentinel and the UUID playlist row (system_key == "liked")
    # as the liked playlist.

    p: Playlist | None = None

    if playlist_id == "liked":
        p = _ensure_liked_playlist(db, user.id)

    if p is None:
        # Load by id first; if it's the system liked playlist, render liked-tracks cover.
        p = db.execute(select(Playlist).where(Playlist.id == playlist_id, Playlist.user_id == user.id)).scalar_one_or_none()
        if p and (p.system_key or "") == "liked":
            playlist_id = "liked"

    if playlist_id == "liked":
        if p is None:
            p = _ensure_liked_playlist(db, user.id)
        tracks = db.execute(select(LikedTrack).where(LikedTrack.user_id == user.id).order_by(LikedTrack.created_at.desc()).limit(500)).scalars().all()
        tracks_dicts = [
            {
                "subsonic_song_id": t.subsonic_song_id,
                "art_url": t.art_url,
            }
            for t in tracks
        ]
        seed = "liked:" + (user.username or user.id)
    else:
        if p is None:
            p = db.execute(select(Playlist).where(Playlist.id == playlist_id, Playlist.user_id == user.id)).scalar_one_or_none()
        if not p:
            raise HTTPException(status_code=404, detail="Playlist not found")
        tracks = db.execute(select(PlaylistTrack).where(PlaylistTrack.playlist_id == p.id, PlaylistTrack.user_id == user.id).order_by(PlaylistTrack.position.asc()).limit(500)).scalars().all()
        tracks_dicts = [
            {
                "subsonic_song_id": t.subsonic_song_id,
                "art_url": t.art_url,
            }
            for t in tracks
        ]
        seed = (p.name or p.id)

    settings = get_settings(db)
    client = await _subsonic_client_from_settings(settings)
    try:
        img_path = await ensure_playlist_cover(
            playlist_id=p.id,
            seed=seed,
            subsonic=client,
            tracks=tracks_dicts,
            size=768,
            tiles=9,
        )
    finally:
        await client.close()

    return FileResponse(img_path, media_type="image/jpeg")

@router.delete("/{playlist_id}")
def delete_playlist(playlist_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    # Never allow deleting the system liked playlist via this endpoint.
    if playlist_id == "liked":
        raise HTTPException(status_code=400, detail="Liked playlist cannot be deleted")
    pl = db.execute(select(Playlist).where(Playlist.user_id == user.id, Playlist.id == playlist_id)).scalar_one_or_none()
    if not pl:
        raise HTTPException(status_code=404, detail="Playlist not found")
    if pl.system_key == "liked":
        raise HTTPException(status_code=400, detail="Liked playlist cannot be deleted")
    db.delete(pl)
    db.commit()
    return {"ok": True}
