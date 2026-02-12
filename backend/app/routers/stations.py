from __future__ import annotations

from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select

from ..auth import get_current_user
from ..db import get_db
from ..models import User, Station, PlaybackSession, QueueItem
from ..schemas import StationCreateRequest, StationResponse, StationPlayRequest
from ..settings_store import get_settings
from ..stations_engine import generate_and_append_station_track


router = APIRouter(prefix="/api/stations", tags=["stations"])


def _to_station(s: Station) -> StationResponse:
    return StationResponse(
        id=s.id,
        name=s.name,
        seed_type=s.seed_type,
        seed_title=s.seed_title,
        seed_artist=s.seed_artist,
        mb_artist_id=s.mb_artist_id or "",
        mb_recording_id=s.mb_recording_id or "",
        discovery=float(s.discovery or 0.35),
        temperature=float(getattr(s, "temperature", 0.9) or 0.9),
        created_at=s.created_at.isoformat() + "Z",
        updated_at=s.updated_at.isoformat() + "Z",
    )


@router.get("", response_model=List[StationResponse])
def list_stations(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.execute(
        select(Station).where(Station.user_id == user.id).order_by(Station.updated_at.desc())
    ).scalars().all()
    return [_to_station(s) for s in rows]


@router.post("", response_model=StationResponse)
def create_station(payload: StationCreateRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    seed_type = (payload.seed_type or "artist").strip().lower()
    if seed_type not in ("artist", "track"):
        raise HTTPException(status_code=400, detail="seed_type must be 'artist' or 'track'")

    s = Station(
        user_id=user.id,
        name=name,
        seed_type=seed_type,
        seed_title=(payload.seed_title or "").strip(),
        seed_artist=(payload.seed_artist or "").strip(),
        mb_artist_id=(payload.mb_artist_id or "").strip() if payload.mb_artist_id else "",
        mb_recording_id=(payload.mb_recording_id or "").strip() if payload.mb_recording_id else "",
        discovery=max(0.0, min(1.0, float(payload.discovery or 0.35))),
        temperature=max(0.2, min(2.0, float(payload.temperature or 0.9))),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return _to_station(s)


@router.post("/{station_id}/play")
async def play_station(station_id: str, payload: StationPlayRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    st = db.get(Station, station_id)
    if not st or st.user_id != user.id:
        raise HTTPException(status_code=404, detail="Station not found")

    # mark active station + enable autoplay
    sess = db.get(PlaybackSession, user.id)
    if not sess:
        sess = PlaybackSession(user_id=user.id)
        db.add(sess)
        db.commit()
        db.refresh(sess)

    sess.autoplay_enabled = True
    sess.active_station_id = st.id

    settings = get_settings(db)

    if payload and payload.reset:
        db.query(QueueItem).filter(QueueItem.session_user_id == user.id).delete()
        sess.current_index = 0
        sess.is_playing = False

    db.commit()

    # Ensure at least one item exists
    await generate_and_append_station_track(db, user.id, st.id, settings=settings, advance_to_new_item=True)

    # Reuse player state endpoint shape by importing lazily
    from .player import state as player_state
    return player_state(db=db, user=user)

@router.delete("/{station_id}")
def delete_station(station_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    st = db.get(Station, station_id)
    if not st or st.user_id != user.id:
        raise HTTPException(status_code=404, detail="Station not found")

    # If this station is active, clear it and disable autoplay.
    sess = db.get(PlaybackSession, user.id)
    if sess and sess.active_station_id == station_id:
        sess.active_station_id = None
        sess.autoplay_enabled = False

    # Remove station (StationTag cascades)
    db.delete(st)
    db.commit()
    return {"ok": True}
