from __future__ import annotations

from datetime import datetime
import asyncio
import json
import os
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import select

from ..auth import get_current_user
from ..db import get_db, SessionLocal
from ..models import User, Station, PlaybackSession, QueueItem, ListenHistoryItem
from ..api_schemas.player import PlayerStateResponse
from ..api_schemas.stations import StationCreateRequest, StationUpdateRequest, StationResponse, StationPlayRequest, StationProviderResponse
from ..settings_store import get_settings
from ..stations_engine import generate_and_append_station_track, StationSeedArtistNotFound, StationGenerationError
from ..station_providers import canonical_station_type, get_station_provider, list_station_providers, reload_station_providers
from ..station_covers import ensure_station_cover, custom_station_cover_path, delete_custom_station_cover, delete_generated_station_cover, has_custom_station_cover, save_custom_station_cover
from ..player.engine import state
from ..realtime import schedule_player_state_broadcast

router = APIRouter(prefix="/api/stations", tags=["stations"])


async def _read_limited_body(request: Request, *, max_bytes: int) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > max_bytes:
                raise HTTPException(status_code=413, detail="Uploaded cover image is too large")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Content-Length")
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status_code=413, detail="Uploaded cover image is too large")
        chunks.append(chunk)
    return b"".join(chunks)

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


def _cover_url(station_id: str, updated_at: datetime | None = None) -> str:
    if updated_at:
        try:
            return f"/api/stations/{station_id}/cover?v={int(updated_at.timestamp() * 1000)}"
        except Exception:
            pass
    return f"/api/stations/{station_id}/cover"



def _station_config_payload(s: Station) -> dict:
    try:
        raw = json.loads(str(getattr(s, "config_json", "{}") or "{}"))
        if not isinstance(raw, dict):
            raw = {}
    except Exception:
        raw = {}
    # Mirror legacy config columns so the provider API has one stable config shape.
    raw.setdefault("seed_type", getattr(s, "seed_type", "artist") or "artist")
    raw.setdefault("seed_title", getattr(s, "seed_title", "") or "")
    raw.setdefault("seed_artist", getattr(s, "seed_artist", "") or "")
    raw.setdefault("mb_artist_id", getattr(s, "mb_artist_id", "") or "")
    raw.setdefault("mb_recording_id", getattr(s, "mb_recording_id", "") or "")
    raw.setdefault("discovery", float(0.35 if getattr(s, "discovery", None) is None else getattr(s, "discovery")))
    raw.setdefault("seed_influence", float(0.75 if getattr(s, "seed_influence", None) is None else getattr(s, "seed_influence")))
    raw.setdefault("artist_cooldown", int(getattr(s, "artist_cooldown", 5) if getattr(s, "artist_cooldown", None) is not None else 5))
    raw.setdefault("artist_variety", int(1 if getattr(s, "artist_variety", None) is None else getattr(s, "artist_variety")))
    raw.setdefault("allow_seed_alternates", bool(int(getattr(s, "allow_seed_alternates", 0) or 0)))
    raw.setdefault("era_start", int(getattr(s, "era_start", 0) or 0))
    raw.setdefault("era_end", int(getattr(s, "era_end", 0) or 0))
    raw.setdefault("popularity_bias", int(50 if getattr(s, "popularity_bias", None) is None else getattr(s, "popularity_bias")))
    raw.setdefault("tag_strictness", int(70 if getattr(s, "tag_strictness", None) is None else getattr(s, "tag_strictness")))
    raw.setdefault("popular_track_pool_size", int(10 if getattr(s, "popular_track_pool_size", None) is None else getattr(s, "popular_track_pool_size")))
    raw.setdefault("artist_blacklist", str(getattr(s, "artist_blacklist", "") or ""))
    raw.setdefault("temperature", float(0.9 if getattr(s, "temperature", None) is None else getattr(s, "temperature")))
    raw.setdefault("source_mode", "prefer_library")
    return raw


