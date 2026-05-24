from __future__ import annotations

from fastapi import APIRouter

from ..api_schemas.player import PlayerHistoryResponse
from ..player import engine as player_engine

router = APIRouter(prefix="/api/history", tags=["history"])

router.add_api_route("", player_engine.history, methods=["GET"], response_model=PlayerHistoryResponse)
router.add_api_route("/limit", player_engine.history_set_limit, methods=["POST"], response_model=PlayerHistoryResponse)
