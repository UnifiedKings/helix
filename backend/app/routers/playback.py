from __future__ import annotations

from fastapi import APIRouter

from ..api_schemas.player import (
    AutoplaySetRequest,
    PlayerActionRequest,
    PlayerJumpRequest,
    PlayerPlayAlbumRequest,
    PlayerPlayPlaylistRequest,
    PlayerPlayTrackRequest,
    PlayerReplayRequest,
    PlayerStateResponse,
)
from ..player import engine as player_engine
from ..services import album_playback

router = APIRouter(prefix="/api/playback", tags=["playback"])

router.add_api_route("/state", player_engine.state, methods=["GET"], response_model=PlayerStateResponse)
router.add_api_route("/autoplay", player_engine.set_autoplay, methods=["POST"], response_model=PlayerStateResponse)
router.add_api_route("/track", player_engine.play_track, methods=["POST"], response_model=PlayerStateResponse)
router.add_api_route("/album", album_playback.play_album, methods=["POST"], response_model=PlayerStateResponse)
router.add_api_route("/playlist", player_engine.play_playlist, methods=["POST"], response_model=PlayerStateResponse)
router.add_api_route("/ended", player_engine.ended, methods=["POST"], response_model=PlayerStateResponse)
router.add_api_route("/jump", player_engine.jump_to, methods=["POST"], response_model=PlayerStateResponse)
router.add_api_route("/replay", player_engine.replay_from_history, methods=["POST"], response_model=PlayerStateResponse)
router.add_api_route("/next", player_engine.next_track, methods=["POST"], response_model=PlayerStateResponse)
router.add_api_route("/previous", player_engine.prev_track, methods=["POST"], response_model=PlayerStateResponse)
router.add_api_route("/pause", player_engine.pause, methods=["POST"], response_model=PlayerStateResponse)
router.add_api_route("/resume", player_engine.resume, methods=["POST"], response_model=PlayerStateResponse)
