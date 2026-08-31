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
from ..services import album_playback

router = APIRouter(prefix="/api/queue", tags=["queue"])

router.add_api_route("/track", player_engine.queue_append_track, methods=["POST"], response_model=PlayerStateResponse)
router.add_api_route("/album", album_playback.queue_append_album, methods=["POST"], response_model=PlayerStateResponse)
router.add_api_route("/items/clear", player_engine.queue_clear, methods=["DELETE"], response_model=PlayerStateResponse)
# Keep the concrete reorder route ahead of /items/{queue_item_id}; otherwise a
# PATCH can be swallowed by the dynamic route and returned as 405.
router.add_api_route("/items/reorder", player_engine.queue_reorder, methods=["PATCH"], response_model=PlayerStateResponse)
router.add_api_route("/items/{queue_item_id}", player_engine.queue_remove_item, methods=["DELETE"], response_model=PlayerRemoveQueueItemResponse)
