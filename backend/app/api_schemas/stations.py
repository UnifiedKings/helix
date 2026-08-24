from __future__ import annotations

from typing import Any, Optional
from pydantic import BaseModel


class StationCreateRequest(BaseModel):
    name: str
    station_type: str = "similar_artist"
    config: dict[str, Any] = {}
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
    popular_track_pool_size: int = 10
    artist_blacklist: str = ""
    temperature: float = 0.9


class StationUpdateRequest(BaseModel):
    name: Optional[str] = None
    station_type: Optional[str] = None
    config: Optional[dict[str, Any]] = None
    discovery: Optional[float] = None
    seed_influence: Optional[float] = None
    artist_cooldown: Optional[int] = None
    artist_variety: Optional[int] = None
    allow_seed_alternates: Optional[bool] = None
    era_start: Optional[int] = None
    era_end: Optional[int] = None
    popularity_bias: Optional[int] = None
    tag_strictness: Optional[int] = None
    popular_track_pool_size: Optional[int] = None
    artist_blacklist: Optional[str] = None


class StationResponse(BaseModel):
    id: str
    name: str
    station_type: str = "similar_artist"
    config: dict[str, Any] = {}
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
    popular_track_pool_size: int = 10
    artist_blacklist: str = ""
    temperature: float = 0.9
    created_at: str
    updated_at: str
    thumbnail_url: str = ""
    cover_url: str = ""
    has_custom_cover: bool = False


class StationPlayRequest(BaseModel):
    # If true, clear the current queue and start the station fresh.
    reset: bool = True


class StationConfigOptionResponse(BaseModel):
    key: str
    label: str
    type: str
    description: str = ""
    required: bool = False
    default: Any = None
    min: Any = None
    max: Any = None
    step: Any = None
    choices: list[dict[str, Any]] = []


class StationProviderResponse(BaseModel):
    station_type: str
    display_name: str
    description: str
    version: str = "1.0.0"
    builtin: bool = False
    config_options: list[StationConfigOptionResponse] = []
