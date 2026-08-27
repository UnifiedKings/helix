from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import time
import urllib.request
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
from .integrations.ytmusic import find_artist_by_name, get_artist_overview, search_ytmusic


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
    cover_key: str = ""


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
            cover_key=str(d.get("cover_key") or ""),
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
                    "cover_key": meta.cover_key,
                },
                f,
            )
    except Exception:
        pass


def _cover_key(seed_artist: str, hint: Optional[Dict[str, Any]]) -> str:
    payload = {"renderer_version": 2, "seed_artist": str(seed_artist or ""), "hint": hint or {}}
    try:
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    except Exception:
        raw = repr(payload)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _clean_artists(values: Any) -> List[str]:
    if isinstance(values, str):
        values = values.replace("\r", "\n").replace(",", "\n").split("\n")
    if not isinstance(values, (list, tuple)):
        return []
    out: List[str] = []
    seen = set()
    for value in values:
        artist = " ".join(str(value or "").strip().split())
        key = artist.lower()
        if not artist or key in seen:
            continue
        seen.add(key)
        out.append(artist)
    return out


async def _first_artist_cover(subsonic: SubsonicClient, artist: str, tile_size: int) -> Tuple[Optional[Image.Image], str]:
    albums = await subsonic.search_albums_by_artist(artist, limit=20)
    for album in albums:
        cover_id = str(album.get("coverArt") or "").strip()
        if not cover_id:
            continue
        data = await subsonic.fetch_cover_art_bytes(cover_id, size=tile_size)
        if not data:
            continue
        try:
            image = Image.open(io.BytesIO(data)).convert("RGB")
            image = _center_crop_square(image).resize((tile_size, tile_size))
            return image, str(album.get("id") or "")
        except Exception:
            continue
    return None, ""


def _norm_text(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _download_image_sync(url: str, size: int) -> Optional[Image.Image]:
    url = str(url or "").strip()
    if not url:
        return None
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 Helix/StationCover",
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            },
        )
        with urllib.request.urlopen(req, timeout=8) as response:
            data = response.read(8 * 1024 * 1024)
        image = Image.open(io.BytesIO(data)).convert("RGB")
        return _center_crop_square(image).resize((size, size))
    except Exception:
        return None


async def _download_image(url: str, size: int) -> Optional[Image.Image]:
    return await asyncio.to_thread(_download_image_sync, url, size)


async def _ytmusic_artist_cover(artist: str, size: int) -> Tuple[Optional[Image.Image], str]:
    """Resolve one representative artist image from YouTube Music."""
    try:
        row = await asyncio.to_thread(find_artist_by_name, artist, artist_limit=8)
    except Exception:
        row = {}
    if not isinstance(row, dict) or not row:
        return None, ""

    url = str(row.get("thumbnail_url") or "").strip()
    browse_id = str(row.get("browse_id") or row.get("artist_id") or "").strip()
    if not url and browse_id:
        try:
            overview = await asyncio.to_thread(get_artist_overview, browse_id)
        except Exception:
            overview = {}
        if isinstance(overview, dict):
            url = str(overview.get("thumbnail_url") or "").strip()

    image = await _download_image(url, size)
    return image, f"ytmusic:artist:{browse_id or _norm_text(artist)}" if image is not None else ""


async def _ytmusic_track_cover(*, title: str, artist: str, album: str, size: int) -> Tuple[Optional[Image.Image], str]:
    """Resolve seed-track artwork from YouTube Music when it is not local."""
    query = " ".join(part for part in (title, artist) if str(part or "").strip())
    if not query:
        return None, ""
    try:
        payload = await asyncio.to_thread(search_ytmusic, query, song_limit=10, album_limit=5)
    except Exception:
        payload = {}

    songs = payload.get("songs") if isinstance(payload, dict) else []
    wanted_title = _norm_text(title)
    wanted_artist = _norm_text(artist)
    wanted_album = _norm_text(album)
    ranked = []
    for row in songs or []:
        if not isinstance(row, dict):
            continue
        score = 0
        if _norm_text(row.get("title")) == wanted_title:
            score += 8
        if wanted_artist and _norm_text(row.get("artist")) == wanted_artist:
            score += 5
        if wanted_album and _norm_text(row.get("album")) == wanted_album:
            score += 3
        ranked.append((score, row))
    ranked.sort(key=lambda pair: pair[0], reverse=True)

    for _score, row in ranked:
        url = str(row.get("thumbnail_url") or "").strip()
        image = await _download_image(url, size)
        if image is not None:
            ident = str(row.get("video_id") or "").strip()
            return image, f"ytmusic:track:{ident or wanted_title}"

    # Album search results are a useful final artwork fallback when the song
    # result itself has no thumbnail.
    for row in (payload.get("albums") or []) if isinstance(payload, dict) else []:
        if not isinstance(row, dict):
            continue
        if wanted_album and _norm_text(row.get("title")) != wanted_album:
            continue
        url = str(row.get("thumbnail_url") or "").strip()
        image = await _download_image(url, size)
        if image is not None:
            ident = str(row.get("browse_id") or "").strip()
            return image, f"ytmusic:album:{ident or wanted_album}"
    return None, ""


