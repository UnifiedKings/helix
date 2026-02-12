from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional

import httpx


MB_BASE = "https://musicbrainz.org/ws/2"


class MusicBrainzClient:
    """Thin async client with politeness throttling.

    MusicBrainz asks that clients:
      - send a descriptive User-Agent
      - avoid excessive request rates
    """

    def __init__(self, user_agent: str, min_interval_ms: int = 1000, timeout_s: int = 20):
        self._user_agent = user_agent
        self._min_interval = max(0.0, float(min_interval_ms) / 1000.0)
        self._timeout = timeout_s
        self._lock = asyncio.Lock()
        self._last_request_at = 0.0
        self._client = httpx.AsyncClient(timeout=timeout_s, headers={"User-Agent": user_agent, "Accept": "application/json"})

    async def close(self) -> None:
        await self._client.aclose()

    async def _throttle(self) -> None:
        if self._min_interval <= 0:
            return
        async with self._lock:
            now = time.time()
            wait = self._min_interval - (now - self._last_request_at)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request_at = time.time()

    async def get_json(self, path: str, params: Dict[str, str]) -> Dict[str, Any]:
        await self._throttle()
        url = f"{MB_BASE}{path}"
        # Always request JSON
        params = dict(params)
        params["fmt"] = "json"
        r = await self._client.get(url, params=params)
        r.raise_for_status()
        return r.json()

    async def search(self, entity: str, query: str, limit: int = 25, offset: int = 0, inc: Optional[str] = None) -> Dict[str, Any]:
        params = {"query": query, "limit": str(limit), "offset": str(offset)}
        if inc:
            params["inc"] = inc
        return await self.get_json(f"/{entity}", params)

    async def lookup(self, entity: str, mbid: str, inc: Optional[str] = None) -> Dict[str, Any]:
        params: Dict[str, str] = {}
        if inc:
            params["inc"] = inc
        return await self.get_json(f"/{entity}/{mbid}", params)
