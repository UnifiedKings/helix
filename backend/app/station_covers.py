from __future__ import annotations

import hashlib
import io
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError

"""Station cover generation.

We intentionally render covers as a simple grid of album art tiles.

No text overlays are drawn. This keeps station art purely visual and avoids
clutter when used at small sizes.
"""

from .integrations.subsonic import SubsonicClient


def _covers_dir() -> str:
    # Stored on the shared /data volume inside the container.
    return str(os.getenv("HELIX_STATION_COVERS_DIR", "/data/station_covers")).rstrip("/")


def _ensure_dir(path: str) -> None:
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        pass


def _cover_paths(station_id: str) -> Tuple[str, str]:
    d = _covers_dir()
    _ensure_dir(d)
    img_path = os.path.join(d, f"{station_id}.jpg")
    meta_path = os.path.join(d, f"{station_id}.json")
    return img_path, meta_path


def _safe_station_filename(station_id: str) -> str:
    safe = "".join(ch for ch in str(station_id or "") if ch.isalnum() or ch in ("-", "_"))
    return safe or "station"


def _custom_covers_dir() -> str:
    d = os.path.join(_covers_dir(), "custom")
    _ensure_dir(d)
    return d


def custom_station_cover_path(station_id: str) -> str:
    return os.path.join(_custom_covers_dir(), f"{_safe_station_filename(station_id)}.jpg")


def has_custom_station_cover(station_id: str) -> bool:
    return os.path.exists(custom_station_cover_path(station_id))


def delete_custom_station_cover(station_id: str) -> bool:
    path = custom_station_cover_path(station_id)
    if not os.path.exists(path):
        return False
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return False


def delete_generated_station_cover(station_id: str) -> None:
    """Remove the generated fallback cover so a custom upload cannot appear stale."""
    img_path, meta_path = _cover_paths(station_id)
    for path in (img_path, meta_path):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except Exception:
            pass


def save_custom_station_cover(
    station_id: str,
    image_bytes: bytes,
    *,
    output_size: int = 1024,
    minimum_side: int = 128,
    max_bytes: int = 10 * 1024 * 1024,
) -> str:
    """Normalize and save a user-supplied station cover.

    User guidance:
    - Recommended: 1024x1024 square image.
    - Minimum: 512x512 effective square crop.
    - Accepted formats are whatever Pillow can identify from PNG/JPG/WebP uploads.
    - Non-square images are center-cropped.
    """
    if not image_bytes:
        raise ValueError("cover image is required")
    if len(image_bytes) > max_bytes:
        raise ValueError("cover image is too large; maximum upload size is 10 MB")

    try:
        with Image.open(io.BytesIO(image_bytes)) as raw:
            image = ImageOps.exif_transpose(raw).convert("RGB")
    except UnidentifiedImageError as exc:
        raise ValueError("cover image must be a valid PNG, JPG, or WebP image") from exc
    except Exception as exc:
        raise ValueError("cover image could not be processed") from exc

    width, height = image.size
    if min(width, height) < minimum_side:
        raise ValueError(f"cover image must be at least {minimum_side}x{minimum_side} px before cropping")

    try:
        resample = Image.Resampling.LANCZOS
    except AttributeError:  # Pillow < 9
        resample = Image.LANCZOS

    image = _center_crop_square(image).resize((output_size, output_size), resample)
    path = custom_station_cover_path(station_id)
    _ensure_dir(os.path.dirname(path))
    image.save(path, format="JPEG", quality=90, optimize=True)
    return path


def _now_ts() -> float:
    return time.time()


