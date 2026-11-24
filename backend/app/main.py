from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

os.environ.setdefault("HELIX_SUBSONIC_ADD_SKIP_EXISTING_ALBUM_TRACKS", "true")
from .album_duplicate_guard import install_album_duplicate_guard
install_album_duplicate_guard()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response, FileResponse

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
from .routers.playlist_custom_covers import router as playlist_custom_covers_router
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
from .routers.quality_upgrades import router as quality_upgrades_router
from .routers.quality_profiles import router as quality_profiles_router
from .routers.quality_search_limits import router as quality_search_limits_router
from .routers.quality_bad_files import router as quality_bad_files_router
from .routers.quality_history import router as quality_history_router
from .routers.quality_rejected_sources import router as quality_rejected_sources_router
from .routers.quality_library_reconciliation import router as quality_library_reconciliation_router
from .routers.shares import router as shares_router
from .routers.shares_liked_support import router as liked_shares_router, install as install_liked_shares

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
                "default-src 'self'; "
                "img-src 'self' https: data: blob:; "
                "media-src 'self' blob:; "
                "connect-src 'self' ws: wss:; "
                "script-src 'self' 'sha256-ieoeWczDHkReVBsRBqaal5AFMlBtNjMzgwKvLqi/tSU='; "
                "script-src-elem 'self' 'sha256-ieoeWczDHkReVBsRBqaal5AFMlBtNjMzgwKvLqi/tSU='; "
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                "font-src 'self' https://fonts.gstatic.com data:; "
                "base-uri 'self'; "
                "frame-ancestors 'none'",
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
    from .quality_continuous_upgrades import install_safe_revert
    install_safe_revert()
    from .quality_library_reconciliation import initialize_import_intents, install_import_tracking
    initialize_import_intents()
    install_import_tracking()
    from .quality_bad_files import QualityBadFile
    from .quality_source_registry import initialize_rejections
    initialize_rejections()
    from .db import Base, engine
    Base.metadata.create_all(bind=engine, tables=[QualityBadFile.__table__])
    from sqlalchemy import text
    with engine.begin() as conn:
        conn.execute(text("""CREATE TRIGGER IF NOT EXISTS quality_bad_files_job_cleanup
            AFTER DELETE ON quality_upgrade_jobs
            BEGIN DELETE FROM quality_bad_files WHERE job_id = OLD.id; END"""))
    from .quality_search_budget import initialize_search_budget
    initialize_search_budget()
    # Phase 1: additive profile schema and legacy-settings snapshot. The
    # upgrade worker intentionally continues using its existing policy.
    from .quality_profile_store import initialize_profiles
    db = SessionLocal()
    try:
        initialize_profiles(db)
    finally:
        db.close()
    from .quality_library_reconciliation import periodic_reconciliation
    asyncio.get_event_loop().create_task(periodic_reconciliation())
    from .realtime import HUB
    try:
        HUB.bind_loop()
    except RuntimeError:
        pass
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
        logging.getLogger(__name__).exception("Failed to start lobby station monitor")
    try:
        from .quality_search_worker import quality_upgrade_worker_loop
        asyncio.get_event_loop().create_task(quality_upgrade_worker_loop())
    except Exception:
        logging.getLogger(__name__).exception("Failed to start quality upgrade worker")

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
app.include_router(playlist_custom_covers_router)
app.include_router(playlists_router)
app.include_router(subsonic_router)
app.include_router(subsonic_add_router)
app.include_router(quality_upgrades_router)
app.include_router(quality_profiles_router)
app.include_router(quality_search_limits_router)
app.include_router(quality_bad_files_router)
app.include_router(quality_history_router)
app.include_router(quality_rejected_sources_router)
app.include_router(quality_library_reconciliation_router)
app.include_router(realtime_router)
app.include_router(user_settings_router)
app.include_router(lyrics_router)
install_liked_shares()
app.include_router(liked_shares_router)
app.include_router(shares_router)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


class SinglePageAppStaticFiles(StaticFiles):
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
