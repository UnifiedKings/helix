from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from ..api_schemas.player import (
    PlayerQueueAppendAlbumRequest,
    PlayerQueueAppendTrackRequest,
    PlayerQueueReorderRequest,
    PlayerRemoveQueueItemResponse,
    PlayerStateResponse,
)
from ..auth import get_current_user
from ..db import get_db
from ..models import PlaybackSession, QueueItem, User
from ..player import engine as player_engine
from ..services import album_playback

router = APIRouter(prefix="/api/queue", tags=["queue"])


def queue_reorder_atomic(
    payload: PlayerQueueReorderRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Reorder the queue atomically and verify before releasing SQLite's write lock.

    The old engine handler committed first and then re-read the queue to verify it.
    A rapid remove/append could therefore change the queue after the successful
    commit but before verification, producing a false 500 even though the reorder
    had already persisted.

    Keep BEGIN IMMEDIATE held through flush + verification, then commit only after
    the database matches the intended order.
    """
    requested_ids: list[str] = []
    seen: set[str] = set()

    for raw_id in getattr(payload, "item_ids", []) or []:
        item_id = " ".join(str(raw_id or "").strip().split())
        if not item_id or item_id in seen:
            continue
        requested_ids.append(item_id)
        seen.add(item_id)

    if not requested_ids:
        raise HTTPException(
            status_code=400,
            detail="Reorder payload must include queue item ids",
        )

    try:
        db.rollback()
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")

        rows = db.execute(
            select(QueueItem)
            .where(QueueItem.session_user_id == user.id)
            .order_by(QueueItem.position.asc(), QueueItem.created_at.asc())
        ).scalars().all()

        if not rows:
            db.rollback()
            return player_engine._changed_state(db=db, user=user)

        by_id = {row.id: row for row in rows}
        unknown = [item_id for item_id in requested_ids if item_id not in by_id]
        if unknown:
            db.rollback()
            raise HTTPException(
                status_code=400,
                detail="Reorder payload contains an unknown queue item",
            )

        sess = db.get(PlaybackSession, user.id)
        if sess is None:
            sess = player_engine._get_or_create_session(db, user.id)

        current_index = max(0, min(int(sess.current_index or 0), len(rows) - 1))
        current_row = rows[current_index]

        requested_rows = [by_id[item_id] for item_id in requested_ids]
        requested_row_ids = {row.id for row in requested_rows}

        # Preserve items that were appended after the client captured its drag list.
        ordered_rows = requested_rows + [
            row for row in rows if row.id not in requested_row_ids
        ]

        # QueueItem has UNIQUE(session_user_id, position). Move everything into a
        # collision-free temporary range first, then assign final positions.
        temporary_base = 1_000_000 + len(rows)
        for offset, row in enumerate(rows):
            row.position = temporary_base + offset
            db.add(row)
        db.flush()

        for position, row in enumerate(ordered_rows):
            row.position = position
            db.add(row)

        sess.current_index = next(
            (
                index
                for index, row in enumerate(ordered_rows)
                if row.id == current_row.id
            ),
            current_index,
        )
        db.add(sess)

        # Flush first so verification observes the exact pending transaction while
        # BEGIN IMMEDIATE still prevents another writer from changing the queue.
        db.flush()

        persisted_ids = db.execute(
            select(QueueItem.id)
            .where(QueueItem.session_user_id == user.id)
            .order_by(QueueItem.position.asc(), QueueItem.created_at.asc())
        ).scalars().all()
        expected_ids = [row.id for row in ordered_rows]

        if persisted_ids != expected_ids:
            db.rollback()
            raise HTTPException(
                status_code=500,
                detail="Queue reorder did not persist",
            )

        db.commit()

    except HTTPException:
        raise
    except (IntegrityError, OperationalError) as exc:
        db.rollback()
        if (
            isinstance(exc, OperationalError)
            and "database is locked" in str(exc).lower()
        ):
            raise HTTPException(
                status_code=503,
                detail="Queue is busy. Please try again.",
            ) from exc
        raise

    return player_engine._changed_state(db=db, user=user)


router.add_api_route(
    "/track",
    player_engine.queue_append_track,
    methods=["POST"],
    response_model=PlayerStateResponse,
)
router.add_api_route(
    "/album",
    album_playback.queue_append_album,
    methods=["POST"],
    response_model=PlayerStateResponse,
)
router.add_api_route(
    "/items/clear",
    player_engine.queue_clear,
    methods=["DELETE"],
    response_model=PlayerStateResponse,
)
# Keep the concrete reorder route ahead of /items/{queue_item_id}; otherwise a
# PATCH can be swallowed by the dynamic route and returned as 405.
router.add_api_route(
    "/items/reorder",
    queue_reorder_atomic,
    methods=["PATCH"],
    response_model=PlayerStateResponse,
)
router.add_api_route(
    "/items/{queue_item_id}",
    player_engine.queue_remove_item,
    methods=["DELETE"],
    response_model=PlayerRemoveQueueItemResponse,
)
