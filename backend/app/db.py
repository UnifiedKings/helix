from __future__ import annotations

import os
import time
import threading
import traceback
import logging
from sqlalchemy import text
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, DeclarativeBase, Session

DB_PATH = os.getenv("MR_DB_PATH", "data/app.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

logger = logging.getLogger(__name__)

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
    pool_timeout=float(os.getenv("HELIX_DB_POOL_TIMEOUT", "30")),
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

class Base(DeclarativeBase):
    pass

def init_db() -> None:
    from . import models  # noqa: F401
    from . import lobby_models  # noqa: F401
    Base.metadata.create_all(bind=engine)

    # Lightweight forward migrations for SQLite. (create_all does not alter existing tables.)
    with engine.begin() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(stations)")).fetchall()}
        if "popular_track_pool_size" not in cols:
            conn.execute(text("ALTER TABLE stations ADD COLUMN popular_track_pool_size INTEGER NOT NULL DEFAULT 10"))

        lobby_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(shared_lobbies)")).fetchall()}
        if lobby_cols and "guest_queue_limit" not in lobby_cols:
            conn.execute(text("ALTER TABLE shared_lobbies ADD COLUMN guest_queue_limit INTEGER NOT NULL DEFAULT 0"))
        if lobby_cols and "cleanup_after_days" not in lobby_cols:
            conn.execute(text("ALTER TABLE shared_lobbies ADD COLUMN cleanup_after_days INTEGER NOT NULL DEFAULT 0"))
        if lobby_cols and "last_history_queue_item_id" not in lobby_cols:
            conn.execute(text("ALTER TABLE shared_lobbies ADD COLUMN last_history_queue_item_id VARCHAR(36) NOT NULL DEFAULT ''"))
        if lobby_cols and "active_station_id" not in lobby_cols:
            conn.execute(text("ALTER TABLE shared_lobbies ADD COLUMN active_station_id VARCHAR(36) NOT NULL DEFAULT ''"))
        if lobby_cols and "password_hash" not in lobby_cols:
            conn.execute(text("ALTER TABLE shared_lobbies ADD COLUMN password_hash TEXT NOT NULL DEFAULT ''"))

        # Human-friendly lobby codes are always five uppercase letters. Existing
        # lobbies from older builds used long URL-safe invite tokens; migrate
        # those in place while preserving the lobby/member records themselves.
        if lobby_cols:
            import secrets
            import string
            rows = conn.execute(text("SELECT id, invite_code FROM shared_lobbies")).fetchall()
            used = {str(row[1] or "").upper() for row in rows if len(str(row[1] or "")) == 5 and str(row[1] or "").isalpha()}
            alphabet = string.ascii_uppercase
            for lobby_id, invite_code in rows:
                current = str(invite_code or "")
                if len(current) == 5 and current.isalpha() and current == current.upper():
                    continue
                for _ in range(100):
                    code = "".join(secrets.choice(alphabet) for _ in range(5))
                    if code not in used:
                        used.add(code)
                        conn.execute(text("UPDATE shared_lobbies SET invite_code = :code WHERE id = :id"), {"code": code, "id": lobby_id})
                        break
                else:
                    raise RuntimeError("Could not generate unique five-letter lobby code during migration")

        lobby_queue_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(shared_lobby_queue_items)")).fetchall()}
        if lobby_queue_cols and "station_id" not in lobby_queue_cols:
            conn.execute(text("ALTER TABLE shared_lobby_queue_items ADD COLUMN station_id VARCHAR(36) NOT NULL DEFAULT ''"))
        if lobby_queue_cols and "station_name" not in lobby_queue_cols:
            conn.execute(text("ALTER TABLE shared_lobby_queue_items ADD COLUMN station_name TEXT NOT NULL DEFAULT ''"))


        # --- playlists.system_key uniqueness migration ---
        # Older schema used system_key='' for user-created playlists with a UNIQUE(user_id, system_key) constraint,
        # which prevents creating more than one user playlist. New schema uses system_key NULL for user-created.
        try:
            pinfo = conn.execute(text("PRAGMA table_info(playlists)")).fetchall()
            if pinfo:
                # row format: (cid, name, type, notnull, dflt_value, pk)
                sys_row = next((r for r in pinfo if r[1] == "system_key"), None)
                if sys_row is not None:
                    notnull = int(sys_row[3] or 0)
                    dflt = (sys_row[4] or "").strip()  # often "''"
                    needs_migration = (notnull == 1)  # old schema had NOT NULL
                    if needs_migration:
                        logger.info("Migrating playlists.system_key to allow multiple user playlists...")
                        conn.execute(text("ALTER TABLE playlists RENAME TO playlists_old"))
                        conn.execute(text(
                            "CREATE TABLE playlists ("
                            "id VARCHAR(36) PRIMARY KEY, "
                            "user_id VARCHAR(36) NOT NULL, "
                            "name TEXT NOT NULL, "
                            "system_key VARCHAR(32) NULL, "
                            "created_at DATETIME NOT NULL, "
                            "updated_at DATETIME NOT NULL"
                            ")"
                        ))
                        conn.execute(text(
                            "INSERT INTO playlists (id, user_id, name, system_key, created_at, updated_at) "
                            "SELECT id, user_id, name, NULLIF(system_key, ''), created_at, updated_at "
                            "FROM playlists_old"
                        ))
                        conn.execute(text("DROP TABLE playlists_old"))
                        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_playlists_user_id ON playlists(user_id)"))
                        # enforce uniqueness only for system playlists (non-NULL system_key)
                        conn.execute(text(
                            "CREATE UNIQUE INDEX IF NOT EXISTS uq_playlist_user_system "
                            "ON playlists(user_id, system_key) WHERE system_key IS NOT NULL"
                        ))
        except Exception:
            logger.exception("Playlist schema migration failed")

        # --- queue_items(session_user_id, position) uniqueness ---
        # Station prefetch can schedule concurrently; enforce a single item per slot.
        try:
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_queue_items_session_pos "
                "ON queue_items(session_user_id, position)"
            ))
        except Exception:
            logger.exception("Queue item position uniqueness migration failed")