def _hash_colors(seed: str) -> Tuple[Tuple[int, int, int], Tuple[int, int, int]]:
    h = hashlib.sha256((seed or "").encode("utf-8")).digest()
    # Keep colors in a pleasant darker range.
    c1 = (int(h[0]) // 2, int(h[1]) // 2, int(h[2]) // 2)
    c2 = (int(h[3]) // 2, int(h[4]) // 2, int(h[5]) // 2)
    return c1, c2


def _make_gradient_tile(size: int, seed: str) -> Image.Image:
    c1, c2 = _hash_colors(seed)
    img = Image.new("RGB", (size, size), c1)
    draw = ImageDraw.Draw(img)
    # Simple vertical gradient.
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





@dataclass
class CoverMeta:
    built_at: float
    rebuild_after: float
    album_ids: List[str]
    real_tiles: int
    generated_tiles: int


def _read_meta(meta_path: str) -> Optional[CoverMeta]:
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            d = json.load(f) or {}
        return CoverMeta(
            built_at=float(d.get("built_at") or 0),
            rebuild_after=float(d.get("rebuild_after") or 0),
            album_ids=list(d.get("album_ids") or []),
            real_tiles=int(d.get("real_tiles") or 0),
            generated_tiles=int(d.get("generated_tiles") or 0),
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
                    "album_ids": meta.album_ids,
                    "real_tiles": meta.real_tiles,
                    "generated_tiles": meta.generated_tiles,
                },
                f,
            )
    except Exception:
        pass


async def ensure_station_cover(
    *,
    station_id: str,
    seed_artist: str,
    subsonic: SubsonicClient,
    size: int = 640,
    tiles: int = 4,
) -> str:
    """Ensure a cached station cover exists and is reasonably fresh.

    Returns the image file path.
    """
    img_path, meta_path = _cover_paths(station_id)
    meta = _read_meta(meta_path)
    now = _now_ts()

    if os.path.exists(img_path) and meta and meta.rebuild_after and now < meta.rebuild_after:
        return img_path

    # Build a 2x2 collage for now.
    grid = int(tiles)
    if grid not in (4, 9):
        grid = 4
    cols = 2 if grid == 4 else 3
    rows = cols
    tile_size = size // cols

    # Fetch candidate albums.
    albums = await subsonic.search_albums_by_artist(seed_artist, limit=50)
    # Prefer diverse albums.
    picks: List[Dict[str, Any]] = []
    seen_titles = set()
    for a in albums:
        t = str(a.get("title") or "").strip().lower()
        if t and t in seen_titles:
            continue
        if t:
            seen_titles.add(t)
        picks.append(a)
        if len(picks) >= grid:
            break

    tile_images: List[Image.Image] = []
    album_ids: List[str] = []
    for a in picks:
        cover_id = str(a.get("coverArt") or "").strip()
        if not cover_id:
            continue
        b = await subsonic.fetch_cover_art_bytes(cover_id, size=tile_size)
        if not b:
            continue
        try:
            im = Image.open(io.BytesIO(b)).convert("RGB")
            im = _center_crop_square(im).resize((tile_size, tile_size))
            tile_images.append(im)
            album_ids.append(str(a.get("id") or ""))
        except Exception:
            continue

    real_tiles = len(tile_images)
    # Fill remaining tiles with generated gradient tiles.
    while len(tile_images) < grid:
        tile_images.append(_make_gradient_tile(tile_size, seed_artist))

    # Compose grid.
    base = Image.new("RGB", (cols * tile_size, rows * tile_size), (0, 0, 0))
    idx = 0
    for r in range(rows):
        for c in range(cols):
            base.paste(tile_images[idx], (c * tile_size, r * tile_size))
            idx += 1

    # Ensure exact requested size.
    base = base.resize((size, size))

    # Save.
    _ensure_dir(os.path.dirname(img_path))
    base.save(img_path, format="JPEG", quality=88, optimize=True)

    # Rebuild policy:
    # - If we used fewer than 4 real arts, retry daily.
    # - Otherwise, refresh weekly ("regularly" but not urgent).
    if real_tiles < 4:
        rebuild_after = now + 24 * 3600
    else:
        rebuild_after = now + 7 * 24 * 3600

    _write_meta(
        meta_path,
        CoverMeta(
            built_at=now,
            rebuild_after=rebuild_after,
            album_ids=[a for a in album_ids if a],
            real_tiles=real_tiles,
            generated_tiles=grid - real_tiles,
        ),
    )

    return img_path
