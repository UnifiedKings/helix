from __future__ import annotations

from datetime import datetime, timedelta
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from ..auth import SESSION_COOKIE, session_max_age_seconds
from ..db import SessionLocal
from ..models import SessionToken, User
from ..lobby_models import SharedLobby, SharedLobbyMember
from ..realtime import HUB, broadcast_player_state, broadcast_lobby_state
from ..subsonic_permissions import can_import_to_subsonic

router = APIRouter(tags=["realtime"])

def _websocket_user(ws: WebSocket) -> User | None:
    token = ws.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    db = SessionLocal()
    try:
        sess = db.execute(select(SessionToken).where(SessionToken.token == token)).scalar_one_or_none()
        if not sess or (sess.created_at and sess.created_at < datetime.utcnow() - timedelta(seconds=session_max_age_seconds())):
            return None
        user = db.get(User, sess.user_id)
        if not user or not user.is_active:
            return None
        db.expunge(user)
        return user
    finally:
        db.close()

@router.websocket("/ws/player")
async def player_socket(ws: WebSocket):
    user = _websocket_user(ws)
    if not user:
        await ws.close(code=4401)
        return
    await ws.accept()
    await HUB.register_player(user.id, ws)
    await broadcast_player_state(user.id)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        HUB.unregister_player(user.id, ws)

@router.websocket("/ws/quality-upgrades")
async def quality_upgrades_socket(ws: WebSocket):
    user = _websocket_user(ws)
    if not user:
        await ws.close(code=4401)
        return

    db = SessionLocal()
    try:
        db_user = db.get(User, user.id)
        if not db_user or not can_import_to_subsonic(db, db_user):
            await ws.close(code=4403)
            return
    finally:
        db.close()

    await ws.accept()
    await HUB.register_quality_upgrades(ws)
    try:
        # The initial HTTP load supplies the current list. This socket only
        # carries invalidation events, so no periodic client traffic is needed.
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        HUB.unregister_quality_upgrades(ws)

@router.websocket("/ws/lobbies/{lobby_id}")
async def lobby_socket(ws: WebSocket, lobby_id: str):
    user = _websocket_user(ws)
    guest_token = (ws.query_params.get("token") or "").strip()
    db = SessionLocal()
    try:
        lobby = db.get(SharedLobby, lobby_id)
        if not lobby:
            await ws.close(code=4404)
            return
        member = None
        if user:
            member = db.execute(select(SharedLobbyMember).where(SharedLobbyMember.lobby_id == lobby_id, SharedLobbyMember.user_id == user.id, SharedLobbyMember.is_active == True)).scalar_one_or_none()
        if member is None and guest_token:
            member = db.execute(select(SharedLobbyMember).where(SharedLobbyMember.lobby_id == lobby_id, SharedLobbyMember.token == guest_token, SharedLobbyMember.is_active == True)).scalar_one_or_none()
        if member is None:
            await ws.close(code=4401)
            return
        member.last_seen_at = datetime.utcnow()
        db.commit()
        member_id = member.id
    finally:
        db.close()
    await ws.accept()
    conn = await HUB.register_lobby(lobby_id, member_id, ws)
    await broadcast_lobby_state(lobby_id)
    try:
        while True:
            await ws.receive_text()
            db = SessionLocal()
            try:
                member = db.get(SharedLobbyMember, member_id)
                if member:
                    member.last_seen_at = datetime.utcnow()
                    db.commit()
            finally:
                db.close()
    except WebSocketDisconnect:
        pass
    finally:
        HUB.unregister_lobby(lobby_id, conn)
