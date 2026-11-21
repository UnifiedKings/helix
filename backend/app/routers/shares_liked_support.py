"""Read-only public sharing support for the system Liked Songs playlist.

This adapter is registered ahead of the existing artwork route and extends the
existing public share payload/stream resolver without changing normal playlists.
It intentionally never uses the recipient's authentication state.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from sqlalchemy import select

from ..db import SessionLocal
from ..models import LikedTrack, Playlist
from . import shares

router = APIRouter(tags=["shares"])


def _liked_playlist(db: Any, share: dict[str, Any]) -> Playlist | None:
    if share.get("resource_type") != "playlist":
        return None
    playlist = db.get(Playlist, str(share["resource_id"]))
    if playlist is None:
        return None
    return playlist if playlist.system_key == "liked" else None


def _liked_track(share: dict[str, Any], item_id: str) -> LikedTrack:
    db = SessionLocal()
    try:
        playlist = _liked_playlist(db, share)
        if playlist is None:
            raise HTTPException(status_code=404, detail="Not a liked playlist")
        track = db.execute(
            select(LikedTrack).where(
                LikedTrack.user_id == playlist.user_id,
                LikedTrack.id == item_id,
            )
        ).scalar_one_or_none()
        if track is None:
            raise HTTPException(status_code=404, detail="Track is not part of this share")
        db.expunge(track)
        return track
    finally:
        db.close()


def _track_target(track: LikedTrack) -> dict[str, Any]:
    # Recovered YTMusic ids are usable when the old library item is stale.
    subsonic_id = str(track.subsonic_song_id or "") if not track.stale_subsonic else ""
    yt_id = str(track.yt_video_id or "")
    return {
        "source": "subsonic" if subsonic_id else "ytmusic",
        "subsonic_song_id": subsonic_id,
        "yt_video_id": yt_id,
        "title": str(track.title or ""),
        "artist": str(track.artist or ""),
        "album": str(track.album or ""),
        "duration_ms": int(track.duration_ms or 0),
        "art_url": str(track.art_url or ""),
        "yt_browse_id": str(track.yt_browse_id or ""),
    }


def install() -> None:
    """Extend the existing share resolver before FastAPI serves requests."""
    original_payload = shares._public_payload
    original_stream_target = shares._resolve_stream_target

    async def public_payload(share: dict[str, Any]) -> dict[str, Any]:
        db = SessionLocal()
        try:
            playlist = _liked_playlist(db, share)
            if playlist is None:
                return await original_payload(share)

            rows = db.execute(
                select(LikedTrack)
                .where(LikedTrack.user_id == playlist.user_id)
                .order_by(LikedTrack.created_at.desc())
                .limit(5000)
            ).scalars().all()

            token = str(share["token"])
            tracks = []
            for track in rows:
                target = _track_target(track)
                subsonic_id = target["subsonic_song_id"]
                yt_id = target["yt_video_id"]
                art = (
                    f"/api/public/shares/{token}/art/{track.id}"
                    if subsonic_id else shares._yt_art(yt_id, str(track.art_url or ""))
                )
                tracks.append({
                    "id": track.id,
                    "title": track.title,
                    "artist": track.artist,
                    "album": track.album,
                    "duration_ms": int(track.duration_ms or 0),
                    "playable": bool(subsonic_id or yt_id),
                    "art_url": art,
                })

            return {
                "type": "playlist",
                "title": playlist.name,
                "artist": "",
                "art_url": next((t["art_url"] for t in tracks if t["art_url"]), ""),
                "tracks": tracks,
                "expires_at": share.get("expires_at"),
            }
        finally:
            db.close()

    async def stream_target(share: dict[str, Any], item_id: str) -> dict[str, Any]:
        db = SessionLocal()
        try:
            liked = _liked_playlist(db, share)
        finally:
            db.close()
        if liked is None:
            return await original_stream_target(share, item_id)

        track = _liked_track(share, item_id)
        target = _track_target(track)
        if not (target["subsonic_song_id"] or target["yt_video_id"]):
            raise HTTPException(status_code=404, detail="Track is not playable")
        return target

    shares._public_payload = public_payload
    shares._resolve_stream_target = stream_target


@router.get("/api/public/shares/{token}/art/{item_id}")
async def liked_share_art(token: str, item_id: str) -> Response:
    share = shares._row(token)
    if not share:
        raise HTTPException(status_code=404, detail="Share unavailable")

    db = SessionLocal()
    try:
        liked = _liked_playlist(db, share)
    finally:
        db.close()
    if liked is None:
        return await shares.public_share_art(token, item_id)

    track = _liked_track(share, item_id)
    target = _track_target(track)
    if not target["subsonic_song_id"]:
        raise HTTPException(status_code=404, detail="Artwork unavailable")

    data = await shares._subsonic_cover_bytes(song_id=target["subsonic_song_id"], cover_id="")
    content_type = "image/png" if data.startswith(b"\\x89PNG") else "image/jpeg"
    return Response(
        content=data,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=3600"},
    )
