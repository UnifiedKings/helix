from __future__ import annotations

from fastapi import APIRouter

from ..api_schemas.player import (
    PlayerQueueAppendAlbumRequest,
    PlayerQueueAppendTrackRequest,
    PlayerQueueReorderRequest,
    PlayerRemoveQueueItemResponse,
    PlayerStateResponse,
)
from ..player import engine as player_engine

router = APIRouter(prefix="/api/queue", tags=["queue"])

router.add_api_route("/track", player_engine.queue_append_track, methods=["POST"], response_model=PlayerStateResponse)
router.add_api_route("/album", player_engine.queue_append_album, methods=["POST"], response_model=PlayerStateResponse)
router.add_api_route("/items/reorder", player_engine.queue_reorder, methods=["PATCH"], response_model=PlayerStateResponse)
router.add_api_route("/items/{queue_item_id}", player_engine.queue_remove_item, methods=["DELETE"], response_model=PlayerRemoveQueueItemResponse)
