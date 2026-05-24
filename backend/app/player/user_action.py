"""Explicit user-initiated playback actions.

This module names the boundary for actions the user directly triggers. The current
implementation still delegates to engine.py so behavior remains identical while the
player subsystem is being decomposed.
"""

from .engine import (
    play_track,
    play_album,
    play_playlist,
    jump_to,
    replay_from_history,
    next_track,
    prev_track,
    pause,
    resume,
    queue_append_track,
    queue_append_album,
    queue_remove_item,
    set_autoplay,
)
