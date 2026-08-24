from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select

from .db import SessionLocal
from .lobby_models import SharedLobby, SharedLobbyMember

logger = logging.getLogger(__name__)


async def lobby_cleanup_loop() -> None:
    """Delete lobbies whose configured inactivity window has elapsed."""
    while True:
        db = SessionLocal()
        try:
            now = datetime.utcnow()
            lobbies = db.execute(
                select(SharedLobby).where(SharedLobby.cleanup_after_days > 0)
            ).scalars().all()

            deleted = 0
            for lobby in lobbies:
                last_member_seen = db.execute(
                    select(func.max(SharedLobbyMember.last_seen_at)).where(
                        SharedLobbyMember.lobby_id == lobby.id
                    )
                ).scalar_one_or_none()
                last_activity = lobby.updated_at or lobby.created_at or now
                if last_member_seen and last_member_seen > last_activity:
                    last_activity = last_member_seen

                days = max(1, int(lobby.cleanup_after_days or 0))
                if last_activity <= now - timedelta(days=days):
                    logger.info(
                        "Cleaning up inactive lobby id=%s name=%r inactive_days=%s",
                        lobby.id,
                        lobby.name,
                        days,
                    )
                    db.delete(lobby)
                    deleted += 1

            if deleted:
                db.commit()
            else:
                db.rollback()
        except Exception:
            db.rollback()
            logger.exception("Lobby cleanup pass failed")
        finally:
            db.close()

        # Lobby cleanup is intentionally low-frequency housekeeping.
        await asyncio.sleep(60 * 60)
