from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import DefaultDict

from fastapi import WebSocket

LOG = logging.getLogger(__name__)

@dataclass(eq=False)
class LobbySocket:
    websocket: WebSocket
    member_id: str

class RealtimeHub:
    def __init__(self) -> None:
        self.player: DefaultDict[str, set[WebSocket]] = defaultdict(set)
        self.lobbies: DefaultDict[str, set[LobbySocket]] = defaultdict(set)
        self.quality_upgrades: set[WebSocket] = set()
        self.loop: asyncio.AbstractEventLoop | None = None
        self._seq = 0

    def bind_loop(self) -> None:
        self.loop = asyncio.get_running_loop()

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    async def register_player(self, user_id: str, ws: WebSocket) -> None:
        self.player[user_id].add(ws)

    def unregister_player(self, user_id: str, ws: WebSocket) -> None:
        bucket = self.player.get(user_id)
        if bucket:
            bucket.discard(ws)
            if not bucket:
                self.player.pop(user_id, None)

    async def register_lobby(self, lobby_id: str, member_id: str, ws: WebSocket) -> LobbySocket:
        conn = LobbySocket(ws, member_id)
        self.lobbies[lobby_id].add(conn)
        return conn

    def unregister_lobby(self, lobby_id: str, conn: LobbySocket) -> None:
        bucket = self.lobbies.get(lobby_id)
        if bucket:
            bucket.discard(conn)
            if not bucket:
                self.lobbies.pop(lobby_id, None)

    async def register_quality_upgrades(self, ws: WebSocket) -> None:
        self.quality_upgrades.add(ws)

    def unregister_quality_upgrades(self, ws: WebSocket) -> None:
        self.quality_upgrades.discard(ws)

HUB = RealtimeHub()

async def broadcast_player_state(user_id: str) -> None:
    sockets = list(HUB.player.get(user_id, ()))
    if not sockets:
        return
    from .db import SessionLocal
    from .models import User
    from .player.engine import state
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if not user:
            return
        payload = state(db=db, user=user).model_dump(mode="json")
    finally:
        db.close()
    msg = {"type": "player.state", "seq": HUB.next_seq(), "state": payload}
    for ws in sockets:
        try:
            await ws.send_json(msg)
        except Exception:
            HUB.unregister_player(user_id, ws)

async def broadcast_lobby_state(lobby_id: str) -> None:
    conns = list(HUB.lobbies.get(lobby_id, ()))
    if not conns:
        return
    from .db import SessionLocal
    from .lobby_models import SharedLobby, SharedLobbyMember
    from .routers.lobbies import _to_lobby_state
    for conn in conns:
        db = SessionLocal()
        try:
            lobby = db.get(SharedLobby, lobby_id)
            member = db.get(SharedLobbyMember, conn.member_id)
            if not lobby or not member or member.lobby_id != lobby_id or not member.is_active:
                try:
                    await conn.websocket.close(code=4403)
                except Exception:
                    pass
                HUB.unregister_lobby(lobby_id, conn)
                continue
            payload = _to_lobby_state(db, lobby, member, include_invite=(member.role == "host")).model_dump(mode="json")
        finally:
            db.close()
        try:
            await conn.websocket.send_json({"type": "lobby.state", "seq": HUB.next_seq(), "state": payload})
        except Exception:
            HUB.unregister_lobby(lobby_id, conn)

async def broadcast_quality_upgrades_changed() -> None:
    sockets = list(HUB.quality_upgrades)
    if not sockets:
        return
    msg = {"type": "quality-upgrades.changed", "seq": HUB.next_seq()}
    for ws in sockets:
        try:
            await ws.send_json(msg)
        except Exception:
            HUB.unregister_quality_upgrades(ws)

def _schedule(coro) -> None:
    loop = HUB.loop
    if loop is None or loop.is_closed():
        return
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is loop:
        loop.create_task(coro)
    else:
        asyncio.run_coroutine_threadsafe(coro, loop)

def schedule_player_state_broadcast(user_id: str) -> None:
    _schedule(broadcast_player_state(user_id))

def schedule_lobby_state_broadcast(lobby_id: str) -> None:
    _schedule(broadcast_lobby_state(lobby_id))

def schedule_quality_upgrades_changed() -> None:
    _schedule(broadcast_quality_upgrades_changed())
