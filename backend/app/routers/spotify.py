from __future__ import annotations

import asyncio
from urllib.parse import urlencode
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..db import get_db
from ..models import User
from ..integrations.spotify import (
    SpotifyAuthRequired,
    SpotifyConfigError,
    build_authorize_url,
    clear_connection,
    connection_status,
    consume_oauth_state,
    create_oauth_state,
    exchange_code,
    list_user_playlists,
    save_connection,
    spotify_configured,
    spotify_redirect_uri,
)

router = APIRouter(tags=["spotify"])

OAUTH_DONE_PATH = "/spotify-oauth.html"


def _spotify_error(exc: Exception, fallback: str) -> HTTPException:
    if isinstance(exc, SpotifyAuthRequired):
        return HTTPException(status_code=401, detail=str(exc))
    if isinstance(exc, SpotifyConfigError):
        return HTTPException(status_code=503, detail="Spotify OAuth is not configured on this Helix server.")
    return HTTPException(status_code=502, detail=f"{fallback}: {exc}")


def _require_spotify_configured() -> None:
    if not spotify_configured():
        raise HTTPException(status_code=503, detail="Spotify OAuth is not configured on this Helix server.")


@router.get("/api/spotify/status")
def spotify_status(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    status = connection_status(db, user.id)
    status["configured"] = spotify_configured()
    return status


@router.post("/api/spotify/auth/start")
def spotify_auth_start(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_spotify_configured()
    redirect_uri = spotify_redirect_uri(request)
    state = create_oauth_state(db, user.id, redirect_uri)
    return {"oauth_url": build_authorize_url(state, redirect_uri)}


@router.get("/spotify/auth/callback")
async def spotify_auth_callback(request: Request, code: str = "", state: str = "", error: str = "", db: Session = Depends(get_db)):
    """Spotify redirects the OAuth popup here after the user approves.

    The one-time code is exchanged server-side (the client secret never reaches
    the browser), the per-user connection is stored, and the popup is bounced to
    a small same-origin page that notifies the opener and closes itself.
    """
    def done(params: Dict[str, str]) -> RedirectResponse:
        query = urlencode(params)
        target = f"{OAUTH_DONE_PATH}?{query}" if query else OAUTH_DONE_PATH
        return RedirectResponse(target, status_code=303)

    if error:
        return done({"status": "error", "error": "Spotify denied access"})

    pending = consume_oauth_state(db, state)
    if pending is None:
        return done({"status": "error", "error": "This authorization link is invalid or has expired."})
    if not code:
        return done({"status": "error", "error": "Spotify did not return an authorization code."})

    # Token exchange is network I/O; keep it off the event loop.
    try:
        tokens = await asyncio.to_thread(exchange_code, code, pending["redirect_uri"])
    except RuntimeError as exc:
        return done({"status": "error", "error": str(exc)[:200]})

    user_id = pending["user_id"]

    # FastAPI's get_db dependency may not have run for this branch, so open a
    # short-lived session for the DB writes inside the thread.
    def _finish() -> str:
        from ..db import SessionLocal
        from ..integrations.spotify import fetch_profile
        with SessionLocal() as session:
            display_name = ""
            try:
                display_name = fetch_profile(tokens["access_token"])
            except Exception:
                display_name = ""
            save_connection(
                session,
                user_id,
                access_token=tokens["access_token"],
                refresh_token=tokens.get("refresh_token") or "",
                expires_at=tokens["expires_at"],
                display_name=display_name,
            )
            return display_name

    try:
        display_name = await asyncio.to_thread(_finish)
    except Exception:
        display_name = ""

    params = {"status": "ok"}
    if display_name:
        params["display_name"] = display_name
    return done(params)


@router.delete("/api/spotify/auth")
def spotify_disconnect(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    cleared = clear_connection(db, user.id)
    return {"connected": False, "cleared": cleared}


@router.get("/api/spotify/playlists")
async def spotify_playlists(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_spotify_configured()
    try:
        return await asyncio.to_thread(list_user_playlists, db, user.id)
    except Exception as exc:
        raise _spotify_error(exc, "Could not fetch Spotify playlists")