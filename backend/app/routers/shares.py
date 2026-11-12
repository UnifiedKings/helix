from __future__ import annotations

import asyncio
import html
import json
import os
import secrets
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel
from sqlalchemy import select, text

from ..auth import get_current_user
from ..db import SessionLocal, engine
from ..integrations.subsonic import SubsonicClient
from ..integrations.ytmusic import get_album_full
from ..models import Playlist, PlaylistTrack, User
from ..player.engine import (
    _ensure_playable_for_stream,
    _stream_inbound_progressive,
    _stream_inbound_with_range,
    _stream_subsonic_with_range,
)
from ..settings_store import get_settings

router = APIRouter(tags=["shares"])


# Share links intentionally live outside the normal authenticated playback state.
# They are capability tokens that grant read-only access to one resource only.
with engine.begin() as conn:
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS share_links (
            id VARCHAR(36) PRIMARY KEY,
            token VARCHAR(128) NOT NULL UNIQUE,
            user_id VARCHAR(36) NOT NULL,
            resource_type VARCHAR(16) NOT NULL,
            resource_id TEXT NOT NULL,
            resource_source VARCHAR(16) NOT NULL DEFAULT 'subsonic',
            title TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at DATETIME NOT NULL,
            expires_at DATETIME NULL
        )
    """))
    columns = {row[1] for row in conn.execute(text("PRAGMA table_info(share_links)")).fetchall()}
    if "resource_source" not in columns:
        conn.execute(text("ALTER TABLE share_links ADD COLUMN resource_source VARCHAR(16) NOT NULL DEFAULT 'subsonic'"))
    if "metadata_json" not in columns:
        conn.execute(text("ALTER TABLE share_links ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_share_links_user_id ON share_links(user_id)"))
    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_share_links_token ON share_links(token)"))


class ShareCreate(BaseModel):
    resource_type: str
    resource_id: str
    resource_source: str | None = None
    expiration_days: int | None = None
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    duration_ms: int | None = None
    art_url: str | None = None


class _ReadOnlyDb:
    """No-op DB adapter for temporary YT playback.

    The normal player fulfillment helper mutates a QueueItem and calls commit(), but
    public shares must not mutate Helix/Subsonic state. Using this adapter lets us
    reuse Helix's temporary playback cache without holding a SQLAlchemy connection
    or persisting any share playback state.
    """

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None


def _expiration_options() -> list[int]:
    raw = os.getenv("HELIX_SHARE_EXPIRATION_DAYS", "1,7,30,365,0")
    values: list[int] = []
    for part in raw.split(","):
        try:
            value = int(part.strip())
        except Exception:
            continue
        if value >= 0 and value not in values:
            values.append(value)
    return values or [1, 7, 30, 365, 0]


def _default_expiration() -> int:
    try:
        value = int(os.getenv("HELIX_SHARE_DEFAULT_EXPIRATION_DAYS", "30"))
    except Exception:
        value = 30
    options = _expiration_options()
    return value if value in options else options[0]


def _settings() -> dict[str, Any]:
    db = SessionLocal()
    try:
        return dict(get_settings(db) or {})
    finally:
        db.close()


def _subsonic(settings: dict[str, Any]) -> SubsonicClient:
    base_url = str(settings.get("subsonic_base_url") or "").strip()
    username = str(settings.get("subsonic_username") or "").strip()
    password = str(settings.get("subsonic_password") or "").strip()
    if not base_url or not username or not password:
        raise HTTPException(status_code=503, detail="Subsonic is not configured")
    return SubsonicClient(
        base_url=base_url,
        username=username,
        password=password,
        client_name=str(settings.get("subsonic_client_name") or "Helix"),
        api_version=str(settings.get("subsonic_api_version") or "1.16.1"),
        timeout_s=int(settings.get("subsonic_timeout_s") or 20),
    )


def _row(token: str, *, include_expired: bool = False) -> dict[str, Any] | None:
    db = SessionLocal()
    try:
        row = db.execute(text("""
            SELECT id, token, user_id, resource_type, resource_id, resource_source,
                   title, metadata_json, created_at, expires_at
            FROM share_links WHERE token=:token
        """), {"token": token}).mappings().first()
        if not row:
            return None
        item = dict(row)
        expires = item.get("expires_at")
        if expires and isinstance(expires, str):
            expires = datetime.fromisoformat(expires)
        if not include_expired and expires and expires <= datetime.utcnow():
            return None
        item["expires_at"] = expires
        try:
            item["metadata"] = json.loads(str(item.get("metadata_json") or "{}"))
        except Exception:
            item["metadata"] = {}
        return item
    finally:
        db.close()


def _safe_external_art(url: str) -> str:
    value = (url or "").strip()
    return value if value.startswith("https://") else ""


def _yt_art(video_id: str, raw_url: str = "") -> str:
    return _safe_external_art(raw_url) or (f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg" if video_id else "")


def _duration_ms(value: Any, *, seconds: bool = False) -> int:
    try:
        parsed = int(value or 0)
    except Exception:
        return 0
    return parsed * 1000 if seconds else parsed


def _subsonic_track_payload(song: dict[str, Any], token: str, *, stream_id: str | None = None) -> dict[str, Any]:
    sid = str(song.get("id") or "")
    public_id = stream_id or sid
    return {
        "id": public_id,
        "title": str(song.get("title") or ""),
        "artist": str(song.get("artist") or ""),
        "album": str(song.get("album") or ""),
        "duration_ms": _duration_ms(song.get("duration"), seconds=True),
        "playable": bool(sid),
        "art_url": f"/api/public/shares/{token}/art/{public_id}" if sid else "",
    }


def _yt_track_payload(track: dict[str, Any], *, stream_id: str | None = None) -> dict[str, Any]:
    video_id = str(track.get("yt_video_id") or track.get("video_id") or track.get("videoId") or "").strip()
    public_id = stream_id or video_id
    duration = track.get("duration_ms")
    if not duration:
        duration = _duration_ms(track.get("duration_seconds"), seconds=True)
    if not duration:
        duration = _duration_ms(track.get("lengthMs"))
    art = str(track.get("art_url") or track.get("thumbnail_url") or track.get("thumbnail") or "")
    return {
        "id": public_id,
        "title": str(track.get("title") or ""),
        "artist": str(track.get("artist") or ""),
        "album": str(track.get("album") or ""),
        "duration_ms": int(duration or 0),
        "playable": bool(video_id),
        "art_url": _yt_art(video_id, art),
    }


async def _yt_album(album_id: str) -> dict[str, Any]:
    try:
        data = await asyncio.to_thread(get_album_full, album_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not load shared album: {exc}") from exc
    if not isinstance(data, dict) or not data:
        raise HTTPException(status_code=404, detail="Shared album no longer exists")
    return data


async def _public_payload(share: dict[str, Any]) -> dict[str, Any]:
    kind = str(share["resource_type"])
    rid = str(share["resource_id"])
    source = str(share.get("resource_source") or "subsonic").lower()
    token = str(share["token"])

    if kind == "playlist":
        db = SessionLocal()
        try:
            playlist = db.get(Playlist, rid)
            if not playlist:
                raise HTTPException(status_code=404, detail="Shared playlist no longer exists")
            rows = db.execute(
                select(PlaylistTrack)
                .where(PlaylistTrack.playlist_id == rid)
                .order_by(PlaylistTrack.position)
            ).scalars().all()

            tracks: list[dict[str, Any]] = []
            for track in rows:
                if track.subsonic_song_id:
                    art_url = f"/api/public/shares/{token}/art/{track.id}"
                else:
                    art_url = _yt_art(str(track.yt_video_id or ""), str(track.art_url or ""))
                tracks.append({
                    "id": track.id,
                    "title": track.title,
                    "artist": track.artist,
                    "album": track.album,
                    "duration_ms": int(track.duration_ms or 0),
                    "playable": bool(track.subsonic_song_id or track.yt_video_id),
                    "art_url": art_url,
                })

            art = next((track["art_url"] for track in tracks if track.get("art_url")), "")
            return {
                "type": "playlist",
                "title": playlist.name,
                "artist": "",
                "art_url": art,
                "tracks": tracks,
                "expires_at": share.get("expires_at"),
            }
        finally:
            db.close()

    if kind == "track":
        if source == "ytmusic":
            metadata = dict(share.get("metadata") or {})
            metadata["yt_video_id"] = rid
            track = _yt_track_payload(metadata, stream_id=rid)
            if not track["title"]:
                track["title"] = str(share.get("title") or "Shared track")
            return {
                "type": "track",
                "title": track["title"],
                "artist": track["artist"],
                "art_url": track["art_url"],
                "tracks": [track],
                "expires_at": share.get("expires_at"),
            }

        client = _subsonic(_settings())
        try:
            song = await client.get_song(rid)
            if not song:
                raise HTTPException(status_code=404, detail="Shared track no longer exists")
            track = _subsonic_track_payload(song, token)
            return {
                "type": "track",
                "title": track["title"],
                "artist": track["artist"],
                "art_url": track["art_url"],
                "tracks": [track],
                "expires_at": share.get("expires_at"),
            }
        finally:
            await client.close()

    if kind == "album":
        if source == "ytmusic":
            album = await _yt_album(rid)
            album_title = str(album.get("title") or album.get("name") or share.get("title") or "Album")
            album_artist = str(album.get("artist") or (share.get("metadata") or {}).get("artist") or "")
            album_art = str(album.get("thumbnail_url") or album.get("art_url") or (share.get("metadata") or {}).get("art_url") or "")
            tracks = []
            for row in album.get("tracks") or []:
                if not isinstance(row, dict):
                    continue
                normalized = dict(row)
                normalized.setdefault("album", album_title)
                normalized.setdefault("artist", album_artist)
                normalized.setdefault("art_url", album_art)
                tracks.append(_yt_track_payload(normalized))
            return {
                "type": "album",
                "title": album_title,
                "artist": album_artist,
                "art_url": _safe_external_art(album_art) or next((t["art_url"] for t in tracks if t["art_url"]), ""),
                "tracks": tracks,
                "expires_at": share.get("expires_at"),
            }

        client = _subsonic(_settings())
        try:
            album = await client.get_album(rid)
            if not album:
                raise HTTPException(status_code=404, detail="Shared album no longer exists")
            tracks = [
                _subsonic_track_payload(song, token)
                for song in (album.get("song") or [])
                if isinstance(song, dict)
            ]
            cover = str(album.get("coverArt") or "")
            art_url = f"/api/public/shares/{token}/art/__root__" if cover else next((t["art_url"] for t in tracks if t["art_url"]), "")
            return {
                "type": "album",
                "title": str(album.get("name") or album.get("title") or "Album"),
                "artist": str(album.get("artist") or ""),
                "art_url": art_url,
                "tracks": tracks,
                "expires_at": share.get("expires_at"),
            }
        finally:
            await client.close()

    raise HTTPException(status_code=400, detail="Unsupported share type")


@router.get("/api/shares/config")
def share_config(user: User = Depends(get_current_user)):
    return {
        "expiration_days": _expiration_options(),
        "default_expiration_days": _default_expiration(),
    }


@router.get("/api/shares")
def list_shares(user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        rows = db.execute(text("""
            SELECT id, token, resource_type, resource_id, resource_source,
                   title, metadata_json, created_at, expires_at
            FROM share_links WHERE user_id=:uid ORDER BY created_at DESC
        """), {"uid": user.id}).mappings().all()
        now = datetime.utcnow()
        out: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            exp = item.get("expires_at")
            exp_dt = datetime.fromisoformat(exp) if isinstance(exp, str) and exp else exp
            item["expired"] = bool(exp_dt and exp_dt <= now)
            item["url"] = f"/share/{item['token']}"
            try:
                metadata = json.loads(str(item.pop("metadata_json", "{}") or "{}"))
            except Exception:
                metadata = {}

            item["artist"] = str(metadata.get("artist") or "")
            item["album"] = str(metadata.get("album") or "")
            item["art_url"] = str(metadata.get("art_url") or "")

            # Playlist covers are generated dynamically from the playlist's current
            # contents, so existing share cards always reflect the live playlist art.
            if item.get("resource_type") == "playlist":
                item["art_url"] = f"/api/playlists/{item['resource_id']}/cover"

            out.append(item)
        return out
    finally:
        db.close()


@router.post("/api/shares")
async def create_share(payload: ShareCreate, user: User = Depends(get_current_user)):
    kind = payload.resource_type.strip().lower()
    rid = payload.resource_id.strip()
    source = (payload.resource_source or "subsonic").strip().lower()
    if kind not in {"track", "album", "playlist"} or not rid:
        raise HTTPException(status_code=400, detail="Invalid share target")
    if kind == "playlist":
        source = "helix"
    elif source not in {"subsonic", "ytmusic"}:
        raise HTTPException(status_code=400, detail="Unsupported share source")

    options = _expiration_options()
    days = _default_expiration() if payload.expiration_days is None else payload.expiration_days
    if days not in options:
        raise HTTPException(status_code=400, detail="Expiration is not allowed by this Helix server")

    title = ""
    metadata: dict[str, Any] = {}

    if kind == "playlist":
        db = SessionLocal()
        try:
            playlist = db.get(Playlist, rid)
            if not playlist or playlist.user_id != user.id:
                raise HTTPException(status_code=404, detail="Playlist not found")
            title = playlist.name
        finally:
            db.close()
    elif source == "subsonic":
        client = _subsonic(_settings())
        try:
            obj = await (client.get_song(rid) if kind == "track" else client.get_album(rid))
            if not obj:
                raise HTTPException(status_code=404, detail=f"{kind.title()} not found in Subsonic")
            title = str(obj.get("title") or obj.get("name") or kind.title())
            metadata = {
                "artist": str(obj.get("artist") or payload.artist or ""),
                "art_url": str(payload.art_url or ""),
            }
        finally:
            await client.close()
    elif kind == "album":
        album = await _yt_album(rid)
        title = str(album.get("title") or album.get("name") or payload.title or "Album")
        metadata = {
            "artist": str(album.get("artist") or payload.artist or ""),
            "art_url": str(album.get("thumbnail_url") or album.get("art_url") or payload.art_url or ""),
        }
    else:
        # YTMusic search results already carry the metadata required for a stable
        # shared-track page. The video id is the authoritative playback identity.
        title = str(payload.title or "").strip()
        if not title:
            raise HTTPException(status_code=400, detail="Track title is required for a YTMusic share")
        metadata = {
            "title": title,
            "artist": str(payload.artist or ""),
            "album": str(payload.album or ""),
            "duration_ms": max(0, int(payload.duration_ms or 0)),
            "art_url": _safe_external_art(str(payload.art_url or "")),
            "yt_video_id": rid,
        }

    token = secrets.token_urlsafe(24)
    now = datetime.utcnow()
    expires = None if days == 0 else now + timedelta(days=days)
    db = SessionLocal()
    try:
        db.execute(text("""
            INSERT INTO share_links(
                id, token, user_id, resource_type, resource_id, resource_source,
                title, metadata_json, created_at, expires_at
            ) VALUES(
                :id, :token, :uid, :kind, :rid, :source,
                :title, :metadata, :created, :expires
            )
        """), {
            "id": secrets.token_hex(18),
            "token": token,
            "uid": user.id,
            "kind": kind,
            "rid": rid,
            "source": source,
            "title": title,
            "metadata": json.dumps(metadata, separators=(",", ":")),
            "created": now,
            "expires": expires,
        })
        db.commit()
    finally:
        db.close()

    return {
        "token": token,
        "url": f"/share/{token}",
        "resource_type": kind,
        "resource_id": rid,
        "resource_source": source,
        "title": title,
        "expires_at": expires,
    }


@router.delete("/api/shares/{token}")
def delete_share(token: str, user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        result = db.execute(
            text("DELETE FROM share_links WHERE token=:token AND user_id=:uid"),
            {"token": token, "uid": user.id},
        )
        db.commit()
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Share not found")
        return {"ok": True}
    finally:
        db.close()


@router.get("/api/public/shares/{token}")
async def public_share(token: str):
    share = _row(token)
    if not share:
        raise HTTPException(status_code=404, detail="This share link has expired or is no longer available")
    return await _public_payload(share)


async def _subsonic_cover_bytes(song_id: str = "", cover_id: str = "") -> bytes:
    client = _subsonic(_settings())
    try:
        resolved_cover = cover_id
        if not resolved_cover and song_id:
            song = await client.get_song(song_id)
            resolved_cover = str((song or {}).get("coverArt") or song_id)
        if not resolved_cover:
            raise HTTPException(status_code=404, detail="Artwork unavailable")
        data = await client.fetch_cover_art_bytes(resolved_cover, size=768)
        if not data:
            raise HTTPException(status_code=404, detail="Artwork unavailable")
        return data
    finally:
        await client.close()


@router.get("/api/public/shares/{token}/art/{item_id}")
async def public_share_art(token: str, item_id: str):
    share = _row(token)
    if not share:
        raise HTTPException(status_code=404, detail="Share unavailable")

    kind = str(share["resource_type"])
    source = str(share.get("resource_source") or "subsonic")
    if source == "ytmusic" and kind in {"track", "album"}:
        raise HTTPException(status_code=404, detail="Artwork is served directly for this share")

    song_id = ""
    cover_id = ""
    if kind == "track":
        if source != "subsonic" or item_id != share["resource_id"]:
            raise HTTPException(status_code=404, detail="Artwork unavailable")
        song_id = str(share["resource_id"])
    elif kind == "album":
        if source != "subsonic":
            raise HTTPException(status_code=404, detail="Artwork unavailable")
        client = _subsonic(_settings())
        try:
            album = await client.get_album(str(share["resource_id"]))
        finally:
            await client.close()
        if not album:
            raise HTTPException(status_code=404, detail="Artwork unavailable")
        if item_id == "__root__":
            cover_id = str(album.get("coverArt") or "")
        else:
            song = next(
                (row for row in (album.get("song") or []) if isinstance(row, dict) and str(row.get("id") or "") == item_id),
                None,
            )
            if not song:
                raise HTTPException(status_code=404, detail="Artwork unavailable")
            cover_id = str(song.get("coverArt") or album.get("coverArt") or item_id)
    elif kind == "playlist":
        db = SessionLocal()
        try:
            track = db.execute(
                select(PlaylistTrack).where(
                    PlaylistTrack.playlist_id == share["resource_id"],
                    PlaylistTrack.id == item_id,
                )
            ).scalar_one_or_none()
            if not track or not track.subsonic_song_id:
                raise HTTPException(status_code=404, detail="Artwork unavailable")
            song_id = str(track.subsonic_song_id)
        finally:
            db.close()
    else:
        raise HTTPException(status_code=404, detail="Artwork unavailable")

    data = await _subsonic_cover_bytes(song_id=song_id, cover_id=cover_id)
    content_type = "image/png" if data.startswith(b"\x89PNG") else "image/jpeg"
    return Response(content=data, media_type=content_type, headers={"Cache-Control": "public, max-age=3600"})


async def _resolve_stream_target(share: dict[str, Any], item_id: str) -> dict[str, Any]:
    kind = str(share["resource_type"])
    source = str(share.get("resource_source") or "subsonic").lower()
    rid = str(share["resource_id"])

    if kind == "track":
        if item_id != rid:
            raise HTTPException(status_code=404, detail="Track is not part of this share")
        if source == "subsonic":
            return {"source": "subsonic", "subsonic_song_id": rid, "yt_video_id": "", "title": share.get("title") or "", "artist": "", "album": "", "duration_ms": 0, "art_url": "", "yt_browse_id": ""}
        metadata = dict(share.get("metadata") or {})
        return {
            "source": "ytmusic",
            "subsonic_song_id": "",
            "yt_video_id": rid,
            "title": str(metadata.get("title") or share.get("title") or ""),
            "artist": str(metadata.get("artist") or ""),
            "album": str(metadata.get("album") or ""),
            "duration_ms": int(metadata.get("duration_ms") or 0),
            "art_url": _yt_art(rid, str(metadata.get("art_url") or "")),
            "yt_browse_id": "",
        }

    if kind == "playlist":
        db = SessionLocal()
        try:
            track = db.execute(
                select(PlaylistTrack).where(
                    PlaylistTrack.playlist_id == rid,
                    PlaylistTrack.id == item_id,
                )
            ).scalar_one_or_none()
            if not track:
                raise HTTPException(status_code=404, detail="Track is not part of this share")
            if not (track.subsonic_song_id or track.yt_video_id):
                raise HTTPException(status_code=404, detail="Track is not playable")
            return {
                "source": "subsonic" if track.subsonic_song_id else "ytmusic",
                "subsonic_song_id": str(track.subsonic_song_id or ""),
                "yt_video_id": str(track.yt_video_id or ""),
                "title": track.title or "",
                "artist": track.artist or "",
                "album": track.album or "",
                "duration_ms": int(track.duration_ms or 0),
                "art_url": str(track.art_url or ""),
                "yt_browse_id": str(track.yt_browse_id or ""),
            }
        finally:
            db.close()

    if kind == "album":
        if source == "subsonic":
            client = _subsonic(_settings())
            try:
                album = await client.get_album(rid)
            finally:
                await client.close()
            song = next(
                (row for row in ((album or {}).get("song") or []) if isinstance(row, dict) and str(row.get("id") or "") == item_id),
                None,
            )
            if not song:
                raise HTTPException(status_code=404, detail="Track is not part of this share")
            return {
                "source": "subsonic",
                "subsonic_song_id": item_id,
                "yt_video_id": "",
                "title": str(song.get("title") or ""),
                "artist": str(song.get("artist") or ""),
                "album": str(song.get("album") or (album or {}).get("name") or ""),
                "duration_ms": _duration_ms(song.get("duration"), seconds=True),
                "art_url": "",
                "yt_browse_id": "",
            }

        album = await _yt_album(rid)
        track = next(
            (
                row for row in (album.get("tracks") or [])
                if isinstance(row, dict)
                and str(row.get("yt_video_id") or row.get("video_id") or row.get("videoId") or "") == item_id
            ),
            None,
        )
        if not track:
            raise HTTPException(status_code=404, detail="Track is not part of this share")
        video_id = item_id
        duration = track.get("duration_ms") or _duration_ms(track.get("duration_seconds"), seconds=True) or _duration_ms(track.get("lengthMs"))
        return {
            "source": "ytmusic",
            "subsonic_song_id": "",
            "yt_video_id": video_id,
            "title": str(track.get("title") or ""),
            "artist": str(track.get("artist") or album.get("artist") or ""),
            "album": str(track.get("album") or album.get("title") or ""),
            "duration_ms": int(duration or 0),
            "art_url": _yt_art(video_id, str(track.get("art_url") or album.get("thumbnail_url") or "")),
            "yt_browse_id": rid,
        }

    raise HTTPException(status_code=404, detail="Track is not part of this share")


@router.get("/api/public/shares/{token}/stream/{item_id}")
async def public_stream(token: str, item_id: str, request: Request):
    share = _row(token)
    if not share:
        raise HTTPException(status_code=404, detail="Share unavailable")

    target = await _resolve_stream_target(share, item_id)
    settings = _settings()
    if target["source"] == "subsonic" and target["subsonic_song_id"]:
        cur = SimpleNamespace(subsonic_song_id=target["subsonic_song_id"], source="subsonic")
        return await _stream_subsonic_with_range(request, cur, settings=settings)

    # YTMusic-backed shares use Helix's temporary playback cache only. DownloadJob
    # defaults to persist_to_subsonic=False, and the no-op DB prevents player state
    # or library rows from being changed by a public listener.
    cur = SimpleNamespace(
        id=f"share:{token}:{item_id}",
        session_user_id=str(share.get("user_id") or ""),
        position=0,
        kind="song",
        title=target["title"],
        artist=target["artist"],
        album=target["album"],
        duration_ms=target["duration_ms"],
        art_url=target["art_url"],
        source="ytmusic",
        subsonic_song_id="",
        yt_video_id=target["yt_video_id"],
        yt_browse_id=target["yt_browse_id"],
        mb_recording_id="",
        mb_artist_id="",
        inbound_path="",
        download_status="",
        is_playable=False,
        error="",
    )
    # Public share playback must be reliable on the first request. Progressive
    # playback from yt-dlp's growing .part file can leave browser <audio> elements
    # stuck until the same track is requested again after the temporary download
    # has finished. Wait for the temporary file to finish, then serve it with
    # normal byte-range support. DownloadJob remains non-persistent, so this does
    # not add the shared track to Subsonic/the Helix library.
    await _ensure_playable_for_stream(
        _ReadOnlyDb(),
        cur,
        settings=settings,
        allow_subsonic_wait=False,
        progressive_inbound=False,
    )
    if cur.source == "subsonic" and cur.subsonic_song_id:
        return await _stream_subsonic_with_range(request, cur, settings=settings)
    if (cur.inbound_path or "").endswith(".part") or cur.download_status == "DOWNLOADING":
        return _stream_inbound_progressive(request, cur)
    return await _stream_inbound_with_range(request, cur)


@router.get("/share/{token}", response_class=HTMLResponse)
async def share_preview(token: str, request: Request):
    share = _row(token)
    if not share:
        return HTMLResponse(
            "<!doctype html><html><head><title>Share unavailable · Helix</title></head>"
            "<body><h1>This share link has expired or is no longer available.</h1></body></html>",
            status_code=404,
        )

    try:
        data = await _public_payload(share)
    except Exception:
        data = {"title": share.get("title") or "Shared music", "artist": "", "art_url": ""}

    origin = str(request.base_url).rstrip("/")
    title = html.escape(str(data.get("title") or "Shared music"))
    artist = html.escape(str(data.get("artist") or ""))
    desc = html.escape(f"{artist + ' · ' if artist else ''}Shared from Helix")
    art = str(data.get("art_url") or "")
    art_url = f"{origin}{art}" if art.startswith("/") else art
    target = f"/share-player/{token}"
    og_type = "song" if share["resource_type"] == "track" else "album" if share["resource_type"] == "album" else "playlist"
    image_tag = f'<meta property="og:image" content="{html.escape(art_url)}">' if art_url else ""

    return HTMLResponse(f'''<!doctype html><html><head><meta charset="utf-8"><title>{title} · Helix</title>
<meta property="og:site_name" content="Helix"><meta property="og:title" content="{title}"><meta property="og:description" content="{desc}"><meta property="og:type" content="music.{og_type}"><meta property="og:url" content="{origin}/share/{token}">{image_tag}<meta name="twitter:card" content="summary_large_image"><meta http-equiv="refresh" content="0;url={target}"></head><body><p><a href="{target}">Open shared music in Helix</a></p></body></html>''')
