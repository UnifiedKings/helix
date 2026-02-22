from __future__ import annotations

from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select

from ..auth import get_current_user
from ..db import get_db
from ..models import User, Station, PlaybackSession, QueueItem, ListenHistoryItem
from ..schemas import StationCreateRequest, StationUpdateRequest, StationResponse, StationPlayRequest
from ..settings_store import get_settings
from ..stations_engine import generate_and_append_station_track


router = APIRouter(prefix="/api/stations", tags=["stations"])

def _thumb_for_station(db: Session, station_id: str) -> str:
    """Phase 1: derive a station thumbnail from recent station listen history."""
    row = db.execute(
        select(ListenHistoryItem.art_url)
        .where(ListenHistoryItem.station_id == station_id)
        .where(ListenHistoryItem.art_url != "")
        .order_by(ListenHistoryItem.created_at.desc())
        .limit(1)
    ).first()
    return (row[0] if row and row[0] else "")



def _to_station(s: Station, thumbnail_url: str = "") -> StationResponse:
    return StationResponse(
        id=s.id,
        name=s.name,
        seed_type=s.seed_type,
        seed_title=s.seed_title,
        seed_artist=s.seed_artist,
        mb_artist_id=s.mb_artist_id or "",
        mb_recording_id=s.mb_recording_id or "",
        discovery=float(s.discovery or 0.35),
        seed_influence=float(getattr(s, "seed_influence", 0.75) or 0.75),
        artist_cooldown=int(getattr(s, "artist_cooldown", 5) or 5),
        artist_variety=int(getattr(s, "artist_variety", 1) or 1),
        allow_seed_alternates=bool(int(getattr(s, "allow_seed_alternates", 0) or 0)),
        era_start=int(getattr(s, "era_start", 0) or 0),
        era_end=int(getattr(s, "era_end", 0) or 0),
        popularity_bias=int(getattr(s, "popularity_bias", 50) or 50),
        tag_strictness=int(getattr(s, "tag_strictness", 70) or 70),
        popular_track_pool_size=int(getattr(s, "popular_track_pool_size", 10) or 10),
        artist_blacklist=str(getattr(s, "artist_blacklist", "") or ""),
        temperature=float(getattr(s, "temperature", 0.9) or 0.9),
        thumbnail_url=thumbnail_url or "",
        created_at=s.created_at.isoformat() + "Z",
        updated_at=s.updated_at.isoformat() + "Z",
    )


@router.get("", response_model=List[StationResponse])
def list_stations(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.execute(
        select(Station).where(Station.user_id == user.id).order_by(Station.updated_at.desc())
    ).scalars().all()
    return [_to_station(s, _thumb_for_station(db, s.id)) for s in rows]


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
        seed_influence=max(0.0, min(1.0, float(payload.seed_influence or 0.75))),
        artist_cooldown=max(0, min(50, int(payload.artist_cooldown or 0))),
        artist_variety=max(0, min(2, int(payload.artist_variety or 1))),
        allow_seed_alternates=1 if bool(payload.allow_seed_alternates) else 0,
        era_start=max(0, min(3000, int(payload.era_start or 0))),
        era_end=max(0, min(3000, int(payload.era_end or 0))),
        popularity_bias=max(0, min(100, int(payload.popularity_bias or 50))),
        tag_strictness=max(0, min(100, int(payload.tag_strictness or 70))),
        popular_track_pool_size=max(0, min(200, int(payload.popular_track_pool_size or 10))),
        artist_blacklist=(payload.artist_blacklist or ""),
        temperature=max(0.2, min(2.0, float(payload.temperature or 0.9))),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return _to_station(s, _thumb_for_station(db, s.id))


@router.patch("/{station_id}", response_model=StationResponse)
def update_station(station_id: str, payload: StationUpdateRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    st = db.get(Station, station_id)
    if not st or st.user_id != user.id:
        raise HTTPException(status_code=404, detail="Station not found")

    if payload.name is not None:
        st.name = (payload.name or "").strip()
    if payload.discovery is not None:
        st.discovery = max(0.0, min(1.0, float(payload.discovery)))
    if payload.seed_influence is not None:
        st.seed_influence = max(0.0, min(1.0, float(payload.seed_influence)))
    if payload.artist_cooldown is not None:
        st.artist_cooldown = max(0, min(50, int(payload.artist_cooldown)))
    if payload.artist_variety is not None:
        st.artist_variety = max(0, min(2, int(payload.artist_variety)))
    if payload.allow_seed_alternates is not None:
        st.allow_seed_alternates = 1 if bool(payload.allow_seed_alternates) else 0
    if payload.era_start is not None:
        st.era_start = max(0, min(3000, int(payload.era_start)))
    if payload.era_end is not None:
        st.era_end = max(0, min(3000, int(payload.era_end)))
    if payload.popularity_bias is not None:
        st.popularity_bias = max(0, min(100, int(payload.popularity_bias)))
    if payload.tag_strictness is not None:
        st.tag_strictness = max(0, min(100, int(payload.tag_strictness)))
    if payload.popular_track_pool_size is not None:
        st.popular_track_pool_size = max(0, min(200, int(payload.popular_track_pool_size)))
    if payload.artist_blacklist is not None:
        st.artist_blacklist = payload.artist_blacklist or ""

    st.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(st)
    return _to_station(st)


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
