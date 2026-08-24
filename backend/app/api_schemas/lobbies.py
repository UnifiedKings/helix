from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


class LobbyPermissions(BaseModel):
    can_add_to_queue: bool = False
    can_remove_own_queue_items: bool = True
    can_remove_any_queue_item: bool = False
    can_control_playback: bool = False
    can_skip: bool = False
    can_seek: bool = False


class LobbyCreateRequest(BaseModel):
    name: str = "Shared Lobby"
    guest_permissions: LobbyPermissions = Field(default_factory=LobbyPermissions)
    guest_queue_limit: int = Field(default=0, ge=0, le=100)
    cleanup_after_days: int = Field(default=0, ge=0, le=365)


class LobbyUpdateRequest(BaseModel):
    name: Optional[str] = None
    is_open: Optional[bool] = None
    guest_permissions: Optional[LobbyPermissions] = None
    guest_queue_limit: Optional[int] = Field(default=None, ge=0, le=100)
    cleanup_after_days: Optional[int] = Field(default=None, ge=0, le=365)


class LobbyJoinRequest(BaseModel):
    invite_code: str
    nickname: str


class LobbyQueueAddRequest(BaseModel):
    title: Optional[str] = None
    artist: Optional[str] = None
    album: Optional[str] = None
    duration_ms: Optional[int] = None
    art_url: Optional[str] = None
    source: Optional[str] = None
    subsonic_song_id: Optional[str] = None
    yt_video_id: Optional[str] = None
    yt_browse_id: Optional[str] = None
    ytmusic_url: Optional[str] = None
    mb_recording_id: Optional[str] = None
    mb_artist_id: Optional[str] = None


class LobbyQueueReorderRequest(BaseModel):
    item_ids: list[str] = []


class LobbySeekRequest(BaseModel):
    position_ms: int = 0


class LobbyMemberUpdateRequest(BaseModel):
    nickname: Optional[str] = None
    is_active: Optional[bool] = None
    permissions: Optional[LobbyPermissions] = None


class LobbySelfUpdateRequest(BaseModel):
    nickname: Optional[str] = None


class LobbyMemberResponse(BaseModel):
    id: str
    nickname: str
    role: str
    is_active: bool
    permissions: LobbyPermissions
    joined_at: str
    last_seen_at: str


class LobbyQueueItemResponse(BaseModel):
    id: str
    position: int
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
    station_id: str = ""
    station_name: str = ""
    added_by_member_id: str = ""
    added_by_nickname: str = ""
    created_at: str


class LobbyHistoryItemResponse(BaseModel):
    id: str
    queue_item_id: str = ""
    title: str
    artist: str
    album: str = ""
    duration_ms: int = 0
    art_url: str = ""
    source: str = ""
    subsonic_song_id: str = ""
    yt_video_id: str = ""
    added_by_member_id: str = ""
    added_by_nickname: str = ""
    played_at: str


class LobbyStateResponse(BaseModel):
    id: str
    name: str
    host_user_id: str
    invite_code: Optional[str] = None
    is_open: bool
    guest_permissions: LobbyPermissions
    guest_queue_limit: int = 0
    cleanup_after_days: int = 0
    active_station_id: str = ""
    active_station_name: str = ""
    self_member_id: str = ""
    self_role: str = "guest"
    self_permissions: LobbyPermissions = Field(default_factory=LobbyPermissions)
    is_playing: bool
    current_index: int
    position_ms: int
    effective_position_ms: int
    server_time_ms: int
    position_updated_at: str
    now_playing: Optional[LobbyQueueItemResponse] = None
    queue: list[LobbyQueueItemResponse] = []
    members: list[LobbyMemberResponse] = []
    history: list[LobbyHistoryItemResponse] = []
    created_at: str
    updated_at: str


class LobbyJoinResponse(BaseModel):
    guest_token: str
    member: LobbyMemberResponse
    lobby: LobbyStateResponse


class LobbyListResponse(BaseModel):
    lobbies: list[LobbyStateResponse] = []


class LobbyOkResponse(BaseModel):
    ok: bool = True