def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --- DB watchdog: detect long-held pooled connections (sessions held too long) ---

_DB_WATCH_WARN_S = float(os.getenv("HELIX_DB_WATCHDOG_WARN_S", "2"))
_DB_WATCH_ERROR_S = float(os.getenv("HELIX_DB_WATCHDOG_ERROR_S", "10"))
_DB_WATCH_INTERVAL_S = float(os.getenv("HELIX_DB_WATCHDOG_INTERVAL_S", "2"))

# Keyed by SQLAlchemy ConnectionRecord objects; values hold checkout time + stack.
_CHECKOUTS: dict[object, dict[str, object]] = {}
_CHECKOUTS_LOCK = threading.Lock()
_WATCHDOG_STARTED = False


def _stack(skip: int = 0) -> str:
    # Stack at checkout time is the most useful thing when diagnosing leaks.
    stk = traceback.format_stack()
    if skip:
        stk = stk[:-skip]
    return "".join(stk)


def _register_pool_watchdog() -> None:
    # QueuePool emits checkout/checkin events. If you switch poolclass, these may not fire.
    # This is best-effort instrumentation; it is safe to run even if events don't fire.
    try:
        @event.listens_for(engine.pool, "checkout")
        def _on_checkout(dbapi_con, con_record, con_proxy):  # type: ignore[no-redef]
            now = time.monotonic()
            with _CHECKOUTS_LOCK:
                _CHECKOUTS[con_record] = {
                    "t": now,
                    "stack": _stack(skip=1),
                }

        @event.listens_for(engine.pool, "checkin")
        def _on_checkin(dbapi_con, con_record):  # type: ignore[no-redef]
            now = time.monotonic()
            rec = None
            with _CHECKOUTS_LOCK:
                rec = _CHECKOUTS.pop(con_record, None)

            if not rec:
                return

            held_s = now - float(rec.get("t") or now)
            if held_s >= _DB_WATCH_WARN_S:
                logger.warning(
                    "[db-watchdog] connection was held %.3fs (warn>=%.1fs). Checkout stack:\n%s",
                    held_s,
                    _DB_WATCH_WARN_S,
                    rec.get("stack") or "",
                )
    except Exception as e:
        logger.exception("[db-watchdog] failed to register pool listeners: %s", e)


_register_pool_watchdog()


async def db_watchdog_loop() -> None:
    """Periodically logs any *currently checked-out* DB connections held too long.

    This catches cases where a request/task is stuck and never returns (so checkin never happens),
    which is exactly the failure mode you saw in production.
    """
    global _WATCHDOG_STARTED
    if _WATCHDOG_STARTED:
        return
    _WATCHDOG_STARTED = True

    import asyncio

    while True:
        try:
            now = time.monotonic()
            offenders: list[tuple[float, str]] = []
            with _CHECKOUTS_LOCK:
                for rec in _CHECKOUTS.values():
                    t0 = float(rec.get("t") or now)
                    held = now - t0
                    if held >= _DB_WATCH_ERROR_S:
                        offenders.append((held, str(rec.get("stack") or "")))

            for held, stack in sorted(offenders, key=lambda x: -x[0])[:5]:
                logger.error(
                    "[db-watchdog] connection currently held %.3fs (error>=%.1fs). Checkout stack:\n%s",
                    held,
                    _DB_WATCH_ERROR_S,
                    stack,
                )
        except Exception:
            logger.exception("[db-watchdog] watchdog loop error")

        await asyncio.sleep(_DB_WATCH_INTERVAL_S)
