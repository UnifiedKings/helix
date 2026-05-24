from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .db import SessionLocal, db_watchdog_loop, init_db
from .routers.admin import router as admin_router
from .routers.album import router as album_router
from .routers.art import router as art_router
from .routers.auth import router as auth_router
from .routers.dislikes import router as dislikes_router
from .routers.likes import router as likes_router
from .routers.playback import router as playback_router
from .routers.queue import router as queue_router
from .routers.playback_history import router as playback_history_router
from .routers.streaming import router as streaming_router
from .routers.fulfillment import router as fulfillment_router
from .routers.playlists import router as playlists_router
from .routers.search import router as search_router
from .routers.settings import router as settings_router
from .routers.stations import router as stations_router
from .routers.subsonic import router as subsonic_router
from .routers.subsonic_add import router as subsonic_add_router
from .routers.system import router as system_router
from .routers.ytmusic import router as ytmusic_router

logging.basicConfig(
    level=getattr(logging, os.getenv("HELIX_LOG_LEVEL", "INFO").upper(), logging.INFO),
    stream=sys.stdout,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    force=True,
)

app = FastAPI(title="Helix Backend", version="0.1.0")

FRONTEND_ORIGIN = os.getenv("MR_FRONTEND_ORIGIN", "http://localhost:8080")

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

    # Detect long-held SQLite connections while the app is running.
    try:
        asyncio.get_event_loop().create_task(db_watchdog_loop())
    except Exception:
        logging.getLogger(__name__).exception("Failed to start db watchdog")

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


# API routers are grouped by OpenAPI domain for easier docs navigation.
app.include_router(system_router)
app.include_router(auth_router)
app.include_router(settings_router)
app.include_router(admin_router)
app.include_router(search_router)
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
app.include_router(dislikes_router)
app.include_router(playlists_router)
app.include_router(subsonic_router)
app.include_router(subsonic_add_router)


# --- Serve frontend (single-container mode) ---
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
