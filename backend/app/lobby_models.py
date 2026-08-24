from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class SharedLobby(Base):
    """A shared playback room owned by an authenticated Helix user."""

    __tablename__ = "shared_lobbies"
    __table_args__ = (UniqueConstraint("invite_code", name="uq_shared_lobby_invite_code"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    host_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    invite_code: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    is_open: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Maximum number of pending queue items each guest may have. 0 = unlimited.
    guest_queue_limit: Mapped[int] = mapped_column(nullable=False, default=0)
    # Delete the lobby after this many days of inactivity. 0 = never.
    cleanup_after_days: Mapped[int] = mapped_column(nullable=False, default=0)
    # Queue item most recently written to lobby playback history.
    last_history_queue_item_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    active_station_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")

    # JSON permissions defaults for guest users.
    permissions_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    current_index: Mapped[int] = mapped_column(nullable=False, default=0)
    is_playing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    position_ms: Mapped[int] = mapped_column(nullable=False, default=0)
    position_updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    members: Mapped[list["SharedLobbyMember"]] = relationship(
        "SharedLobbyMember",
        back_populates="lobby",
        cascade="all, delete-orphan",
        order_by="SharedLobbyMember.joined_at",
    )
    history_items: Mapped[list["SharedLobbyHistoryItem"]] = relationship(
        "SharedLobbyHistoryItem",
        back_populates="lobby",
        cascade="all, delete-orphan",
        order_by="SharedLobbyHistoryItem.played_at.desc()",
    )
    queue_items: Mapped[list["SharedLobbyQueueItem"]] = relationship(
        "SharedLobbyQueueItem",
        back_populates="lobby",
        cascade="all, delete-orphan",
        order_by="SharedLobbyQueueItem.position",
    )


class SharedLobbyMember(Base):
    """A host account or guest identity currently known to a lobby."""

    __tablename__ = "shared_lobby_members"
    __table_args__ = (UniqueConstraint("token", name="uq_shared_lobby_member_token"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    lobby_id: Mapped[str] = mapped_column(String(36), ForeignKey("shared_lobbies.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)

    nickname: Mapped[str] = mapped_column(Text, nullable=False, default="")
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="guest")  # host | guest
    token: Mapped[str] = mapped_column(String(128), nullable=True, index=True)
    permissions_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    joined_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    lobby: Mapped[SharedLobby] = relationship("SharedLobby", back_populates="members")


class SharedLobbyQueueItem(Base):
    """A source-neutral queued track inside a shared lobby."""

    __tablename__ = "shared_lobby_queue_items"
    __table_args__ = (UniqueConstraint("lobby_id", "position", name="uq_shared_lobby_queue_pos"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    lobby_id: Mapped[str] = mapped_column(String(36), ForeignKey("shared_lobbies.id", ondelete="CASCADE"), nullable=False, index=True)
    added_by_member_id: Mapped[str] = mapped_column(String(36), ForeignKey("shared_lobby_members.id", ondelete="SET NULL"), nullable=True, index=True)

    position: Mapped[int] = mapped_column(nullable=False, default=0)
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
    station_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    station_name: Mapped[str] = mapped_column(Text, nullable=False, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    lobby: Mapped[SharedLobby] = relationship("SharedLobby", back_populates="queue_items")
    added_by: Mapped[SharedLobbyMember] = relationship("SharedLobbyMember")


class SharedLobbyHistoryItem(Base):
    """Persistent snapshot of a track when it becomes active lobby playback."""

    __tablename__ = "shared_lobby_history_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    lobby_id: Mapped[str] = mapped_column(String(36), ForeignKey("shared_lobbies.id", ondelete="CASCADE"), nullable=False, index=True)
    queue_item_id: Mapped[str] = mapped_column(String(36), ForeignKey("shared_lobby_queue_items.id", ondelete="SET NULL"), nullable=True, index=True)
    added_by_member_id: Mapped[str] = mapped_column(String(36), ForeignKey("shared_lobby_members.id", ondelete="SET NULL"), nullable=True, index=True)
    added_by_nickname: Mapped[str] = mapped_column(Text, nullable=False, default="")

    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    artist: Mapped[str] = mapped_column(Text, nullable=False, default="")
    album: Mapped[str] = mapped_column(Text, nullable=False, default="")
    duration_ms: Mapped[int] = mapped_column(nullable=False, default=0)
    art_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    subsonic_song_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    yt_video_id: Mapped[str] = mapped_column(Text, nullable=False, default="")

    played_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    lobby: Mapped[SharedLobby] = relationship("SharedLobby", back_populates="history_items")