async def _ytmusic_artist_album_covers(artist: str, *, limit: int, size: int) -> List[Tuple[Image.Image, str]]:
    """Return several distinct album covers for one artist from YouTube Music."""
    try:
        payload = await asyncio.to_thread(search_ytmusic, artist, song_limit=0, album_limit=max(12, limit * 4))
    except Exception:
        payload = {}
    rows = payload.get("albums") if isinstance(payload, dict) else []
    wanted_artist = _norm_text(artist)
    out: List[Tuple[Image.Image, str]] = []
    seen = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        row_artist = _norm_text(row.get("artist"))
        if wanted_artist and row_artist and row_artist != wanted_artist:
            continue
        key = (_norm_text(row.get("title")), str(row.get("browse_id") or ""))
        if key in seen:
            continue
        seen.add(key)
        image = await _download_image(str(row.get("thumbnail_url") or ""), size)
        if image is None:
            continue
        ident = str(row.get("browse_id") or "").strip()
        out.append((image, f"ytmusic:album:{ident or key[0]}"))
        if len(out) >= limit:
            break
    return out


async def _track_cover(subsonic: SubsonicClient, *, title: str, artist: str, album: str, size: int) -> Tuple[Optional[Image.Image], str]:
    if not title or not artist:
        return None, ""
    try:
        row = await subsonic.search_song_best(title=title, artist=artist, album=album or "")
    except Exception:
        row = None
    if not row:
        return None, ""
    cover_id = str(row.get("coverArt") or "").strip()
    if not cover_id:
        return None, ""
    data = await subsonic.fetch_cover_art_bytes(cover_id, size=size)
    if not data:
        return None, ""
    try:
        image = Image.open(io.BytesIO(data)).convert("RGB")
        return _center_crop_square(image).resize((size, size)), str(row.get("albumId") or row.get("album") or "")
    except Exception:
        return None, ""


