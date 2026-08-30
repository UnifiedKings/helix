from __future__ import annotations

import os
import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response
from starlette.responses import FileResponse
from sqlalchemy import select, func, delete, update
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..db import get_db
from ..models import User, Playlist, PlaylistTrack, LikedTrack
from ..api_schemas.playlists import (
    PlaylistCreateRequest,
    PlaylistResponse,
    PlaylistDetailResponse,
    PlaylistTrackAddRequest,
    PlaylistReorderRequest,
    PlaylistTrackResponse,
    PlaylistImportPreviewRequest,
    PlaylistImportApplyRequest,
)
from ..settings_store import get_settings
from ..integrations.subsonic import SubsonicClient
from ..playlist_covers import ensure_playlist_cover, invalidate_playlist_cover
from ..validators import is_valid_yt_video_id
from ..art_sources import yt_thumbnail_url, is_allowed_art_url
from ..playlist_imports import (
    ImportedTrack,
    match_track,
    parse_exportify_csv,
    parse_helix_json,
    parse_pandora_playlist_url,
    parse_ytmusic_playlist_url,
    parse_ytmusic_saved_html,
    track_identity_keys,
)

router = APIRouter(prefix="/api/playlists", tags=["playlists"])




def _subsonic_art_url(subsonic_song_id: str, size: int = 512) -> str:
    sid = (subsonic_song_id or "").strip()
    if not sid:
        return ""
    return f"/api/art/subsonic/{quote(sid, safe='')}?size={int(size)}"


def _is_allowed_internal_art_url(url: str) -> bool:
    u = (url or "").strip()
    return u.startswith("/api/art/subsonic/") or u.startswith("/api/art/ytmusic/")


def _safe_track_art_url(art_url: str, subsonic_song_id: str = "", yt_video_id: str = "") -> str:
    art = (art_url or "").strip()
    if art:
        if _is_allowed_internal_art_url(art) or is_allowed_art_url(art):
            return art
        art = ""

    sid = (subsonic_song_id or "").strip()
    if sid:
        return _subsonic_art_url(sid)

    vid = (yt_video_id or "").strip()
    if vid and is_valid_yt_video_id(vid):
        return yt_thumbnail_url(vid)

    return ""

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




def _is_liked_playlist_row(p: Playlist | None) -> bool:
    return bool(p and ((p.system_key or "") == "liked"))


def _resolve_user_playlist(db: Session, user_id: str, playlist_id: str) -> Playlist | None:
    """Resolve a playlist id, accepting both the literal liked sentinel and its DB UUID."""
    pid = (playlist_id or "").strip()
    if pid == "liked":
        return _ensure_liked_playlist(db, user_id)
    if not pid:
        return None
    return db.execute(select(Playlist).where(Playlist.id == pid, Playlist.user_id == user_id)).scalar_one_or_none()


def _liked_playlist_detail(db: Session, user: User, liked: Playlist | None = None) -> PlaylistDetailResponse:
    liked = liked or _ensure_liked_playlist(db, user.id)
    rows = db.execute(
        select(LikedTrack)
        .where(LikedTrack.user_id == user.id)
        .order_by(LikedTrack.created_at.desc())
        .limit(5000)
    ).scalars().all()
    pr = _to_playlist_response(liked, len(rows))
    return PlaylistDetailResponse(playlist=pr, tracks=[_to_track_response(r) for r in rows])

def _normalize_user_playlist_system_keys(db: Session, user_id: str) -> None:
    """Convert legacy user-created playlist system_key values to NULL.

    Earlier React playlist work created normal playlists with system_key="" even
    though the model treats NULL as the user-created playlist value. Because the
    table has a unique constraint on (user_id, system_key), a second normal
    playlist for the same user can fail with an IntegrityError and surface as a
    500. Keeping user-created playlists as NULL allows multiple normal playlists
    while reserving non-empty system_key values for singleton system playlists.
    """
    db.execute(
        update(Playlist)
        .where(Playlist.user_id == user_id, Playlist.system_key == "")
        .values(system_key=None)
    )


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
        position=int(getattr(t, "position", 0) or 0),
        key=getattr(t, "key", "") or "",
        title=getattr(t, "title", "") or "",
        artist=getattr(t, "artist", "") or "",
        album=getattr(t, "album", "") or "",
        duration_ms=int(getattr(t, "duration_ms", 0) or 0),
        art_url=_safe_track_art_url(
            getattr(t, "art_url", "") or "",
            getattr(t, "subsonic_song_id", "") or "",
            getattr(t, "yt_video_id", "") or "",
        ),
        source=getattr(t, "source", "") or "",
        subsonic_song_id=getattr(t, "subsonic_song_id", "") or "",
        yt_video_id=getattr(t, "yt_video_id", "") or "",
        yt_browse_id=getattr(t, "yt_browse_id", "") or "",
        mb_recording_id=getattr(t, "mb_recording_id", "") or "",
        mb_artist_id=getattr(t, "mb_artist_id", "") or "",
        created_at=getattr(t, "created_at").isoformat() + "Z",
    )


