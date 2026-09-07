from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import select, or_

from ..auth import get_current_user
from ..db import get_db
from ..models import User, DislikedTrack
from ..api_schemas.likes import DislikeToggleRequest
from ..api_schemas.playlists import DislikeResponse
from ..validators import is_valid_yt_video_id
from ..art_sources import yt_thumbnail_url, is_allowed_art_url


router = APIRouter(prefix="/api/dislikes", tags=["dislikes"])


def _stable_key(payload: DislikeToggleRequest) -> str:
    sid = (payload.subsonic_song_id or "").strip()
    if sid:
        return f"subsonic:{sid}"
    vid = (payload.yt_video_id or "").strip()
    if vid and is_valid_yt_video_id(vid):
        return f"yt:{vid}"
    return f"text:{(payload.title or '').strip()}|{(payload.artist or '').strip()}"


def _identity_filters(*, subsonic_song_id: str = "", yt_video_id: str = ""):
    sid = (subsonic_song_id or "").strip()
    vid = (yt_video_id or "").strip()
    filters = []
    if sid:
        filters.extend([
            DislikedTrack.key == f"subsonic:{sid}",
            DislikedTrack.subsonic_song_id == sid,
        ])
    if vid:
        filters.extend([
            DislikedTrack.key == f"yt:{vid}",
            DislikedTrack.yt_video_id == vid,
        ])
    return filters


def _find_disliked_by_identity(
    db: Session,
    user_id: str,
    *,
    subsonic_song_id: str = "",
    yt_video_id: str = "",
) -> Optional[DislikedTrack]:
    filters = _identity_filters(
        subsonic_song_id=subsonic_song_id,
        yt_video_id=yt_video_id,
    )
    if not filters:
        return None
    return (
        db.execute(
            select(DislikedTrack)
            .where(
                DislikedTrack.user_id == user_id,
                or_(*filters),
            )
            .limit(1)
        )
        .scalars()
        .first()
    )


@router.get("/is-disliked", response_model=DislikeResponse)
def is_disliked(
    yt_video_id: Optional[str] = None,
    subsonic_song_id: Optional[str] = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = _find_disliked_by_identity(
        db,
        str(user.id),
        subsonic_song_id=(subsonic_song_id or ""),
        yt_video_id=(yt_video_id or ""),
    )
    return DislikeResponse(disliked=bool(row))


@router.post("/toggle", response_model=DislikeResponse)
def toggle_dislike(
    payload: DislikeToggleRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    sid = (payload.subsonic_song_id or "").strip()
    vid = (payload.yt_video_id or "").strip()

    row = _find_disliked_by_identity(
        db,
        str(user.id),
        subsonic_song_id=sid,
        yt_video_id=vid,
    )

    key = _stable_key(payload)
    if row is None and not sid and not vid:
        row = db.execute(
            select(DislikedTrack).where(
                DislikedTrack.user_id == user.id,
                DislikedTrack.key == key,
            )
        ).scalar_one_or_none()

    if row:
        db.delete(row)
        db.commit()
        return DislikeResponse(disliked=False)

    yt_video_id = vid
    if yt_video_id and (not is_valid_yt_video_id(yt_video_id)):
        yt_video_id = ""

    art_url = (payload.art_url or "").strip() if payload.art_url else ""
    if art_url and (not is_allowed_art_url(art_url)):
        art_url = ""
    if yt_video_id and (not art_url):
        art_url = yt_thumbnail_url(yt_video_id)

    x = DislikedTrack(
        user_id=user.id,
        key=key,
        title=(payload.title or "").strip(),
        artist=(payload.artist or "").strip(),
        album=(payload.album or "").strip() if payload.album else "",
        duration_ms=int(payload.duration_ms or 0),
        art_url=art_url,
        source=(payload.source or "").strip() if payload.source else "",
        subsonic_song_id=sid,
        yt_video_id=yt_video_id,
        yt_browse_id=(payload.yt_browse_id or "").strip()
        if payload.yt_browse_id
        else "",
        mb_recording_id=(payload.mb_recording_id or "").strip()
        if payload.mb_recording_id
        else "",
        mb_artist_id=(payload.mb_artist_id or "").strip()
        if payload.mb_artist_id
        else "",
        mb_match_confidence=float(payload.mb_match_confidence or 0.0),
        mb_match_type=(payload.mb_match_type or "").strip()
        if payload.mb_match_type
        else "",
    )
    db.add(x)
    db.commit()
    return DislikeResponse(disliked=True)