async def ensure_station_cover(
    *,
    station_id: str,
    seed_artist: str,
    subsonic: SubsonicClient,
    cover_hint: Optional[Dict[str, Any]] = None,
    size: int = 640,
    tiles: int = 4,
) -> str:
    """Ensure a cached station cover exists and is reasonably fresh.

    User-uploaded covers are handled by the router before this function runs.
    Generated covers may be provider-guided through ``cover_hint``. Providers
    that do not supply a hint continue to use the historical seed-artist collage.
    """
    img_path, meta_path = _cover_paths(station_id)
    meta = _read_meta(meta_path)
    now = _now_ts()
    key = _cover_key(seed_artist, cover_hint)

    if os.path.exists(img_path) and meta and meta.rebuild_after and now < meta.rebuild_after and meta.cover_key == key:
        return img_path

    grid = int(tiles)
    if grid not in (4, 9):
        grid = 4
    cols = 2 if grid == 4 else 3
    rows = cols
    tile_size = size // cols

    hint = dict(cover_hint or {})
    mode = str(hint.get("mode") or "artist").strip().lower()
    album_ids: List[str] = []
    tile_images: List[Image.Image] = []

    # Song Radio and other track-anchored providers can use the seed track art
    # directly rather than an unrelated collage from the artist's albums.
    if mode == "track":
        image, album_id = await _track_cover(
            subsonic,
            title=str(hint.get("title") or "").strip(),
            artist=str(hint.get("artist") or seed_artist or "").strip(),
            album=str(hint.get("album") or "").strip(),
            size=size,
        )
        if image is None:
            image, album_id = await _ytmusic_track_cover(
                title=str(hint.get("title") or "").strip(),
                artist=str(hint.get("artist") or seed_artist or "").strip(),
                album=str(hint.get("album") or "").strip(),
                size=size,
            )
        if image is not None:
            _ensure_dir(os.path.dirname(img_path))
            image.save(img_path, format="JPEG", quality=88, optimize=True)
            _write_meta(meta_path, CoverMeta(
                built_at=now,
                rebuild_after=now + 7 * 24 * 3600,
                album_ids=[album_id] if album_id else [],
                real_tiles=1,
                generated_tiles=0,
                cover_key=key,
            ))
            return img_path
        # If neither local nor YouTube Music seed-track artwork is available,
        # gracefully fall back to the artist collage.
        mode = "artist"

    if mode == "generated":
        artists: List[str] = []
    elif mode == "artists":
        artists = _clean_artists(hint.get("artists"))
    elif mode == "album":
        # Album mode currently resolves through the declared artist. This still
        # gives plugins a stable strategy and degrades cleanly if the album is
        # not yet present in the library.
        artists = _clean_artists([hint.get("artist") or seed_artist])
    else:  # artist + unknown modes
        artists = _clean_artists([hint.get("artist") or seed_artist])

    if artists:
        if mode == "artists":
            for artist in artists[:grid]:
                image, album_id = await _first_artist_cover(subsonic, artist, tile_size)
                if image is None:
                    image, album_id = await _ytmusic_artist_cover(artist, tile_size)
                if image is not None:
                    tile_images.append(image)
                    if album_id:
                        album_ids.append(album_id)
        else:
            # Historical behavior: one artist can contribute several distinct
            # album covers to the collage.
            artist = artists[0]
            albums = await subsonic.search_albums_by_artist(artist, limit=50)
            seen_titles = set()
            for album in albums:
                title = str(album.get("title") or "").strip().lower()
                if title and title in seen_titles:
                    continue
                if title:
                    seen_titles.add(title)
                cover_id = str(album.get("coverArt") or "").strip()
                if not cover_id:
                    continue
                data = await subsonic.fetch_cover_art_bytes(cover_id, size=tile_size)
                if not data:
                    continue
                try:
                    image = Image.open(io.BytesIO(data)).convert("RGB")
                    image = _center_crop_square(image).resize((tile_size, tile_size))
                    tile_images.append(image)
                    album_ids.append(str(album.get("id") or ""))
                except Exception:
                    continue
                if len(tile_images) >= grid:
                    break

            # Fill any missing collage slots from YouTube Music album artwork.
            # This keeps artist-based station covers useful even when the artist
            # is absent or only partially represented in the local library.
            if len(tile_images) < grid:
                yt_rows = await _ytmusic_artist_album_covers(
                    artist,
                    limit=grid - len(tile_images),
                    size=tile_size,
                )
                for image, album_id in yt_rows:
                    tile_images.append(image)
                    if album_id:
                        album_ids.append(album_id)
                    if len(tile_images) >= grid:
                        break

            # If YouTube Music has no usable album artwork either, use the
            # artist's YouTube Music image before resorting to generated tiles.
            if len(tile_images) < grid:
                image, artist_id = await _ytmusic_artist_cover(artist, tile_size)
                if image is not None:
                    tile_images.append(image)
                    if artist_id:
                        album_ids.append(artist_id)

    real_tiles = len(tile_images)
    fallback_seed = str(hint.get("fallback_seed") or seed_artist or hint.get("label") or "Station")
    while len(tile_images) < grid:
        tile_images.append(_make_gradient_tile(tile_size, f"{fallback_seed}:{len(tile_images)}"))

    base = Image.new("RGB", (cols * tile_size, rows * tile_size), (0, 0, 0))
    idx = 0
    for r in range(rows):
        for c in range(cols):
            base.paste(tile_images[idx], (c * tile_size, r * tile_size))
            idx += 1
    base = base.resize((size, size))

    _ensure_dir(os.path.dirname(img_path))
    base.save(img_path, format="JPEG", quality=88, optimize=True)

    rebuild_after = now + (24 * 3600 if real_tiles < grid else 7 * 24 * 3600)
    _write_meta(meta_path, CoverMeta(
        built_at=now,
        rebuild_after=rebuild_after,
        album_ids=[a for a in album_ids if a],
        real_tiles=real_tiles,
        generated_tiles=grid - real_tiles,
        cover_key=key,
    ))
    return img_path
