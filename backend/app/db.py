from __future__ import annotations

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase, Session

DB_PATH = os.getenv("MR_DB_PATH", "data/app.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

class Base(DeclarativeBase):
    pass

def init_db() -> None:
    from . import models  # noqa: F401
    Base.metadata.create_all(bind=engine)

    # Lightweight schema migration (SQLite).
    # This project doesn't ship Alembic yet, so we do best-effort ALTER TABLE
    # for additive changes.
    try:
        from sqlalchemy import text

        def has_col(table: str, col: str) -> bool:
            with engine.connect() as conn:
                rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
            return any(r[1] == col for r in rows)

        def add_col(table: str, ddl: str) -> None:
            with engine.connect() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {ddl}"))
                conn.commit()

        # queue_items: store YT ids + inbound playback path for download-jobs.
        if not has_col("queue_items", "yt_video_id"):
            add_col("queue_items", "yt_video_id TEXT NOT NULL DEFAULT ''")
        if not has_col("queue_items", "yt_browse_id"):
            add_col("queue_items", "yt_browse_id TEXT NOT NULL DEFAULT ''")
        if not has_col("queue_items", "inbound_path"):
            add_col("queue_items", "inbound_path TEXT NOT NULL DEFAULT ''")
        if not has_col("queue_items", "download_status"):
            add_col("queue_items", "download_status TEXT NOT NULL DEFAULT ''")

        # playback_sessions: autoplay state (per user)
        if not has_col("playback_sessions", "autoplay_enabled"):
            add_col("playback_sessions", "autoplay_enabled INTEGER NOT NULL DEFAULT 1")
        if not has_col("playback_sessions", "active_station_id"):
            add_col("playback_sessions", "active_station_id TEXT NOT NULL DEFAULT ''")

        # stations: per-station configuration knobs
        if not has_col("stations", "seed_influence"):
            add_col("stations", "seed_influence REAL NOT NULL DEFAULT 0.75")
        if not has_col("stations", "artist_cooldown"):
            add_col("stations", "artist_cooldown INTEGER NOT NULL DEFAULT 5")
        if not has_col("stations", "artist_variety"):
            add_col("stations", "artist_variety INTEGER NOT NULL DEFAULT 1")
        if not has_col("stations", "allow_seed_alternates"):
            add_col("stations", "allow_seed_alternates INTEGER NOT NULL DEFAULT 0")
        if not has_col("stations", "era_start"):
            add_col("stations", "era_start INTEGER NOT NULL DEFAULT 0")
        if not has_col("stations", "era_end"):
            add_col("stations", "era_end INTEGER NOT NULL DEFAULT 0")
        if not has_col("stations", "popularity_bias"):
            add_col("stations", "popularity_bias INTEGER NOT NULL DEFAULT 50")
        if not has_col("stations", "tag_strictness"):
            add_col("stations", "tag_strictness INTEGER NOT NULL DEFAULT 70")
        if not has_col("stations", "artist_blacklist"):
            add_col("stations", "artist_blacklist TEXT NOT NULL DEFAULT ''")

    except Exception:
        # If migration fails (permissions/locking), Helix will still run; new
        # features may be degraded until the DB is reset.
        return

def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