async def _subsonic_client_from_settings(settings: Dict[str, Any]) -> Optional[SubsonicClient]:
    base_url = (settings.get("subsonic_base_url") or "").rstrip("/")
    username = settings.get("subsonic_username") or ""
    password = settings.get("subsonic_password") or ""
    if not base_url or not username or not password:
        return None
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

    _normalize_user_playlist_system_keys(db, user.id)

    p = Playlist(user_id=user.id, name=name, system_key=None)
    db.add(p)
    db.commit()
    db.refresh(p)
    return _to_playlist_response(p, 0)


@router.get("/{playlist_id}", response_model=PlaylistDetailResponse)
def playlist_detail(playlist_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    p = _resolve_user_playlist(db, user.id, playlist_id)
    if not p:
        raise HTTPException(status_code=404, detail="Playlist not found")

    # The frontend usually navigates with the real UUID returned by /api/playlists.
    # For the system Liked Songs playlist, its tracks live in liked_tracks rather
    # than playlist_tracks, so treat both /api/playlists/liked and
    # /api/playlists/<liked UUID> identically.
    if _is_liked_playlist_row(p):
        return _liked_playlist_detail(db, user, p)

    rows = db.execute(select(PlaylistTrack).where(PlaylistTrack.playlist_id == p.id, PlaylistTrack.user_id == user.id).order_by(PlaylistTrack.position.asc(), PlaylistTrack.created_at.asc())).scalars().all()
    pr = _to_playlist_response(p, len(rows))
    return PlaylistDetailResponse(playlist=pr, tracks=[_to_track_response(r) for r in rows])


@router.post("/{playlist_id}/tracks", response_model=PlaylistDetailResponse)
def playlist_add_track(playlist_id: str, payload: PlaylistTrackAddRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    p = _resolve_user_playlist(db, user.id, playlist_id)
    if not p:
        raise HTTPException(status_code=404, detail="Playlist not found")

    key = _stable_key(payload)

    # Liked playlist rows are stored in liked_tracks, not playlist_tracks. Accept
    # either the "liked" sentinel or the real liked playlist UUID here too.
    if _is_liked_playlist_row(p):
        existing = db.execute(select(LikedTrack).where(LikedTrack.user_id == user.id, LikedTrack.key == key)).scalar_one_or_none()
        if not existing:
            from .likes import toggle_like  # local import to avoid circular
            toggle_like(payload, db=db, user=user)
        return _liked_playlist_detail(db, user, p)

    if p.system_key:
        raise HTTPException(status_code=400, detail="Cannot add tracks to system playlist")

    existing = db.execute(select(PlaylistTrack).where(PlaylistTrack.playlist_id == p.id, PlaylistTrack.key == key)).scalar_one_or_none()
    if existing:
        return playlist_detail(p.id, db=db, user=user)

    max_pos = db.execute(select(func.max(PlaylistTrack.position)).where(PlaylistTrack.playlist_id == p.id)).scalar_one()
    next_pos = int(max_pos or -1) + 1

    yt_video_id = (payload.yt_video_id or "").strip() if payload.yt_video_id else ""
    if yt_video_id and (not is_valid_yt_video_id(yt_video_id)):
        yt_video_id = ""

    art_url = _safe_track_art_url(
        (payload.art_url or "").strip() if payload.art_url else "",
        (payload.subsonic_song_id or "").strip() if payload.subsonic_song_id else "",
        yt_video_id,
    )

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
    p = _resolve_user_playlist(db, user.id, playlist_id)
    if not p:
        raise HTTPException(status_code=404, detail="Playlist not found")

    if _is_liked_playlist_row(p):
        # Removing from liked playlist means un-like.
        row = db.execute(select(LikedTrack).where(LikedTrack.user_id == user.id, LikedTrack.id == track_id)).scalar_one_or_none()
        if row:
            db.delete(row)
            p.updated_at = datetime.utcnow()
            db.commit()
        invalidate_playlist_cover(p.id)
        return _liked_playlist_detail(db, user, p)

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


@router.patch("/{playlist_id}/tracks/reorder", response_model=PlaylistDetailResponse)
def playlist_reorder_tracks(playlist_id: str, payload: PlaylistReorderRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    p = _resolve_user_playlist(db, user.id, playlist_id)
    if not p:
        raise HTTPException(status_code=404, detail="Playlist not found")
    if _is_liked_playlist_row(p):
        raise HTTPException(status_code=400, detail="Liked playlist order cannot be edited")
    if p.system_key:
        raise HTTPException(status_code=400, detail="System playlist order cannot be edited")

    requested_ids: List[str] = []
    seen: set[str] = set()
    for track_id in payload.track_ids or []:
        tid = (track_id or "").strip()
        if tid and tid not in seen:
            requested_ids.append(tid)
            seen.add(tid)

    rows = db.execute(
        select(PlaylistTrack)
        .where(PlaylistTrack.playlist_id == p.id, PlaylistTrack.user_id == user.id)
        .order_by(PlaylistTrack.position.asc(), PlaylistTrack.created_at.asc())
    ).scalars().all()

    row_ids = [r.id for r in rows]
    if set(requested_ids) != set(row_ids) or len(requested_ids) != len(row_ids):
        raise HTTPException(status_code=400, detail="Reorder payload must include every playlist track exactly once")

    by_id = {r.id: r for r in rows}
    for position, track_id in enumerate(requested_ids):
        by_id[track_id].position = position

    p.updated_at = datetime.utcnow()
    db.commit()
    invalidate_playlist_cover(p.id)

    return playlist_detail(p.id, db=db, user=user)




def _playlist_existing_import_keys(db: Session, p: Playlist, user_id: str) -> set[str]:
    keys: set[str] = set()
    if _is_liked_playlist_row(p):
        rows = db.execute(select(LikedTrack).where(LikedTrack.user_id == user_id)).scalars().all()
    else:
        rows = db.execute(select(PlaylistTrack).where(PlaylistTrack.playlist_id == p.id, PlaylistTrack.user_id == user_id)).scalars().all()
    for row in rows:
        key = (getattr(row, "key", "") or "").strip()
        if key:
            keys.add(key)
        vid = (getattr(row, "yt_video_id", "") or "").strip()
        if vid:
            keys.add(f"yt:{vid}")
        title = (getattr(row, "title", "") or "").strip().casefold()
        artist = (getattr(row, "artist", "") or "").strip().casefold()
        if title and artist:
            keys.add(f"text:{title}|{artist}")
    return keys


def _import_source_row(track: ImportedTrack) -> dict[str, Any]:
    return {
        "source": track.source,
        "source_track_id": track.source_track_id,
        "title": track.title,
        "artist": track.artist,
        "album": track.album,
        "duration_ms": int(track.duration_ms or 0),
        "artwork_url": track.artwork_url,
        "isrc": track.isrc,
        "yt_video_id": track.yt_video_id,
    }


async def _preview_match_tracks(tracks: list[ImportedTrack], duplicate_flags: list[bool]) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(5)

    async def one(index: int, track: ImportedTrack) -> dict[str, Any]:
        if duplicate_flags[index]:
            return {
                "index": index,
                "source_track": _import_source_row(track),
                "status": "duplicate",
                "confidence": 1.0,
                "candidate": None,
                "alternatives": [],
            }
        async with semaphore:
            result = await asyncio.to_thread(match_track, track)
        return {"index": index, "source_track": _import_source_row(track), **result}

    return await asyncio.gather(*(one(index, track) for index, track in enumerate(tracks)))


@router.get("/{playlist_id}/export")
def export_playlist(playlist_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    p = _resolve_user_playlist(db, user.id, playlist_id)
    if not p:
        raise HTTPException(status_code=404, detail="Playlist not found")
    detail = _liked_playlist_detail(db, user, p) if _is_liked_playlist_row(p) else playlist_detail(p.id, db=db, user=user)
    tracks = []
    for track in detail.tracks:
        tracks.append({
            "title": track.title,
            "artist": track.artist,
            "album": track.album,
            "duration_ms": track.duration_ms,
            "art_url": track.art_url,
            "source": track.source,
            "source_id": track.yt_video_id or track.subsonic_song_id or track.key,
            "subsonic_song_id": track.subsonic_song_id,
            "yt_video_id": track.yt_video_id,
            "yt_browse_id": track.yt_browse_id,
        })
    return {
        "format": "helix-playlist",
        "version": 1,
        "playlist": {"name": detail.playlist.name, "tracks": tracks},
    }


@router.post("/{playlist_id}/import/preview")
async def preview_playlist_import(payload: PlaylistImportPreviewRequest, playlist_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    p = _resolve_user_playlist(db, user.id, playlist_id)
    if not p:
        raise HTTPException(status_code=404, detail="Playlist not found")

    source = (payload.source or "").strip().lower()
    content = payload.content or ""
    url = (payload.url or "").strip()
    reported_count: Optional[int] = None
    imported_name = "Imported playlist"
    try:
        if source == "helix":
            imported_name, tracks = parse_helix_json(content)
        elif source == "spotify":
            if not content:
                raise ValueError("Export the Spotify playlist with Exportify, then upload its CSV file.")
            tracks = parse_exportify_csv(content)
            imported_name = (payload.filename or "Spotify playlist").rsplit(".", 1)[0].replace("_", " ")
        elif source == "ytmusic":
            if content:
                reported_count, tracks = parse_ytmusic_saved_html(content)
                imported_name = "YouTube Music Liked Music"
            else:
                imported_name, tracks = await asyncio.to_thread(parse_ytmusic_playlist_url, url)
        elif source == "pandora":
            imported_name, reported_count, tracks = await parse_pandora_playlist_url(url)
        else:
            raise ValueError("Unknown playlist import source.")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not read {source or 'playlist'} import: {exc}") from exc

    if not tracks:
        raise HTTPException(status_code=400, detail="No usable tracks were found in this import.")
    if len(tracks) > 2500:
        raise HTTPException(status_code=400, detail="Playlist imports are currently limited to 2,500 tracks at a time.")

    existing_keys = _playlist_existing_import_keys(db, p, user.id)
    duplicate_flags: list[bool] = []
    for track in tracks:
        possible = track_identity_keys(track)
        # Also accept the playlist's historical text key format for local comparisons.
        possible.add(f"text:{track.title}|{track.artist}")
        possible.add(f"text:{track.title.casefold()}|{track.artist.casefold()}")
        duplicate_flags.append(any(key in existing_keys for key in possible))

    rows = await _preview_match_tracks(tracks, duplicate_flags)
    counts = {"matched": 0, "review": 0, "unmatched": 0, "duplicate": 0}
    for row in rows:
        status = row.get("status") or "unmatched"
        counts[status] = counts.get(status, 0) + 1

    return {
        "source": source,
        "playlist_name": imported_name,
        "reported_count": reported_count,
        "parsed_count": len(tracks),
        "counts": counts,
        "tracks": rows,
    }


@router.post("/{playlist_id}/import/apply", response_model=PlaylistDetailResponse)
def apply_playlist_import(payload: PlaylistImportApplyRequest, playlist_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    p = _resolve_user_playlist(db, user.id, playlist_id)
    if not p:
        raise HTTPException(status_code=404, detail="Playlist not found")
    if len(payload.tracks) > 2500:
        raise HTTPException(status_code=400, detail="Too many tracks in one import.")

    existing_keys = _playlist_existing_import_keys(db, p, user.id)
    max_pos = -1
    if not _is_liked_playlist_row(p):
        max_pos = int(db.execute(select(func.max(PlaylistTrack.position)).where(PlaylistTrack.playlist_id == p.id)).scalar_one() or -1)

    for incoming in payload.tracks:
        add = PlaylistTrackAddRequest(**incoming.model_dump())
        key = _stable_key(add)
        text_key = f"text:{incoming.title.casefold()}|{incoming.artist.casefold()}"
        if payload.skip_existing and (key in existing_keys or text_key in existing_keys):
            continue
        vid = (incoming.yt_video_id or "").strip()
        if vid and not is_valid_yt_video_id(vid):
            vid = ""
        art_url = _safe_track_art_url(incoming.art_url, incoming.subsonic_song_id, vid)
        values = dict(
            user_id=user.id,
            key=key,
            title=(incoming.title or "").strip(),
            artist=(incoming.artist or "").strip(),
            album=(incoming.album or "").strip(),
            duration_ms=int(incoming.duration_ms or 0),
            art_url=art_url,
            source=(incoming.source or "").strip(),
            subsonic_song_id=(incoming.subsonic_song_id or "").strip(),
            yt_video_id=vid,
            yt_browse_id=(incoming.yt_browse_id or "").strip(),
            mb_recording_id="",
            mb_artist_id="",
        )
        if _is_liked_playlist_row(p):
            db.add(LikedTrack(**values))
        else:
            max_pos += 1
            db.add(PlaylistTrack(playlist_id=p.id, position=max_pos, **values))
        existing_keys.add(key)
        existing_keys.add(text_key)
        if vid:
            existing_keys.add(f"yt:{vid}")

    p.updated_at = datetime.utcnow()
    db.commit()
    invalidate_playlist_cover(p.id)
    return _liked_playlist_detail(db, user, p) if _is_liked_playlist_row(p) else playlist_detail(p.id, db=db, user=user)


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
                "yt_video_id": getattr(t, "yt_video_id", "") or "",
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
                "yt_video_id": getattr(t, "yt_video_id", "") or "",
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
        if client is not None:
            await client.close()

    return FileResponse(img_path, media_type="image/jpeg")

@router.delete("/{playlist_id}", response_model=dict[str, bool])
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
