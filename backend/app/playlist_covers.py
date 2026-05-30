from __future__ import annotations

import hashlib
import io
import json
import os
import random
import time
import asyncio
import urllib.request
from urllib.parse import urlparse, unquote
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw

from .integrations.subsonic import SubsonicClient
from .art_sources import is_allowed_art_url

"""Playlist cover generation.

We render playlist covers as a simple grid collage of album art from tracks
contained in the playlist. No text overlays are drawn.

Covers are cached on disk and refreshed periodically so that when Helix later
imports more songs (and thus album art becomes available), the playlist cover
will improve over time.
"""


def _covers_dir() -> str:
    return str(os.getenv("HELIX_PLAYLIST_COVERS_DIR", "/data/playlist_covers")).rstrip("/")


def _ensure_dir(path: str) -> None:
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        pass


def _cover_paths(playlist_id: str) -> Tuple[str, str]:
    d = _covers_dir()
    _ensure_dir(d)
    img_path = os.path.join(d, f"{playlist_id}.jpg")
    meta_path = os.path.join(d, f"{playlist_id}.json")
    return img_path, meta_path


def invalidate_playlist_cover(playlist_id: str) -> None:
    """Delete cached cover + meta so next request regenerates immediately."""
    img_path, meta_path = _cover_paths(playlist_id)
    for p in (img_path, meta_path):
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass


def _now_ts() -> float:
    return time.time()


