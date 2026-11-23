"""Persistent, per-request slskd upgrade search budget.

This module does not alter candidate matching or initial music fulfillment.
Reservations are committed BEFORE requesting a new Soulseek search. Under an
unexpected network error, a reserved request still counts (conservative).
"""
from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

from .db import engine, SessionLocal
from .settings_store import get_settings

DEFAULT_HOURLY = 10
DEFAULT_DAILY = 50
DEFAULT_CONCURRENCY = 1


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(value: datetime) -> str:
    return value.isoformat(timespec="microseconds")


def initialize_search_budget() -> None:
    with engine.begin() as conn:
        conn.execute(text("""CREATE TABLE IF NOT EXISTS quality_search_budget_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            job_id TEXT NOT NULL,
            query_hash TEXT NOT NULL
        )"""))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_quality_search_started ON quality_search_budget_events(started_at)"))


def effective_config(settings: dict[str, Any]) -> dict[str, int]:
    def number(key: str, fallback: int, lo: int, hi: int) -> int:
        try:
            return max(lo, min(hi, int(settings.get(key, fallback))))
        except (ValueError, TypeError):
            return fallback
    hourly = number('quality_search_max_hourly', DEFAULT_HOURLY, 0, 240)
    daily = number('quality_search_max_daily', DEFAULT_DAILY, 0, 2000)
    return {
        'max_hourly': hourly,
        'max_daily': daily,
        'concurrent_searches': number('slskd_concurrent_searches', DEFAULT_CONCURRENCY, 1, 3),
    }


def _inspect(conn: Any, now: datetime, cfg: dict[str, int]) -> dict[str, Any]:
    hour_start = _stamp(now - timedelta(hours=1))
    day_start = _stamp(now - timedelta(days=1))
    rows = conn.execute(text("SELECT started_at FROM quality_search_budget_events WHERE started_at > :cutoff ORDER BY started_at ASC"), {'cutoff': day_start}).scalars().all()
    hour = [r for r in rows if r > hour_start]
    day = rows
    waits: list[float] = []
    if cfg['max_hourly'] and len(hour) >= cfg['max_hourly']:
        waits.append(max(1., (datetime.fromisoformat(hour[len(hour) - cfg['max_hourly']]) + timedelta(hours=1) - now).total_seconds() + .01))
    if cfg['max_daily'] and len(day) >= cfg['max_daily']:
        waits.append(max(1., (datetime.fromisoformat(day[len(day) - cfg['max_daily']]) + timedelta(days=1) - now).total_seconds() + .01))
    return {'used_hourly': len(hour), 'used_daily': len(day), 'wait_seconds': max(waits, default=0.0), **cfg}


def snapshot() -> dict[str, Any]:
    initialize_search_budget()
    db = SessionLocal()
    try:
        cfg = effective_config(get_settings(db))
    finally:
        db.close()
    with engine.connect() as conn:
        return _inspect(conn, _utcnow(), cfg)


def reserve_search(job_id: str, query: str) -> float:
    """Atomic reservation. Returns seconds until budget can be checked again.

    SQLite BEGIN IMMEDIATE serializes concurrent reservations even if more than
    one Helix process is running against the same DB. Do not use the ORM's
    normal transaction here: its deferred locks would allow oversubscription.
    """
    initialize_search_budget()
    db = SessionLocal()
    try:
        cfg = effective_config(get_settings(db))
    finally:
        db.close()
    with engine.connect() as conn:
        conn.exec_driver_sql('BEGIN IMMEDIATE')
        try:
            now = _utcnow()
            state = _inspect(conn, now, cfg)
            if state['wait_seconds'] > 0:
                conn.rollback()
                return float(state['wait_seconds'])
            conn.execute(text("INSERT INTO quality_search_budget_events (started_at, job_id, query_hash) VALUES (:time, :job, :hash)"), {
                'time': _stamp(now), 'job': job_id,
                'hash': hashlib.sha256(query.casefold().strip().encode()).hexdigest(),
            })
            conn.execute(text("DELETE FROM quality_search_budget_events WHERE started_at < :cutoff"), {'cutoff': _stamp(now - timedelta(days=2))})
            conn.commit()
            return 0.0
        except BaseException:
            conn.rollback()
            raise


async def wait_for_search_budget(job_id: str, query: str) -> None:
    """Wait without holding a database connection or asyncio lock."""
    while True:
        wait = await asyncio.to_thread(reserve_search, job_id, query)
        if wait <= 0:
            return
        await asyncio.sleep(min(wait, 30.0))
