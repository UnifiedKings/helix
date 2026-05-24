from __future__ import annotations

from fastapi import APIRouter

from ..player import engine as player_engine

router = APIRouter(prefix="/api/stream", tags=["streaming"])

router.add_api_route("/{queue_item_id}", player_engine.stream_item, methods=["GET"])
