from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Optional, Tuple

import httpx


def _cache_dir() -> Path:
    base = os.getenv("HELIX_IMAGE_CACHE_DIR")
    if base:
        return Path(base)
    # default: ./data/image_cache relative to project root
    return Path(__file__).resolve().parent.parent / "data" / "image_cache"


def _key_for_url(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _safe_url(url: str) -> bool:
    return url.startswith("http://") or url.startswith("https://")


def _path_for_key(key: str) -> Path:
    d = _cache_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / key


def _iter_cache_files() -> list[Path]:
    d = _cache_dir()
    if not d.exists():
        return []
    return [p for p in d.iterdir() if p.is_file()]


def _cache_size_bytes() -> int:
    total = 0
    for p in _iter_cache_files():
        try:
            total += p.stat().st_size
        except Exception:
            pass
    return total


def _evict_if_needed(max_bytes: int) -> None:
    if max_bytes <= 0:
        return
    files = _iter_cache_files()
    total = 0
    stats: list[Tuple[float, Path, int]] = []
    for p in files:
        try:
            st = p.stat()
            total += st.st_size
            stats.append((st.st_mtime, p, st.st_size))
        except Exception:
            continue
    if total <= max_bytes:
        return
    # delete oldest first
    stats.sort(key=lambda t: t[0])
    for _, p, sz in stats:
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass
        total -= sz
        if total <= max_bytes:
            break


def _ttl_cleanup(ttl_days: int) -> None:
    if ttl_days <= 0:
        return
    cutoff = time.time() - (ttl_days * 86400)
    for p in _iter_cache_files():
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink(missing_ok=True)
        except Exception:
            continue


async def fetch_cached_image(url: str, *, max_mb: int, ttl_days: int, user_agent: str | None = None) -> Tuple[bytes, str]:
    """Fetch image bytes, caching on disk.

    Returns: (bytes, content_type)
    """
    if not _safe_url(url):
        raise ValueError("Invalid image url")

    max_bytes = int(max_mb) * 1024 * 1024
    _ttl_cleanup(int(ttl_days))
    _evict_if_needed(max_bytes)

    key = _key_for_url(url)
    path = _path_for_key(key)

    if path.exists():
        try:
            data = path.read_bytes()
            # touch for LRU
            os.utime(path, None)
            # content-type is unknown; assume jpeg/png based on header when serving
            return data, _sniff_content_type(data)
        except Exception:
            pass

    async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers={**({"User-Agent": user_agent} if user_agent else {}), "Accept": "image/*"}) as client:
        r = await client.get(url)
        r.raise_for_status()
        data = r.content
        ctype = r.headers.get("content-type") or _sniff_content_type(data)

    try:
        path.write_bytes(data)
    except Exception:
        # if we can't write, still return bytes
        return data, ctype

    _evict_if_needed(max_bytes)
    return data, ctype


def _sniff_content_type(data: bytes) -> str:
    if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if len(data) >= 3 and data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if len(data) >= 6 and (data[:6] in (b"GIF87a", b"GIF89a")):
        return "image/gif"
    return "application/octet-stream"
