from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


class SetupRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=256)


class LoginRequest(BaseModel):
    username: str
    password: str


class MeResponse(BaseModel):
    id: str
    username: str
    role: str


class AdminCreateUserRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=256)
    role: str = Field(default="user")  # "admin" | "user"


class AdminUserResponse(BaseModel):
    id: str
    username: str
    role: str
    is_active: bool


class AdminUpdateUserRequest(BaseModel):
    is_active: Optional[bool] = None
    role: Optional[str] = None


# --- Player / Queue API ---

class PlayerQueueItem(BaseModel):
    id: str
    position: int
    title: str
    artist: str
    album: str = ""
    duration_ms: int = 0
    art_url: str = ""
    source: str = "subsonic"
    subsonic_song_id: str = ""
    yt_video_id: str = ""
    is_playable: bool = False
    error: str = ""


class PlayerStateResponse(BaseModel):
    is_playing: bool
    current_index: int
    now_playing: Optional[PlayerQueueItem] = None
    queue: list[PlayerQueueItem] = []
    autoplay_enabled: bool = True
    active_station_id: str = ""
    active_station: Optional[StationResponse] = None


class PlayerPlayTrackRequest(BaseModel):
    # Prefer passing rich metadata from the UI; recording MBID is optional.
    recording_id: Optional[str] = None
    title: str
    artist: str
    album: Optional[str] = None
    duration_ms: Optional[int] = None
    art_url: Optional[str] = None
    # Primary id for YT Music tracks
    yt_video_id: Optional[str] = None
    ytmusic_url: Optional[str] = None


class PlayerPlayAlbumRequest(BaseModel):
    # YT Music album identifier (browseId). MusicBrainz is no longer the primary source.
    browse_id: str
    # Optional metadata for better UX/fallback.
    title: Optional[str] = None
    artist: Optional[str] = None
    art_url: Optional[str] = None


class PlayerJumpRequest(BaseModel):
    index: int


class PlayerQueueAppendAlbumRequest(BaseModel):
    browse_id: str
    title: Optional[str] = None
    artist: Optional[str] = None
    art_url: Optional[str] = None


class PlayerQueueAppendTrackRequest(BaseModel):
    recording_id: Optional[str] = None
    title: str
    artist: str
    album: Optional[str] = None
    duration_ms: Optional[int] = None
    art_url: Optional[str] = None
    yt_video_id: Optional[str] = None
    ytmusic_url: Optional[str] = None


class PlayerRemoveQueueItemResponse(BaseModel):
    ok: bool = True


class PlayerHistoryItem(BaseModel):
    id: str
    queue_item_id: str
    title: str
    artist: str
    album: str = ""
    duration_ms: int = 0
    art_url: str = ""
    source: str = "subsonic"
    event: str = "skipped"
    reason: str = ""
    played_ms: int = 0
    created_at: str


class PlayerHistoryResponse(BaseModel):
    limit: int
    items: list[PlayerHistoryItem] = []


class PlayerPositionRequest(BaseModel):
    queue_item_id: Optional[str] = None
    position_ms: int = 0


class PlayerActionRequest(BaseModel):
    position_ms: Optional[int] = None


# --- Stations / Likes ---


class StationCreateRequest(BaseModel):
    name: str
    seed_type: str = "artist"  # artist|track
    seed_title: str = ""
    seed_artist: str = ""
    mb_artist_id: Optional[str] = None
    mb_recording_id: Optional[str] = None
    discovery: float = 0.35
    seed_influence: float = 0.75
    artist_cooldown: int = 5
    # 0=low, 1=medium, 2=high
    artist_variety: int = 1
    allow_seed_alternates: bool = False
    era_start: int = 0
    era_end: int = 0
    popularity_bias: int = 50
    tag_strictness: int = 70
    artist_blacklist: str = ""
    temperature: float = 0.9


class StationUpdateRequest(BaseModel):
    name: Optional[str] = None
    discovery: Optional[float] = None
    seed_influence: Optional[float] = None
    artist_cooldown: Optional[int] = None
    artist_variety: Optional[int] = None
    allow_seed_alternates: Optional[bool] = None
    era_start: Optional[int] = None
    era_end: Optional[int] = None
    popularity_bias: Optional[int] = None
    tag_strictness: Optional[int] = None
    artist_blacklist: Optional[str] = None


class StationResponse(BaseModel):
    id: str
    name: str
    seed_type: str
    seed_title: str
    seed_artist: str
    mb_artist_id: str = ""
    mb_recording_id: str = ""
    discovery: float = 0.35
    seed_influence: float = 0.75
    artist_cooldown: int = 5
    artist_variety: int = 1
    allow_seed_alternates: bool = False
    era_start: int = 0
    era_end: int = 0
    popularity_bias: int = 50
    tag_strictness: int = 70
    artist_blacklist: str = ""
    temperature: float = 0.9
    created_at: str
    updated_at: str


class StationPlayRequest(BaseModel):
    # If true, clear the current queue and start the station fresh.
    reset: bool = True


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
    yt_browse_id: str = ""
    mb_recording_id: str = ""
    mb_artist_id: str = ""
    created_at: str


class LikedTracksResponse(BaseModel):
    items: list[LikedTrackResponse]


class DislikeToggleRequest(LikeToggleRequest):
    """Same payload shape as LikeToggleRequest."""


class DislikeResponse(BaseModel):
    disliked: bool
