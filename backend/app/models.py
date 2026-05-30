from __future__ import annotations

import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="user")  # "admin" | "user"
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    sessions: Mapped[list["SessionToken"]] = relationship(
        "SessionToken",
        back_populates="user",
        cascade="all, delete-orphan",
    )


class SessionToken(Base):
    __tablename__ = "sessions"
    __table_args__ = (UniqueConstraint("token", name="uq_session_token"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    token: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    user: Mapped["User"] = relationship("User", back_populates="sessions")


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)


# --- Playback / Queue (backend-owned) ---

class PlaybackSession(Base):
    __tablename__ = "playback_sessions"

    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    current_index: Mapped[int] = mapped_column(default=0)
    is_playing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Autoplay: when the queue ends, Helix can append a new item and keep playing.
    autoplay_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # If set, autoplay pulls from this station.
    active_station_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    user: Mapped["User"] = relationship("User")
    queue_items: Mapped[list["QueueItem"]] = relationship(
        "QueueItem",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="QueueItem.position",
    )


class QueueItem(Base):
    __tablename__ = "queue_items"
    __table_args__ = (UniqueConstraint("session_user_id", "position", name="uq_queue_items_session_pos"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("playback_sessions.user_id", ondelete="CASCADE"), nullable=False, index=True)

    position: Mapped[int] = mapped_column(nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="song")  # song | albumtrack
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    artist: Mapped[str] = mapped_column(Text, nullable=False, default="")
    album: Mapped[str] = mapped_column(Text, nullable=False, default="")
    duration_ms: Mapped[int] = mapped_column(nullable=False, default=0)
    art_url: Mapped[str] = mapped_column(Text, nullable=False, default="")

    source: Mapped[str] = mapped_column(String(16), nullable=False, default="subsonic")  # subsonic | missing
    subsonic_song_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")

    # YouTube Music identifiers (primary catalog in the current Helix flow)
    yt_video_id: Mapped[str] = mapped_column(Text, nullable=False, default="")
    yt_browse_id: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # Optional MusicBrainz identifiers (used for station discovery + stable de-dupe)
    mb_recording_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    mb_artist_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    # Inbound (ASAP) playback file path when a track is downloaded but not yet imported.
    inbound_path: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # download_status: DOWNLOADING | DOWNLOADED | FINALIZED | (empty)
    download_status: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_playable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    session: Mapped["PlaybackSession"] = relationship("PlaybackSession", back_populates="queue_items")



class ListenHistoryItem(Base):
    __tablename__ = "listen_history"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    station_id: Mapped[str] = mapped_column(String(36), nullable=False, default="", index=True)

    queue_item_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    artist: Mapped[str] = mapped_column(Text, nullable=False, default="")
    album: Mapped[str] = mapped_column(Text, nullable=False, default="")
    duration_ms: Mapped[int] = mapped_column(nullable=False, default=0)
    art_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="subsonic")

    subsonic_song_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    yt_video_id: Mapped[str] = mapped_column(Text, nullable=False, default="")
    yt_browse_id: Mapped[str] = mapped_column(Text, nullable=False, default="")

    mb_recording_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    mb_artist_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    event: Mapped[str] = mapped_column(String(16), nullable=False, default="skipped")  # skipped | completed
    reason: Mapped[str] = mapped_column(String(32), nullable=False, default="")  # next | prev | jump | removed_current | replaced_queue | ended
    played_ms: Mapped[int] = mapped_column(nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    user: Mapped["User"] = relationship("User")


# --- Stations / Likes ---


class Station(Base):
    __tablename__ = "stations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    name: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # Internal provider key used by the station-provider registry. Existing
    # stations default to the original ListenBrainz similar-artist logic.
    station_type: Mapped[str] = mapped_column(String(96), nullable=False, default="listenbrainz_similar_artist")

    # Provider-specific JSON config. The legacy columns below remain for
    # backward compatibility and are mirrored into provider config at runtime.
    config_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    seed_type: Mapped[str] = mapped_column(String(16), nullable=False, default="artist")  # artist|track
    seed_title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    seed_artist: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # Optional MusicBrainz ids used as a semantic anchor for tags/similarity.
    mb_artist_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    mb_recording_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    # --- Station configuration knobs ---
    # discoverability: 0..1 (exposed as 0..100)
    discovery: Mapped[float] = mapped_column(nullable=False, default=0.35)
    # seed influence: 0..1 (exposed as 0..100)
    seed_influence: Mapped[float] = mapped_column(nullable=False, default=0.75)

    # don't repeat artist within X tracks
    artist_cooldown: Mapped[int] = mapped_column(nullable=False, default=5)
    # 0=low, 1=medium, 2=high
    artist_variety: Mapped[int] = mapped_column(nullable=False, default=1)

    # If false, we block alternate versions of the seed track
    allow_seed_alternates: Mapped[int] = mapped_column(nullable=False, default=0)

    # Optional advanced controls
    era_start: Mapped[int] = mapped_column(nullable=False, default=0)  # 0 => any
    era_end: Mapped[int] = mapped_column(nullable=False, default=0)    # 0 => any
    popularity_bias: Mapped[int] = mapped_column(nullable=False, default=50)  # 0..100 (popular..obscure)
    tag_strictness: Mapped[int] = mapped_column(nullable=False, default=70)   # 0..100 (loose..strict)

    # When selecting a track for a chosen artist, randomly sample from the top X most popular
    # tracks for that artist (per ListenBrainz listen counts). 0 disables this behavior.
    popular_track_pool_size: Mapped[int] = mapped_column(nullable=False, default=10)

    # newline/comma separated list of artist names to never play on this station
    artist_blacklist: Mapped[str] = mapped_column(Text, nullable=False, default="")

    temperature: Mapped[float] = mapped_column(nullable=False, default=0.9)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    user: Mapped["User"] = relationship("User")


class StationTag(Base):
    """Cached tag weights for a station (bootstrapped from MusicBrainz, evolves over time)."""

    __tablename__ = "station_tags"
    __table_args__ = (UniqueConstraint("station_id", "tag", name="uq_station_tag"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    station_id: Mapped[str] = mapped_column(String(36), ForeignKey("stations.id", ondelete="CASCADE"), nullable=False, index=True)
    tag: Mapped[str] = mapped_column(Text, nullable=False)
    weight: Mapped[float] = mapped_column(nullable=False, default=1.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)


class LikedTrack(Base):
    __tablename__ = "liked_tracks"
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_liked_user_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # key is a stable identifier for "this song". Prefer subsonic_song_id, else yt_video_id, else fallback.
    key: Mapped[str] = mapped_column(String(160), nullable=False, index=True)

    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    artist: Mapped[str] = mapped_column(Text, nullable=False, default="")
    album: Mapped[str] = mapped_column(Text, nullable=False, default="")
    duration_ms: Mapped[int] = mapped_column(nullable=False, default=0)
    art_url: Mapped[str] = mapped_column(Text, nullable=False, default="")

    source: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    subsonic_song_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    yt_video_id: Mapped[str] = mapped_column(Text, nullable=False, default="")
    yt_browse_id: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # If a liked track was originally backed by Subsonic but that Subsonic item/file
    # later disappears, keep the old Subsonic id for history but mark it stale and
    # persist a recovered YTMusic video id for future playback.
    stale_subsonic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ytmusic_recovered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)

    mb_recording_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    mb_artist_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    mb_match_confidence: Mapped[float] = mapped_column(nullable=False, default=0.0)
    mb_match_type: Mapped[str] = mapped_column(String(16), nullable=False, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    user: Mapped["User"] = relationship("User")


class DislikedTrack(Base):
    __tablename__ = "disliked_tracks"
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_disliked_user_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # key is a stable identifier for "this song". Prefer subsonic_song_id, else yt_video_id, else fallback.
    key: Mapped[str] = mapped_column(String(160), nullable=False, index=True)

    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    artist: Mapped[str] = mapped_column(Text, nullable=False, default="")
    album: Mapped[str] = mapped_column(Text, nullable=False, default="")
    duration_ms: Mapped[int] = mapped_column(nullable=False, default=0)
    art_url: Mapped[str] = mapped_column(Text, nullable=False, default="")

    source: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    subsonic_song_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    yt_video_id: Mapped[str] = mapped_column(Text, nullable=False, default="")
    yt_browse_id: Mapped[str] = mapped_column(Text, nullable=False, default="")

    mb_recording_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    mb_artist_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    mb_match_confidence: Mapped[float] = mapped_column(nullable=False, default=0.0)
    mb_match_type: Mapped[str] = mapped_column(String(16), nullable=False, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    user: Mapped["User"] = relationship("User")


# --- Playlists ---


class Playlist(Base):
    __tablename__ = "playlists"
    __table_args__ = (UniqueConstraint("user_id", "system_key", name="uq_playlist_user_system"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # system_key used for special playlists (e.g., 'liked'). NULL for user-created.
    system_key: Mapped[str | None] = mapped_column(String(32), nullable=True, default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    user: Mapped["User"] = relationship("User")
    tracks: Mapped[list["PlaylistTrack"]] = relationship(
        "PlaylistTrack",
        back_populates="playlist",
        cascade="all, delete-orphan",
        order_by="PlaylistTrack.position",
    )


class PlaylistTrack(Base):
    __tablename__ = "playlist_tracks"
    __table_args__ = (
        UniqueConstraint("playlist_id", "key", name="uq_playlist_track_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    playlist_id: Mapped[str] = mapped_column(String(36), ForeignKey("playlists.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    position: Mapped[int] = mapped_column(nullable=False, default=0)

    # Stable identity: prefer subsonic song id else yt video id else text.
    key: Mapped[str] = mapped_column(String(160), nullable=False, index=True)

    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    artist: Mapped[str] = mapped_column(Text, nullable=False, default="")
    album: Mapped[str] = mapped_column(Text, nullable=False, default="")
    duration_ms: Mapped[int] = mapped_column(nullable=False, default=0)
    art_url: Mapped[str] = mapped_column(Text, nullable=False, default="")

    source: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    subsonic_song_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    yt_video_id: Mapped[str] = mapped_column(Text, nullable=False, default="")
    yt_browse_id: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # If a playlist track was originally backed by Subsonic but that Subsonic
    # item/file later disappears, keep the historical Subsonic id but persist a
    # recovered YTMusic video id for future temporary playback.
    stale_subsonic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ytmusic_recovered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)

    mb_recording_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    mb_artist_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    playlist: Mapped["Playlist"] = relationship("Playlist", back_populates="tracks")
    user: Mapped["User"] = relationship("User")

