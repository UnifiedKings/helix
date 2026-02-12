from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import select

from ..auth import get_current_user
from ..db import get_db
from ..models import User, DislikedTrack
from ..schemas import DislikeToggleRequest, DislikeResponse


router = APIRouter(prefix="/api/dislikes", tags=["dislikes"])


def _stable_key(payload: DislikeToggleRequest) -> str:
    sid = (payload.subsonic_song_id or "").strip()
    if sid:
        return f"subsonic:{sid}"
    vid = (payload.yt_video_id or "").strip()
    if vid:
        return f"yt:{vid}"
    return f"text:{(payload.title or '').strip()}|{(payload.artist or '').strip()}"


@router.get("/is-disliked", response_model=DislikeResponse)
def is_disliked(
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
        return DislikeResponse(disliked=False)
    row = db.execute(select(DislikedTrack).where(DislikedTrack.user_id == user.id, DislikedTrack.key == key)).scalar_one_or_none()
    return DislikeResponse(disliked=bool(row))


@router.post("/toggle", response_model=DislikeResponse)
def toggle_dislike(payload: DislikeToggleRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    key = _stable_key(payload)
    row = db.execute(select(DislikedTrack).where(DislikedTrack.user_id == user.id, DislikedTrack.key == key)).scalar_one_or_none()
    if row:
        db.delete(row)
        db.commit()
        return DislikeResponse(disliked=False)

    x = DislikedTrack(
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
    return DislikeResponse(disliked=True)