def _hash_colors(seed: str) -> Tuple[Tuple[int, int, int], Tuple[int, int, int]]:
    h = hashlib.sha256((seed or "").encode("utf-8")).digest()
    c1 = (int(h[0]) // 2, int(h[1]) // 2, int(h[2]) // 2)
    c2 = (int(h[3]) // 2, int(h[4]) // 2, int(h[5]) // 2)
    return c1, c2


def _make_gradient_tile(size: int, seed: str) -> Image.Image:
    c1, c2 = _hash_colors(seed)
    img = Image.new("RGB", (size, size), c1)
    draw = ImageDraw.Draw(img)
    for y in range(size):
        t = y / max(1, size - 1)
        r = int(c1[0] * (1 - t) + c2[0] * t)
        g = int(c1[1] * (1 - t) + c2[1] * t)
        b = int(c1[2] * (1 - t) + c2[2] * t)
        draw.line([(0, y), (size, y)], fill=(r, g, b))
    return img


def _center_crop_square(img: Image.Image) -> Image.Image:
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    return img.crop((left, top, left + side, top + side))


def _fetch_url_bytes(url: str, timeout: float = 10.0) -> bytes:
    """Fetch bytes from an http(s) URL (best-effort)."""
    if not url:
        return b""
    req = urllib.request.Request(url, headers={"User-Agent": "Helix/playlist-covers"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read() or b""
    except Exception:
        return b""


def _internal_subsonic_cover_id(url: str) -> str:
    """Extract the cover id from Helix's internal Subsonic art URL.

    Playlist tracks often store art as /api/art/subsonic/<id>?size=512. The
    cover generator runs server-side and should not HTTP-fetch Helix's own
    authenticated endpoint, so parse the id and use the Subsonic client directly.
    """
    raw = str(url or "").strip()
    if not raw.startswith("/api/art/subsonic/"):
        return ""
    try:
        parsed = urlparse(raw)
        prefix = "/api/art/subsonic/"
        if not parsed.path.startswith(prefix):
            return ""
        return unquote(parsed.path[len(prefix):]).strip()
    except Exception:
        return ""


@dataclass
class CoverMeta:
    built_at: float
    rebuild_after: float
    cover_ids: List[str]
    real_tiles: int
    generated_tiles: int
    sample_version: int = 2


def _read_meta(meta_path: str) -> Optional[CoverMeta]:
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            d = json.load(f) or {}
        return CoverMeta(
            built_at=float(d.get("built_at") or 0),
            rebuild_after=float(d.get("rebuild_after") or 0),
            cover_ids=list(d.get("cover_ids") or []),
            real_tiles=int(d.get("real_tiles") or 0),
            generated_tiles=int(d.get("generated_tiles") or 0),
            sample_version=int(d.get("sample_version") or 0),
        )
    except Exception:
        return None


def _write_meta(meta_path: str, meta: CoverMeta) -> None:
    try:
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "built_at": meta.built_at,
                    "rebuild_after": meta.rebuild_after,
                    "cover_ids": meta.cover_ids,
                    "real_tiles": meta.real_tiles,
                    "generated_tiles": meta.generated_tiles,
                    "sample_version": meta.sample_version,
                },
                f,
            )
    except Exception:
        pass


async def ensure_playlist_cover(
    *,
    playlist_id: str,
    seed: str,
    subsonic: Optional[SubsonicClient],
    # list of track dicts containing at least subsonic_song_id and/or art_url
    tracks: List[Dict[str, Any]],
    size: int = 768,
    tiles: int = 9,
) -> str:
    img_path, meta_path = _cover_paths(playlist_id)
    meta = _read_meta(meta_path)
    now = _now_ts()

    if os.path.exists(img_path) and meta and meta.rebuild_after and now < meta.rebuild_after and meta.sample_version >= 2:
        return img_path

    grid = int(tiles)
    if grid not in (4, 9, 16):
        grid = 9
    cols = 2 if grid == 4 else (3 if grid == 9 else 4)
    rows = cols
    tile_size = size // cols

    # Gather coverArt ids by looking up Subsonic songs, and also collect
    # direct art URLs stored on playlist tracks (e.g., YouTube thumbnails).
    #
    # Do not just use the first N playlist rows. Long playlists otherwise get
    # covers that always represent the first few tracks. Shuffle a bounded copy
    # before collecting artwork so each regeneration samples across the playlist.
    cover_ids: List[str] = []
    art_urls: List[str] = []
    seen = set()

    sampled_tracks = list(tracks or [])[:500]
    random.SystemRandom().shuffle(sampled_tracks)

    for t in sampled_tracks:
        au = str(t.get("art_url") or "").strip()

        # Internal Helix art URLs need special handling. They are authenticated
        # app routes, not public URLs the cover builder should fetch over HTTP.
        internal_cid = _internal_subsonic_cover_id(au)
        if internal_cid and internal_cid not in seen:
            seen.add(internal_cid)
            cover_ids.append(internal_cid)
            if len(cover_ids) >= grid:
                break
            continue

        # Only use known-safe remote art sources.
        if au and is_allowed_art_url(au) and au not in seen:
            seen.add(au)
            art_urls.append(au)

        sid = str(t.get("subsonic_song_id") or "").strip()
        if not sid or subsonic is None:
            continue
        try:
            song = await subsonic.get_song(sid)
        except Exception:
            song = None
        if not song:
            continue
        cid = str(song.get("coverArt") or "").strip()
        if not cid or cid in seen:
            continue
        seen.add(cid)
        cover_ids.append(cid)
        if len(cover_ids) >= grid:
            break

    tile_images: List[Image.Image] = []

    for cid in cover_ids:
        if subsonic is None:
            break
        b = await subsonic.fetch_cover_art_bytes(cid, size=tile_size)
        if not b:
            continue
        try:
            im = Image.open(io.BytesIO(b)).convert("RGB")
            im = _center_crop_square(im).resize((tile_size, tile_size))
            tile_images.append(im)
        except Exception:
            continue
        if len(tile_images) >= grid:
            break

    # If we still don't have enough real tiles, try direct art URLs.
    if len(tile_images) < grid and art_urls:
        for url in art_urls:
            if len(tile_images) >= grid:
                break
            b = await asyncio.to_thread(_fetch_url_bytes, url, 10.0)
            if not b:
                continue
            try:
                im = Image.open(io.BytesIO(b)).convert("RGB")
                im = _center_crop_square(im).resize((tile_size, tile_size))
                tile_images.append(im)
            except Exception:
                continue

    real_tiles = len(tile_images)
    while len(tile_images) < grid:
        tile_images.append(_make_gradient_tile(tile_size, seed))

    base = Image.new("RGB", (cols * tile_size, rows * tile_size), (0, 0, 0))
    idx = 0
    for r in range(rows):
        for c in range(cols):
            base.paste(tile_images[idx], (c * tile_size, r * tile_size))
            idx += 1

    base = base.resize((size, size))
    _ensure_dir(os.path.dirname(img_path))
    base.save(img_path, format="JPEG", quality=88, optimize=True)

    # Rebuild policy:
    # - If we used fewer than 4 real tiles, retry daily.
    # - If we used fewer than full grid, retry every 3 days.
    # - Otherwise, refresh weekly.
    if real_tiles < 4:
        rebuild_after = now + 24 * 3600
    elif real_tiles < grid:
        rebuild_after = now + 3 * 24 * 3600
    else:
        rebuild_after = now + 7 * 24 * 3600

    _write_meta(
        meta_path,
        CoverMeta(
            built_at=now,
            rebuild_after=rebuild_after,
            cover_ids=cover_ids[:grid],
            real_tiles=real_tiles,
            generated_tiles=grid - real_tiles,
            sample_version=2,
        ),
    )

    return img_path
