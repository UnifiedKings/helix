from __future__ import annotations

import os
import secrets
import threading
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import httpx

_CACHE_LOCK = threading.Lock()
_CACHE_EXPIRES_AT = 0.0
_CACHE_RESULTS: list[dict[str, Any]] = []

UNSPLASH_API_URL = "https://api.unsplash.com/search/photos"
UNSPLASH_SITE_URL = "https://unsplash.com/"
UTM_SOURCE = "helix"
UTM_MEDIUM = "referral"


def _enabled() -> bool:
    raw = os.getenv("HELIX_LOGIN_BACKGROUND_UNSPLASH_ENABLED", "false")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _access_key() -> str:
    return (os.getenv("HELIX_UNSPLASH_ACCESS_KEY", "") or "").strip()


def _query() -> str:
    return (
        os.getenv(
            "HELIX_LOGIN_BACKGROUND_UNSPLASH_QUERY",
            "vinyl record player turntable music",
        )
        or "vinyl record player turntable music"
    ).strip()


def _cache_seconds() -> int:
    try:
        return max(60, int(os.getenv("HELIX_LOGIN_BACKGROUND_UNSPLASH_CACHE_SECONDS", "900")))
    except Exception:
        return 900


def _with_utm(url: str) -> str:
    if not url:
        return ""
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["utm_source"] = UTM_SOURCE
    query["utm_medium"] = UTM_MEDIUM
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _sized_image_url(photo: dict[str, Any]) -> str:
    urls = photo.get("urls") or {}
    raw = str(urls.get("raw") or urls.get("full") or urls.get("regular") or "").strip()
    if not raw:
        return ""

    parts = urlsplit(raw)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    # Unsplash's returned image URLs support dynamic resize parameters.
    query.update(
        {
            "auto": "format",
            "fit": "crop",
            "w": "2400",
            "q": "82",
        }
    )
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _fetch_results() -> list[dict[str, Any]]:
    global _CACHE_EXPIRES_AT, _CACHE_RESULTS

    now = time.monotonic()
    with _CACHE_LOCK:
        if _CACHE_RESULTS and now < _CACHE_EXPIRES_AT:
            return list(_CACHE_RESULTS)

    key = _access_key()
    if not key:
        return []

    try:
        with httpx.Client(timeout=10.0, follow_redirects=False) as client:
            response = client.get(
                UNSPLASH_API_URL,
                headers={
                    "Authorization": f"Client-ID {key}",
                    "Accept-Version": "v1",
                    "Accept": "application/json",
                },
                params={
                    "query": _query(),
                    "page": 1,
                    "per_page": 24,
                    "order_by": "relevant",
                    "orientation": "landscape",
                    "content_filter": "high",
                },
            )
            response.raise_for_status()
            payload = response.json()
    except Exception:
        return []

    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        return []

    usable = [
        item for item in results
        if isinstance(item, dict)
        and _sized_image_url(item)
        and isinstance(item.get("user"), dict)
    ]

    with _CACHE_LOCK:
        _CACHE_RESULTS = usable
        _CACHE_EXPIRES_AT = time.monotonic() + _cache_seconds()

    return list(usable)


def random_login_background() -> dict[str, Any]:
    if not _enabled() or not _access_key():
        return {"enabled": False}

    results = _fetch_results()
    if not results:
        return {"enabled": False}

    # Stay within the highest-ranked relevant results while still changing the
    # background from one visit to another.
    pool = results[:12] if len(results) > 12 else results
    photo = secrets.choice(pool)

    user = photo.get("user") or {}
    user_links = user.get("links") or {}
    photo_links = photo.get("links") or {}

    return {
        "enabled": True,
        "image_url": _sized_image_url(photo),
        "photographer_name": str(user.get("name") or user.get("username") or "Unsplash photographer"),
        "photographer_url": _with_utm(str(user_links.get("html") or "")),
        "unsplash_url": _with_utm(UNSPLASH_SITE_URL),
        "photo_url": _with_utm(str(photo_links.get("html") or "")),
    }