def _to_station(s: Station, thumbnail_url: str = "") -> StationResponse:
    return StationResponse(
        id=s.id,
        name=s.name,
        station_type=canonical_station_type(str(getattr(s, "station_type", "") or "similar_artist")),
        config=_station_config_payload(s),
        seed_type=s.seed_type,
        seed_title=s.seed_title,
        seed_artist=s.seed_artist,
        mb_artist_id=s.mb_artist_id or "",
        mb_recording_id=s.mb_recording_id or "",
        discovery=float(0.35 if s.discovery is None else s.discovery),
        seed_influence=float(0.75 if getattr(s, "seed_influence", None) is None else getattr(s, "seed_influence")),
        artist_cooldown=int(getattr(s, "artist_cooldown", 5) if getattr(s, "artist_cooldown", None) is not None else 5),
        artist_variety=int(1 if getattr(s, "artist_variety", None) is None else getattr(s, "artist_variety")),
        allow_seed_alternates=bool(int(getattr(s, "allow_seed_alternates", 0) or 0)),
        era_start=int(getattr(s, "era_start", 0) or 0),
        era_end=int(getattr(s, "era_end", 0) or 0),
        popularity_bias=int(50 if getattr(s, "popularity_bias", None) is None else getattr(s, "popularity_bias")),
        tag_strictness=int(70 if getattr(s, "tag_strictness", None) is None else getattr(s, "tag_strictness")),
        popular_track_pool_size=int(10 if getattr(s, "popular_track_pool_size", None) is None else getattr(s, "popular_track_pool_size")),
        artist_blacklist=str(getattr(s, "artist_blacklist", "") or ""),
        temperature=float(0.9 if getattr(s, "temperature", None) is None else getattr(s, "temperature")),
        # Station cards should represent the seed artist, not the last played track.
        thumbnail_url=thumbnail_url or _cover_url(s.id, s.updated_at),
        cover_url=thumbnail_url or _cover_url(s.id, s.updated_at),
        has_custom_cover=has_custom_station_cover(s.id),
        created_at=s.created_at.isoformat() + "Z",
        updated_at=s.updated_at.isoformat() + "Z",
    )


@router.get("/types", response_model=list[StationProviderResponse])
def list_station_types(user: User = Depends(get_current_user)):
    return [info.to_dict() for info in list_station_providers()]


@router.post("/types/reload", response_model=list[StationProviderResponse])
def reload_station_types(user: User = Depends(get_current_user)):
    if str(getattr(user, "role", "") or "").lower() != "admin":
        raise HTTPException(status_code=403, detail="admin only")
    reload_station_providers()
    return [info.to_dict() for info in list_station_providers()]


