from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import get_current_user
from ..db import SessionLocal
from ..models import User
from ..settings_store import get_settings
from ..integrations.subsonic import SubsonicClient
from ..integrations.ytmusic import get_album_full


router = APIRouter(prefix="/api", tags=["album"])


def _load_settings_short() -> Dict[str, Any]:
    db = SessionLocal()
    try:
        return dict(get_settings(db) or {})
    finally:
        db.close()


def _subsonic_client_from_settings(settings: Dict[str, Any]) -> SubsonicClient | None:
    base_url = str(settings.get("subsonic_base_url") or "").strip()
    username = str(settings.get("subsonic_username") or "").strip()
    password = str(settings.get("subsonic_password") or "").strip()
    if not base_url or not username or not password:
        return None
    return SubsonicClient(
        base_url=base_url,
        username=username,
        password=password,
        client_name=str(settings.get("subsonic_client_name") or "Helix"),
        api_version=str(settings.get("subsonic_api_version") or "1.16.1"),
        timeout_s=int(settings.get("subsonic_timeout_s") or 20),
    )


def _subsonic_album_detail(album: Dict[str, Any]) -> Dict[str, Any]:
    album_id = str(album.get("id") or "")
    title = str(album.get("name") or album.get("title") or "")
    artist = str(album.get("artist") or "")
    cover_id = str(album.get("coverArt") or "").strip()
    art_url = f"/api/art/subsonic/{cover_id}?size=768" if cover_id else ""

    tracks = []
    raw_tracks = album.get("song") or []
    if isinstance(raw_tracks, list):
        for song in raw_tracks:
            if not isinstance(song, dict):
                continue
            song_cover_id = str(song.get("coverArt") or cover_id or "").strip()
            song_art_url = f"/api/art/subsonic/{song_cover_id}?size=512" if song_cover_id else art_url
            try:
                duration_seconds = int(song.get("duration") or 0)
            except (TypeError, ValueError):
                duration_seconds = 0
            tracks.append({
                "title": str(song.get("title") or ""),
                "artist": str(song.get("artist") or artist),
                "album": str(song.get("album") or title),
                "duration_seconds": duration_seconds,
                "duration_ms": duration_seconds * 1000 if duration_seconds else 0,
                "art_url": song_art_url,
                "thumbnail_url": song_art_url,
                "source": "subsonic",
                "subsonic_song_id": str(song.get("id") or ""),
            })

    return {
        "browse_id": album_id,
        "subsonic_album_id": album_id,
        "source": "subsonic",
        "title": title,
        "artist": artist,
        "year": album.get("year") or "",
        "thumbnail_url": art_url,
        "art_url": art_url,
        "tracks": tracks,
    }


@router.get("/album/{album_id}")
async def album_view(
    album_id: str,
    source: str | None = Query(default=None),
    user: User = Depends(get_current_user),
):
    aid = (album_id or "").strip()
    if not aid:
        raise HTTPException(status_code=400, detail="album_id is required")

    requested_source = (source or "").strip().lower()

    if requested_source == "subsonic":
        settings = _load_settings_short()
        client = _subsonic_client_from_settings(settings)
        if client is None:
            raise HTTPException(status_code=503, detail="Subsonic is not configured.")
        try:
            album = await client.get_album(aid)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Could not load album from Subsonic: {exc}") from exc
        finally:
            try:
                await client.close()
            except Exception:
                pass
        if not album:
            raise HTTPException(status_code=404, detail="Album not found in Subsonic.")
        return _subsonic_album_detail(album)

    # Existing behavior remains the default for YTMusic links and old clients.
    try:
        data = get_album_full(aid)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not load album from YouTube Music: {exc}") from exc
    if not data:
        raise HTTPException(status_code=404, detail="Album not found on YouTube Music.")
    return data
