from __future__ import annotations

from typing import Optional
from pydantic import BaseModel


class AutoplaySetRequest(BaseModel):
    enabled: bool


class LikeToggleRequest(BaseModel):
    # Stable identity: prefer subsonic_song_id else yt_video_id.
    subsonic_song_id: Optional[str] = None
    yt_video_id: Optional[str] = None
    yt_browse_id: Optional[str] = None
    source: Optional[str] = None
    title: str
    artist: str
    album: Optional[str] = None
    duration_ms: Optional[int] = None
    art_url: Optional[str] = None
    mb_recording_id: Optional[str] = None
    mb_artist_id: Optional[str] = None
    mb_match_confidence: Optional[float] = None
    mb_match_type: Optional[str] = None


class LikeResponse(BaseModel):
    liked: bool


class LikedTrackResponse(BaseModel):
    id: str
    title: str
    artist: str
    album: str = ""
    duration_ms: int = 0
    art_url: str = ""
    source: str = ""
    subsonic_song_id: str = ""
    yt_video_id: str = ""
    stale_subsonic: bool = False
    ytmusic_recovered_at: str = ""
    created_at: str


class LikedTracksResponse(BaseModel):
    items: list[LikedTrackResponse]


class DislikeToggleRequest(LikeToggleRequest):
    """Same payload shape as LikeToggleRequest."""
