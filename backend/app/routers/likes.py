from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import select

from ..auth import get_current_user
from ..db import get_db
from ..models import User, LikedTrack, Playlist, QueueItem, ListenHistoryItem
from ..schemas import LikeToggleRequest, LikeResponse, LikedTracksResponse, LikedTrackResponse
from ..playlist_covers import invalidate_playlist_cover
from ..validators import is_valid_yt_video_id
from ..art_sources import yt_thumbnail_url, is_allowed_art_url


router = APIRouter(prefix="/api/likes", tags=["likes"])

def _invalidate_liked_cover(db: Session, user_id: str) -> None:
    try:
        p = db.execute(select(Playlist).where(Playlist.user_id == user_id, Playlist.system_key == "liked")).scalar_one_or_none()
        if p:
            p.updated_at = datetime.utcnow()
            db.commit()
            invalidate_playlist_cover(p.id)
    except Exception:
        pass



def _stable_key(payload: LikeToggleRequest) -> str:
    sid = (payload.subsonic_song_id or "").strip()
    if sid:
        return f"subsonic:{sid}"
    vid = (payload.yt_video_id or "").strip()
    if vid:
        return f"yt:{vid}"
    # last-resort: title|artist (can collide, but better than nothing)
    return f"text:{(payload.title or '').strip()}|{(payload.artist or '').strip()}"


def _to_row(x: LikedTrack) -> LikedTrackResponse:
    return LikedTrackResponse(
        id=x.id,
        title=x.title or "",
        artist=x.artist or "",
        album=x.album or "",
        duration_ms=int(x.duration_ms or 0),
        art_url=x.art_url or "",
        source=x.source or "",
        subsonic_song_id=x.subsonic_song_id or "",
        yt_video_id=x.yt_video_id or "",
        yt_browse_id=x.yt_browse_id or "",
        mb_recording_id=x.mb_recording_id or "",
        mb_artist_id=x.mb_artist_id or "",
        created_at=x.created_at.isoformat() + "Z",
    )


@router.get("", response_model=LikedTracksResponse)
def list_liked(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.execute(
        select(LikedTrack).where(LikedTrack.user_id == user.id).order_by(LikedTrack.created_at.desc()).limit(5000)
    ).scalars().all()
    return LikedTracksResponse(items=[_to_row(r) for r in rows])


@router.get("/is-liked", response_model=LikeResponse)
def is_liked(
    yt_video_id: Optional[str] = None,
    subsonic_song_id: Optional[str] = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    key = ""
    if subsonic_song_id:
        key = f"subsonic:{subsonic_song_id.strip()}"
    elif yt_video_id:
        key = f"yt:{yt_video_id.strip()}"
    if not key:
        return LikeResponse(liked=False)
    row = db.execute(select(LikedTrack).where(LikedTrack.user_id == user.id, LikedTrack.key == key)).scalar_one_or_none()
    return LikeResponse(liked=bool(row))


@router.post("/toggle", response_model=LikeResponse)
def toggle_like(payload: LikeToggleRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    key = _stable_key(payload)
    row = db.execute(select(LikedTrack).where(LikedTrack.user_id == user.id, LikedTrack.key == key)).scalar_one_or_none()
    if row:
        db.delete(row)
        db.commit()
        _invalidate_liked_cover(db, user.id)
        return LikeResponse(liked=False)

    # --- Backfill subsonic_song_id / art_url when the client doesn't provide them ---
    # The playlist cover generator relies on Subsonic coverArt, which requires a subsonic_song_id.
    # Android clients may like a track with only yt_video_id populated. In that case, try to
    # resolve the Subsonic ID from our own queue/history records.
    resolved_subsonic_song_id = (payload.subsonic_song_id or "").strip()
    # Do not accept arbitrary art_url from clients.
    resolved_art_url = (payload.art_url or "").strip() if payload.art_url else ""
    if resolved_art_url and (not is_allowed_art_url(resolved_art_url)):
        resolved_art_url = ""
    resolved_yt_video_id = (payload.yt_video_id or "").strip() if payload.yt_video_id else ""
    if resolved_yt_video_id and (not is_valid_yt_video_id(resolved_yt_video_id)):
        resolved_yt_video_id = ""
    if resolved_yt_video_id and (not resolved_art_url):
        resolved_art_url = yt_thumbnail_url(resolved_yt_video_id)

    if (not resolved_subsonic_song_id) and resolved_yt_video_id:
        # 1) Current/Recent queue items (most reliable)
        qi = (
            db.execute(
                select(QueueItem)
                .where(
                    QueueItem.session_user_id == user.id,
                    QueueItem.yt_video_id == resolved_yt_video_id,
                )
                .order_by(QueueItem.created_at.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        if qi:
            resolved_subsonic_song_id = (qi.subsonic_song_id or "").strip()
            if not resolved_art_url:
                resolved_art_url = (qi.art_url or "").strip()

    if (not resolved_subsonic_song_id) and resolved_yt_video_id:
        # 2) Listen history (next best)
        hi = (
            db.execute(
                select(ListenHistoryItem)
                .where(
                    ListenHistoryItem.user_id == user.id,
                    ListenHistoryItem.yt_video_id == resolved_yt_video_id,
                )
                .order_by(ListenHistoryItem.created_at.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        if hi:
            resolved_subsonic_song_id = (hi.subsonic_song_id or "").strip() or resolved_subsonic_song_id
            if not resolved_art_url:
                resolved_art_url = (hi.art_url or "").strip()

    x = LikedTrack(
        user_id=user.id,
        key=key,
        title=(payload.title or "").strip(),
        artist=(payload.artist or "").strip(),
        album=(payload.album or "").strip() if payload.album else "",
        duration_ms=int(payload.duration_ms or 0),
        art_url=resolved_art_url,
        source=(payload.source or "").strip() if payload.source else "",
        subsonic_song_id=resolved_subsonic_song_id,
        yt_video_id=resolved_yt_video_id,
        yt_browse_id=(payload.yt_browse_id or "").strip() if payload.yt_browse_id else "",
        mb_recording_id=(payload.mb_recording_id or "").strip() if payload.mb_recording_id else "",
        mb_artist_id=(payload.mb_artist_id or "").strip() if payload.mb_artist_id else "",
        mb_match_confidence=float(payload.mb_match_confidence or 0.0),
        mb_match_type=(payload.mb_match_type or "").strip() if payload.mb_match_type else "",
    )
    db.add(x)
    db.commit()
    _invalidate_liked_cover(db, user.id)
    return LikeResponse(liked=True)
