from __future__ import annotations

from pydantic import BaseModel

from .likes import LikeToggleRequest


class PlaylistCreateRequest(BaseModel):
    name: str


class PlaylistResponse(BaseModel):
    id: str
    name: str
    system_key: str = ""
    track_count: int = 0
    created_at: str
    updated_at: str
    thumbnail_url: str = ""


class PlaylistTrackAddRequest(LikeToggleRequest):
    """Same payload as LikeToggleRequest; adds a track to a playlist."""


class PlaylistTrackResponse(BaseModel):
    id: str
    key: str
    title: str
    artist: str
    album: str = ""
    duration_ms: int = 0
    art_url: str = ""
    source: str = ""
    subsonic_song_id: str = ""
    yt_video_id: str = ""
    yt_browse_id: str = ""
    mb_recording_id: str = ""
    mb_artist_id: str = ""
    created_at: str


class PlaylistDetailResponse(BaseModel):
    playlist: PlaylistResponse
    tracks: list[PlaylistTrackResponse] = []


class DislikeResponse(BaseModel):
    disliked: bool
