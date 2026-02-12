from __future__ import annotations

import hashlib
import os
import random
import string
from typing import Any, Dict, Optional, Tuple, List

import httpx


def _rand_salt(n: int = 12) -> str:
    return "".join(random.choice(string.ascii_letters + string.digits) for _ in range(n))


def _token(password: str, salt: str) -> str:
    # token auth: md5(password + salt)
    return hashlib.md5((password + salt).encode("utf-8")).hexdigest()


def _norm(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


def _contains_bad_variant(title: str) -> bool:
    t = _norm(title)
    bad = [" live", "(live", " session", "radio", "demo", "acoustic", "remix", "mix", "cover", "karaoke"]
    return any(b in t for b in bad)


class SubsonicClient:
    def __init__(self, base_url: str, username: str, password: str, client_name: str = "Helix", api_version: str = "1.16.1", timeout_s: int = 20):
        self.base_url = (base_url or "").rstrip("/")
        self.username = username
        self.password = password
        self.client_name = client_name
        self.api_version = api_version
        self.timeout = timeout_s
        self._http = httpx.AsyncClient(timeout=timeout_s)

    async def close(self):
        await self._http.aclose()

    def _auth_params(self) -> Dict[str, str]:
        salt = _rand_salt()
        return {
            "u": self.username,
            "t": _token(self.password, salt),
            "s": salt,
            "v": self.api_version,
            "c": self.client_name,
            "f": "json",
        }

    async def search_song_best(self, title: str, artist: str, duration_ms: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Search Subsonic for the best matching song. Returns song dict (Subsonic JSON) or None."""
        q = f'{title} {artist}'.strip()
        url = f"{self.base_url}/rest/search3.view"
        params = {"query": q, **self._auth_params()}
        r = await self._http.get(url, params=params)
        r.raise_for_status()
        data = (r.json() or {}).get("subsonic-response", {})
        res = data.get("searchResult3", {}) or {}
        songs: List[Dict[str, Any]] = res.get("song") or []
        if not songs:
            return None

        # score candidates
        nt = _norm(title)
        na = _norm(artist)
        best = None
        best_score = -1e9

        for s in songs:
            st = _norm(s.get("title") or "")
            sa = _norm(s.get("artist") or "")
            score = 0.0
            if st == nt:
                score += 100
            elif nt in st or st in nt:
                score += 60
            else:
                score += 10

            if sa == na:
                score += 80
            elif na in sa or sa in na:
                score += 40
            else:
                score -= 10

            if _contains_bad_variant(s.get("title") or ""):
                score -= 25

            if duration_ms and s.get("duration"):
                # subsonic duration is seconds
                ds = int(s.get("duration")) * 1000
                diff = abs(ds - int(duration_ms))
                if diff <= 3000:
                    score += 20
                elif diff <= 8000:
                    score += 5
                else:
                    score -= 15

            if score > best_score:
                best_score = score
                best = s

        return best

    def stream_url(self, song_id: str) -> str:
        url = f"{self.base_url}/rest/stream.view"
        # We intentionally do NOT include password; use token auth.
        # Client will fetch through Helix proxy endpoint, so this is mostly for debugging.
        return url + f"?id={httpx.QueryParams({'id': song_id}).get('id')}"

    async def start_scan(self) -> bool:
        """Trigger a media scan (Navidrome supports this through Subsonic API)."""
        url = f"{self.base_url}/rest/startScan.view"
        params = {**self._auth_params()}
        try:
            r = await self._http.get(url, params=params)
            r.raise_for_status()
            return True
        except Exception:
            return False
