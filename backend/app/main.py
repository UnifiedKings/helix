from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from starlette.responses import FileResponse

from .db import SessionLocal, db_watchdog_loop, init_db
from .routers.admin import router as admin_router
from .routers.album import router as album_router
from .routers.art import router as art_router
from .routers.auth import router as auth_router
from .routers.dislikes import router as dislikes_router
from .routers.likes import router as likes_router
from .routers.lobbies import router as lobbies_router
from .routers.lyrics import router as lyrics_router
from .routers.playback import router as playback_router
from .routers.queue import router as queue_router
from .routers.playback_history import router as playback_history_router
from .routers.streaming import router as streaming_router
from .routers.fulfillment import router as fulfillment_router
from .routers.home import router as home_router
from .routers.playlists import router as playlists_router
from .routers.search import router as search_router
from .routers.settings import router as settings_router
from .routers.stations import router as stations_router
from .routers.subsonic import router as subsonic_router
from .routers.subsonic_add import router as subsonic_add_router
from .routers.system import router as system_router
from .routers.ytmusic import router as ytmusic_router
from .routers.user_settings import router as user_settings_router
from .routers.realtime import router as realtime_router

logging.basicConfig(
    level=getattr(logging, os.getenv("HELIX_LOG_LEVEL", "INFO").upper(), logging.INFO),
    stream=sys.stdout,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    force=True,
)

app = FastAPI(title="Helix Backend", version="0.1.0")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault(
            "Content-Security-Policy",
            os.getenv(
                "HELIX_CONTENT_SECURITY_POLICY",
                "default-src 'self'; img-src 'self' https: data: blob:; media-src 'self' blob:; connect-src 'self' ws: wss:; script-src 'self' 'sha256-ieoeWczDHkReVBsRBqaal5AFMlBtNjMzgwKvLqi/tSU='; script-src-elem 'self' 'sha256-ieoeWczDHkReVBsRBqaal5AFMlBtNjMzgwKvLqi/tSU='; style-src 'self' 'unsafe-inline'; base-uri 'self'; frame-ancestors 'none'",
            ),
        )
        return response


class OriginGuardMiddleware(BaseHTTPMiddleware):
    SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

    async def dispatch(self, request, call_next):
        if request.method.upper() not in self.SAFE_METHODS:
            origin = request.headers.get("origin")
            referer = request.headers.get("referer")
            allowed = {FRONTEND_ORIGIN.rstrip("/")}
            for extra in (os.getenv("HELIX_ALLOWED_ORIGINS", "") or "").split(","):
                extra = extra.strip().rstrip("/")
                if extra:
                    allowed.add(extra)
            request_host = (request.headers.get('host', '') or '').strip()
            if request_host:
                allowed.add(f"http://{request_host}".rstrip("/"))
                allowed.add(f"https://{request_host}".rstrip("/"))
            host_origin = f"{request.url.scheme}://{request_host}".rstrip("/")
            allowed.add(host_origin)
            candidate = (origin or "").rstrip("/")
            if not candidate and referer:
                try:
                    from urllib.parse import urlparse
                    parsed = urlparse(referer)
                    candidate = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
                except Exception:
                    candidate = ""
            if candidate and candidate not in allowed:
                return Response("Forbidden origin", status_code=403)
        return await call_next(request)


FRONTEND_ORIGIN = os.getenv("MR_FRONTEND_ORIGIN", "http://localhost:8080")

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(OriginGuardMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup():
    init_db()

    from .realtime import HUB
    try:
        HUB.bind_loop()
    except RuntimeError:
        pass

    # Detect long-held SQLite connections while the app is running.
    try:
        asyncio.get_event_loop().create_task(db_watchdog_loop())
    except Exception:
        logging.getLogger(__name__).exception("Failed to start db watchdog")

    try:
        from .lobby_cleanup import lobby_cleanup_loop
        asyncio.get_event_loop().create_task(lobby_cleanup_loop())
    except Exception:
        logging.getLogger(__name__).exception("Failed to start lobby cleanup loop")

    try:
        from .lobby_station import lobby_station_monitor_loop
        asyncio.get_event_loop().create_task(lobby_station_monitor_loop())
    except Exception:
        logging.getLogger(__name__).exception("Failed to start lobby station monitor loop")

    # Start background download/finalize workers (YouTube Music fulfillment).
    # The manager still owns the front-of-queue-only download enforcement.
    from .download_manager import DOWNLOAD_MANAGER
    from .settings_store import get_settings

    def _settings_getter():
        db = SessionLocal()
        try:
            return get_settings(db)
        finally:
            db.close()

    DOWNLOAD_MANAGER.set_settings_getter(_settings_getter)
    DOWNLOAD_MANAGER.start()


@app.on_event("startup")
async def _realtime_startup():
    from .realtime import HUB
    HUB.bind_loop()


# API routers are grouped by OpenAPI domain for easier docs navigation.
app.include_router(system_router)
app.include_router(auth_router)
app.include_router(settings_router)
app.include_router(admin_router)
app.include_router(search_router)
app.include_router(home_router)
app.include_router(album_router)
app.include_router(playback_router)
app.include_router(queue_router)
app.include_router(playback_history_router)
app.include_router(streaming_router)
app.include_router(fulfillment_router)
app.include_router(ytmusic_router)
app.include_router(stations_router)
app.include_router(art_router)
app.include_router(likes_router)
app.include_router(lobbies_router)
app.include_router(dislikes_router)
app.include_router(playlists_router)
app.include_router(subsonic_router)
app.include_router(subsonic_add_router)
app.include_router(realtime_router)
app.include_router(user_settings_router)
app.include_router(lyrics_router)


# --- Serve frontend (single-container mode) ---
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


class SinglePageAppStaticFiles(StaticFiles):
    """Serve built frontend assets and fall back to index.html for React routes."""

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except Exception as exc:
            status_code = getattr(exc, "status_code", None)
            if status_code == 404 and not path.startswith("api/"):
                index_file = Path(self.directory) / "index.html"
                if index_file.exists():
                    return FileResponse(index_file)
            raise


if STATIC_DIR.exists():
    app.mount("/", SinglePageAppStaticFiles(directory=str(STATIC_DIR), html=True), name="static")