@router.get("", response_model=List[StationResponse])
def list_stations(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.execute(
        select(Station).where(Station.user_id == user.id).order_by(Station.updated_at.desc())
    ).scalars().all()
    return [_to_station(s, _cover_url(s.id, s.updated_at)) for s in rows]


@router.post("", response_model=StationResponse)
def create_station(payload: StationCreateRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    seed_type = (payload.seed_type or "artist").strip().lower()
    if seed_type not in ("artist", "track"):
        raise HTTPException(status_code=400, detail="seed_type must be 'artist' or 'track'")

    station_type = canonical_station_type(payload.station_type)
    config = dict(payload.config or {})
    # Mirror legacy create fields into the provider config unless explicitly supplied.
    config.setdefault("seed_type", seed_type)
    config.setdefault("seed_title", (payload.seed_title or "").strip())
    config.setdefault("seed_artist", (payload.seed_artist or "").strip())
    config.setdefault("mb_artist_id", (payload.mb_artist_id or "").strip())
    config.setdefault("mb_recording_id", (payload.mb_recording_id or "").strip())
    config.setdefault("discovery", max(0.0, min(1.0, float(0.35 if payload.discovery is None else payload.discovery))))
    config.setdefault("seed_influence", max(0.0, min(1.0, float(0.75 if payload.seed_influence is None else payload.seed_influence))))
    config.setdefault("popular_track_pool_size", max(0, min(200, int(10 if payload.popular_track_pool_size is None else payload.popular_track_pool_size))))
    config.setdefault("artist_blacklist", payload.artist_blacklist or "")
    try:
        get_station_provider(station_type).validate_config(config)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    s = Station(
        user_id=user.id,
        name=name,
        station_type=station_type,
        config_json=json.dumps(config),
        seed_type=seed_type,
        seed_title=(payload.seed_title or "").strip(),
        seed_artist=(payload.seed_artist or "").strip(),
        mb_artist_id=(payload.mb_artist_id or "").strip() if payload.mb_artist_id else "",
        mb_recording_id=(payload.mb_recording_id or "").strip() if payload.mb_recording_id else "",
        discovery=max(0.0, min(1.0, float(0.35 if payload.discovery is None else payload.discovery))),
        seed_influence=max(0.0, min(1.0, float(0.75 if payload.seed_influence is None else payload.seed_influence))),
        artist_cooldown=max(0, min(50, int(payload.artist_cooldown or 0))),
        artist_variety=max(0, min(2, int(1 if payload.artist_variety is None else payload.artist_variety))),
        allow_seed_alternates=1 if bool(payload.allow_seed_alternates) else 0,
        era_start=max(0, min(3000, int(payload.era_start or 0))),
        era_end=max(0, min(3000, int(payload.era_end or 0))),
        popularity_bias=max(0, min(100, int(50 if payload.popularity_bias is None else payload.popularity_bias))),
        tag_strictness=max(0, min(100, int(70 if payload.tag_strictness is None else payload.tag_strictness))),
        popular_track_pool_size=max(0, min(200, int(10 if payload.popular_track_pool_size is None else payload.popular_track_pool_size))),
        artist_blacklist=(payload.artist_blacklist or ""),
        temperature=max(0.2, min(2.0, float(0.9 if payload.temperature is None else payload.temperature))),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return _to_station(s, _cover_url(s.id, s.updated_at))


@router.get("/{station_id}/cover")
async def station_cover(station_id: str, user: User = Depends(get_current_user)):
    # DB burst: validate and snapshot seed fields, then release DB before Subsonic cover generation.
    db = SessionLocal()
    try:
        st = db.get(Station, station_id)
        if not st or st.user_id != user.id:
            raise HTTPException(status_code=404, detail="Station not found")
        settings = dict(get_settings(db) or {})
        config = _station_config_payload(st)
        seed_artist = st.seed_artist or st.seed_title or st.name or "Station"
        station_name = st.name or "Station"
        station_type = st.station_type
        sid = st.id
        custom_path = custom_station_cover_path(sid)
        if os.path.exists(custom_path):
            return FileResponse(custom_path, media_type="image/jpeg", headers={"Cache-Control": "no-cache, max-age=0"})
    finally:
        db.close()

    # Providers may optionally announce a cover strategy. Existing providers and
    # plugins remain compatible: Helix derives a sensible strategy from their
    # normal seed/config fields when no explicit hint is supplied.
    try:
        provider = get_station_provider(station_type)
        cover_hint = provider.cover_hint(dict(config or {}))
    except Exception:
        cover_hint = None

    if not cover_hint:
        seed_title = str((config or {}).get("seed_title") or "").strip()
        configured_seed_artist = str((config or {}).get("seed_artist") or "").strip()
        seed_type = str((config or {}).get("seed_type") or "").strip().lower()
        raw_seed_artists = (config or {}).get("seed_artists")
        if isinstance(raw_seed_artists, str):
            seed_artists = [
                part.strip()
                for part in raw_seed_artists.replace("\r", "\n").replace(",", "\n").split("\n")
                if part.strip()
            ]
        elif isinstance(raw_seed_artists, list):
            seed_artists = [str(part or "").strip() for part in raw_seed_artists if str(part or "").strip()]
        else:
            seed_artists = []

        if seed_title and configured_seed_artist and seed_type == "track":
            cover_hint = {"mode": "track", "title": seed_title, "artist": configured_seed_artist, "fallback_seed": station_name}
        elif seed_artists:
            cover_hint = {"mode": "artists", "artists": seed_artists[:4], "fallback_seed": station_name}
        elif configured_seed_artist:
            cover_hint = {"mode": "artist", "artist": configured_seed_artist, "fallback_seed": station_name}
        else:
            cover_hint = {"mode": "generated", "label": station_name, "fallback_seed": station_name}

    try:
        from ..stations_engine import _subsonic_client_from_settings

        sub = await _subsonic_client_from_settings(settings)
        try:
            img_path = await ensure_station_cover(
                station_id=sid,
                seed_artist=seed_artist,
                subsonic=sub,
                cover_hint=cover_hint,
                size=640,
                tiles=4,
            )
        finally:
            try:
                await sub.close()
            except Exception:
                pass
    except Exception:
        class _Fake:
            async def search_albums_by_artist(self, artist: str, limit: int = 50):
                return []

            async def fetch_cover_art_bytes(self, cover_id: str, *, size: int = 512):
                return None

        img_path = await ensure_station_cover(
            station_id=sid,
            seed_artist=seed_artist,
            subsonic=_Fake(),
            cover_hint=cover_hint,
            size=640,
            tiles=4,
        )

    cache_headers = {"Cache-Control": "no-cache, max-age=0"} if has_custom_station_cover(sid) else {"Cache-Control": "public, max-age=3600"}
    return FileResponse(img_path, media_type="image/jpeg", headers=cache_headers)

async def _save_station_cover_upload(station_id: str, request: Request, db: Session, user: User) -> StationResponse:
    st = db.get(Station, station_id)
    if not st or st.user_id != user.id:
        raise HTTPException(status_code=404, detail="Station not found")

    # Do not trust or require the browser-provided Content-Type. Some browsers
    # report drag/dropped images as application/octet-stream or omit the MIME
    # type entirely. Pillow validates the actual bytes below.
    try:
        max_bytes = max(1, int(os.getenv("HELIX_STATION_COVER_MAX_BYTES", str(5 * 1024 * 1024))))
    except Exception:
        max_bytes = 5 * 1024 * 1024
    body = await _read_limited_body(request, max_bytes=max_bytes)
    try:
        saved_path = save_custom_station_cover(station_id, body)
        delete_generated_station_cover(station_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not os.path.exists(saved_path):
        raise HTTPException(status_code=500, detail="cover image was processed but not saved")

    st.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(st)
    return _to_station(st, _cover_url(st.id, st.updated_at))


@router.put("/{station_id}/cover", response_model=StationResponse)
async def upload_station_cover(station_id: str, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return await _save_station_cover_upload(station_id, request, db, user)


@router.post("/{station_id}/cover", response_model=StationResponse)
async def upload_station_cover_post(station_id: str, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return await _save_station_cover_upload(station_id, request, db, user)


@router.delete("/{station_id}/cover", response_model=StationResponse)
def delete_station_cover(station_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    st = db.get(Station, station_id)
    if not st or st.user_id != user.id:
        raise HTTPException(status_code=404, detail="Station not found")

    delete_custom_station_cover(station_id)
    st.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(st)
    return _to_station(st, _cover_url(st.id, st.updated_at))


@router.patch("/{station_id}", response_model=StationResponse)
def update_station(station_id: str, payload: StationUpdateRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    st = db.get(Station, station_id)
    if not st or st.user_id != user.id:
        raise HTTPException(status_code=404, detail="Station not found")

    if payload.name is not None:
        st.name = (payload.name or "").strip()
    if payload.station_type is not None:
        st.station_type = canonical_station_type(payload.station_type)
    if payload.config is not None:
        incoming_config = dict(payload.config or {})
        current = _station_config_payload(st)
        current.update(incoming_config)
        if "artist_cooldown" in incoming_config:
            st.artist_cooldown = max(0, min(50, int(incoming_config.get("artist_cooldown") or 0)))
        # Keep legacy seed columns synchronized with provider config. This matters
        # for track-seeded providers such as Song Radio and for station cards/covers.
        if "seed_type" in incoming_config:
            st.seed_type = str(incoming_config.get("seed_type") or "artist").strip() or "artist"
        if "seed_title" in incoming_config:
            st.seed_title = str(incoming_config.get("seed_title") or "").strip()
        if "seed_artist" in incoming_config:
            st.seed_artist = str(incoming_config.get("seed_artist") or "").strip()
        st.config_json = json.dumps(current)
    if payload.discovery is not None:
        st.discovery = max(0.0, min(1.0, float(payload.discovery)))
    if payload.seed_influence is not None:
        st.seed_influence = max(0.0, min(1.0, float(payload.seed_influence)))
    if payload.artist_cooldown is not None:
        st.artist_cooldown = max(0, min(50, int(payload.artist_cooldown)))
        try:
            current = json.loads(str(getattr(st, "config_json", "{}") or "{}"))
            if not isinstance(current, dict):
                current = {}
        except Exception:
            current = {}
        current["artist_cooldown"] = int(st.artist_cooldown)
        st.config_json = json.dumps(current)
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

    config = _station_config_payload(st)
    try:
        get_station_provider(getattr(st, "station_type", "similar_artist")).validate_config(config)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    st.config_json = json.dumps(config)

    st.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(st)
    return _to_station(st)


@router.post("/{station_id}/play", response_model=PlayerStateResponse)
async def play_station(station_id: str, payload: StationPlayRequest, user: User = Depends(get_current_user)):
    # DB burst: validate station + set session active station / autoplay / reset if requested
    db = SessionLocal()
    try:
        st = db.get(Station, station_id)
        if not st or st.user_id != user.id:
            raise HTTPException(status_code=404, detail="Station not found")

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
    finally:
        db.close()

    # Generate the current item immediately. Then prefetch ahead in the background so /play returns fast.
    try:
        ahead = max(0, int(os.getenv("HELIX_PREFETCH_AHEAD", "1")))
    except Exception:
        ahead = 1

    try:
        first = await generate_and_append_station_track(user.id, station_id, settings=settings, advance_to_new_item=True)
        if not first:
            raise StationGenerationError("Unable to generate station right now. The provider returned no playable tracks for the current station settings.", status_code=503)
    except StationSeedArtistNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except StationGenerationError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e

    # Return current player state
    db = SessionLocal()
    try:
        snapshot = state(db=db, user=user)
        schedule_player_state_broadcast(user.id)
        return snapshot
    finally:
        db.close()


@router.delete("/{station_id}", response_model=dict[str, bool])
def delete_station(station_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    st = db.get(Station, station_id)
    if not st or st.user_id != user.id:
        raise HTTPException(status_code=404, detail="Station not found")

    # If this station is active, clear it and disable autoplay.
    sess = db.get(PlaybackSession, user.id)
    if sess and sess.active_station_id == station_id:
        sess.active_station_id = None
        sess.autoplay_enabled = False

    delete_custom_station_cover(station_id)

    # Remove station (StationTag cascades)
    db.delete(st)
    db.commit()
    schedule_player_state_broadcast(user.id)
    return {"ok": True}
