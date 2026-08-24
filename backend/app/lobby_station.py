from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime

from sqlalchemy import select

from .db import SessionLocal
from .lobby_models import SharedLobby, SharedLobbyHistoryItem, SharedLobbyQueueItem
from .models import Station
from .settings_store import get_settings
from .station_providers import get_station_provider
from .station_providers.models import StationContext, StationHistorySnapshot, StationQueueSnapshot
from .stations_engine import StationGenerationError, StationSeedArtistNotFound, _resolve_station_result_to_queue_item, _station_config_from_model, _station_source_mode
from .realtime import HUB, schedule_lobby_state_broadcast

LOG = logging.getLogger(__name__)
_LOCKS: dict[str, asyncio.Lock] = {}


def _target_ahead() -> int:
    try:
        return max(1, min(20, int(os.getenv("HELIX_LOBBY_STATION_AHEAD", "3") or "3")))
    except Exception:
        return 3


def _get_lock(lobby_id: str) -> asyncio.Lock:
    lock = _LOCKS.get(lobby_id)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[lobby_id] = lock
    return lock


def _build_context(lobby_id: str):
    db = SessionLocal()
    try:
        lobby = db.get(SharedLobby, lobby_id)
        station_id = (getattr(lobby, "active_station_id", "") or "").strip() if lobby else ""
        if not lobby or not station_id:
            return None
        station = db.get(Station, station_id)
        if not station or station.user_id != lobby.host_user_id:
            if lobby:
                lobby.active_station_id = ""
                db.commit()
            return None
        history = db.execute(select(SharedLobbyHistoryItem).where(SharedLobbyHistoryItem.lobby_id == lobby_id).order_by(SharedLobbyHistoryItem.played_at.desc()).limit(200)).scalars().all()
        queue = db.execute(select(SharedLobbyQueueItem).where(SharedLobbyQueueItem.lobby_id == lobby_id).order_by(SharedLobbyQueueItem.position.asc()).limit(500)).scalars().all()
        current = max(0, int(lobby.current_index or 0))
        # Lobby queue rows are intentionally retained after playback, but station
        # providers must not treat already-played rows as still queued forever.
        # Past playback belongs in recent_tracks (where each provider can apply
        # its configured history/repeat window).  queued_tracks should represent
        # only the current item and items that are actually still ahead.
        active_queue = queue[current:] if queue else []
        context = StationContext(
            user_id=lobby.host_user_id,
            station_id=station.id,
            station_name=station.name,
            station_type=str(getattr(station, "station_type", "") or "similar_artist"),
            config=_station_config_from_model(station),
            recent_tracks=[StationHistorySnapshot(title=x.title or "", artist=x.artist or "", album=x.album or "", source=x.source or "") for x in history],
            recent_artists=[x.artist or "" for x in history if (x.artist or "").strip()],
            queued_tracks=[StationQueueSnapshot(title=x.title or "", artist=x.artist or "", album=x.album or "", source=x.source or "", position=int(x.position or 0)) for x in active_queue],
        )
        # The station is an auto-fill source for the shared queue, not a
        # separate queue of its own. Any track after the current position
        # (manual or station-generated) counts toward the ahead target.
        tracks_ahead = max(0, len(queue) - current - 1)
        return context, station.name or "Station", dict(get_settings(db) or {}), tracks_ahead, len(queue)
    finally:
        db.close()


