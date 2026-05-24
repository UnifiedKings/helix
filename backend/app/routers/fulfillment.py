from __future__ import annotations

from fastapi import APIRouter

from ..player import engine as player_engine

router = APIRouter(prefix="/api/fulfillment", tags=["fulfillment"])

router.add_api_route("/{queue_item_id}/request", player_engine.request_fulfillment, methods=["POST"])
