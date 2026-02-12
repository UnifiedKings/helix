from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import select

from ..auth import get_current_user
from ..db import get_db
from ..models import User, LikedTrack
from ..schemas import LikeToggleRequest, LikeResponse, LikedTracksResponse, LikedTrackResponse


router = APIRouter(prefix="/api/likes", tags=["likes"])


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
        return LikeResponse(liked=False)

    x = LikedTrack(
        user_id=user.id,
        key=key,
        title=(payload.title or "").strip(),
        artist=(payload.artist or "").strip(),
        album=(payload.album or "").strip() if payload.album else "",
        duration_ms=int(payload.duration_ms or 0),
        art_url=(payload.art_url or "").strip() if payload.art_url else "",
        source=(payload.source or "").strip() if payload.source else "",
        subsonic_song_id=(payload.subsonic_song_id or "").strip() if payload.subsonic_song_id else "",
        yt_video_id=(payload.yt_video_id or "").strip() if payload.yt_video_id else "",
        yt_browse_id=(payload.yt_browse_id or "").strip() if payload.yt_browse_id else "",
        mb_recording_id=(payload.mb_recording_id or "").strip() if payload.mb_recording_id else "",
        mb_artist_id=(payload.mb_artist_id or "").strip() if payload.mb_artist_id else "",
        mb_match_confidence=float(payload.mb_match_confidence or 0.0),
        mb_match_type=(payload.mb_match_type or "").strip() if payload.mb_match_type else "",
    )
    db.add(x)
    db.commit()
    return LikeResponse(liked=True)