async def fill_lobby_station(lobby_id: str, *, start_if_empty: bool = False) -> int:
    async with _get_lock(lobby_id):
        built = _build_context(lobby_id)
        if not built:
            return 0
        context, station_name, settings, tracks_ahead, queue_len = built
        need = max(0, _target_ahead() - tracks_ahead)
        # With an empty queue there is no current track yet. Generate one
        # current track plus the configured number ahead so starting a station
        # on an empty lobby immediately establishes the same invariant.
        if start_if_empty and queue_len == 0:
            need = _target_ahead() + 1
        if need <= 0:
            return 0

        provider = get_station_provider(context.station_type)
        source_mode = _station_source_mode(context.config)
        provider_count = max(need * 12, need + 24)
        try:
            results = await asyncio.wait_for(provider.next_tracks(context, provider_count), timeout=float(os.getenv("HELIX_STATION_PROVIDER_TIMEOUT_S", "20")))
        except ValueError as exc:
            if "mbid" in str(exc).lower() or "seed artist" in str(exc).lower():
                raise StationSeedArtistNotFound(str(exc)) from exc
            raise StationGenerationError(str(exc), status_code=400) from exc
        except asyncio.TimeoutError as exc:
            raise StationGenerationError(f"Station provider timed out: {provider.station_type}", status_code=504) from exc
        if not results:
            raise StationGenerationError(f"Station provider returned no candidates: {provider.station_type}", status_code=503)

        resolved = []
        seen = set()
        for choice in results:
            if not choice or not (choice.title or "").strip() or not (choice.artist or "").strip():
                continue
            key = choice.key()
            if key in seen:
                continue
            seen.add(key)
            qitem = await _resolve_station_result_to_queue_item(choice, settings=settings, source_mode=source_mode)
            if qitem:
                resolved.append(qitem)
            if len(resolved) >= need:
                break
        if not resolved:
            raise StationGenerationError("Station candidates were found, but none resolved to playable tracks.", status_code=503)

        db = SessionLocal()
        try:
            lobby = db.get(SharedLobby, lobby_id)
            if not lobby or (lobby.active_station_id or "") != context.station_id:
                return 0
            rows = db.execute(select(SharedLobbyQueueItem).where(SharedLobbyQueueItem.lobby_id == lobby_id).order_by(SharedLobbyQueueItem.position.asc())).scalars().all()
            max_pos = max((int(x.position or 0) for x in rows), default=-1)
            was_empty = not rows
            stopped_at_end = bool(rows) and not lobby.is_playing and int(lobby.current_index or 0) >= len(rows) - 1
            for offset, q in enumerate(resolved, 1):
                db.add(SharedLobbyQueueItem(
                    lobby_id=lobby_id, added_by_member_id=None, position=max_pos + offset,
                    title=q.title or "", artist=q.artist or "", album=q.album or "", duration_ms=int(q.duration_ms or 0), art_url=q.art_url or "",
                    source=q.source or "", subsonic_song_id=q.subsonic_song_id or "", yt_video_id=q.yt_video_id or "", yt_browse_id=q.yt_browse_id or "",
                    mb_recording_id=q.mb_recording_id or "", mb_artist_id=q.mb_artist_id or "", station_id=context.station_id, station_name=station_name,
                ))
            if was_empty and start_if_empty:
                lobby.current_index = 0
                lobby.position_ms = 0
                lobby.is_playing = True
                lobby.position_updated_at = datetime.utcnow()
            elif stopped_at_end:
                lobby.current_index = len(rows)
                lobby.position_ms = 0
                lobby.is_playing = True
                lobby.position_updated_at = datetime.utcnow()
            lobby.updated_at = datetime.utcnow()
            db.commit()
            schedule_lobby_state_broadcast(lobby_id)
            return len(resolved)
        finally:
            db.close()


def schedule_lobby_station_fill(lobby_id: str) -> None:
    loop = HUB.loop
    if loop is None or loop.is_closed():
        return
    coro = fill_lobby_station(lobby_id)
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is loop:
        loop.create_task(coro)
    else:
        asyncio.run_coroutine_threadsafe(coro, loop)


async def lobby_station_monitor_loop() -> None:
    """Keep the active lobby-station queue-ahead invariant healthy.

    Event-driven refill scheduling remains in place for immediate response, but
    correctness must not depend on every queue/playback mutation remembering to
    schedule a refill.  This low-cost watchdog only inspects lobbies that have
    an active station and tops them up when necessary.
    """
    try:
        interval = max(1.0, min(60.0, float(os.getenv("HELIX_LOBBY_STATION_MONITOR_INTERVAL_S", "3") or "3")))
    except Exception:
        interval = 3.0

    while True:
        db = SessionLocal()
        try:
            lobby_ids = list(db.execute(
                select(SharedLobby.id).where(SharedLobby.active_station_id != "")
            ).scalars().all())
        except Exception:
            LOG.exception("Lobby station monitor could not enumerate active lobbies")
            lobby_ids = []
        finally:
            db.close()

        for lobby_id in lobby_ids:
            try:
                # If the queue has become empty while a station is active, let
                # the station create a new current track plus the ahead buffer.
                db = SessionLocal()
                try:
                    queue_count = db.execute(
                        select(SharedLobbyQueueItem.id).where(SharedLobbyQueueItem.lobby_id == lobby_id)
                    ).scalars().all()
                    is_empty = len(queue_count) == 0
                finally:
                    db.close()
                await fill_lobby_station(lobby_id, start_if_empty=is_empty)
            except StationGenerationError as exc:
                LOG.warning("Lobby station monitor refill failed lobby=%s err=%s", lobby_id, exc)
            except Exception:
                LOG.exception("Lobby station monitor refill crashed lobby=%s", lobby_id)

        await asyncio.sleep(interval)
