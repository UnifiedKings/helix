from __future__ import annotations

from typing import Optional, List, Dict
from pydantic import BaseModel, Field


# --- Subsonic availability / resolve (for UI badges) ---


class SubsonicResolveSongCandidate(BaseModel):
    key: str = Field(description="Client-provided stable key for correlation (e.g., video_id).")
    title: str
    artist: str
    album: str = ""
    duration_seconds: Optional[int] = None

class SubsonicResolveAlbumCandidate(BaseModel):
    key: str = Field(description="Client-provided stable key for correlation (e.g., browse_id).")
    title: str
    artist: str
    browse_id: str = Field(default="", description="YTMusic browse_id for fetching tracklist when verifying completeness.")
    year: str = ""

class SubsonicResolveRequest(BaseModel):
    songs: List[SubsonicResolveSongCandidate] = Field(default_factory=list)
    albums: List[SubsonicResolveAlbumCandidate] = Field(default_factory=list)

class SubsonicResolveSongResult(BaseModel):
    available: bool
    subsonic_song_id: str = ""

class SubsonicResolveAlbumResult(BaseModel):
    available: bool
    subsonic_album_id: str = ""
    complete: bool = False
    expected_track_count: int = 0
    matched_track_count: int = 0
    subsonic_track_count: int = 0

class SubsonicResolveResponse(BaseModel):
    songs: Dict[str, SubsonicResolveSongResult] = Field(default_factory=dict)
    albums: Dict[str, SubsonicResolveAlbumResult] = Field(default_factory=dict)
