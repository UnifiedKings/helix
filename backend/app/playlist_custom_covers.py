"""User-supplied playlist artwork alongside Helix's generated collage cache."""
from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path
from PIL import Image, ImageOps, UnidentifiedImageError


def _directory() -> Path:
    return Path(os.getenv('HELIX_PLAYLIST_COVERS_DIR', '/data/playlist_covers')) / 'custom'


def custom_cover_path(playlist_id: str) -> Path:
    # The caller must first resolve the playlist to the owning user's database row.
    # Canonical UUIDs also avoid unexpected filename characters.
    import uuid
    canonical_id = str(uuid.UUID(playlist_id))
    return _directory() / f'{canonical_id}.jpg'


def has_custom_cover(playlist_id: str) -> bool:
    return custom_cover_path(playlist_id).is_file()


def save_custom_cover(playlist_id: str, data: bytes) -> Path:
    """Bounded upload, decoded by Pillow, normalized to a square JPEG atomically."""
    if not data or len(data) > 10 * 1024 * 1024:
        raise ValueError('Upload must be a nonempty image smaller than 10 MB')
    try:
        with Image.open(io.BytesIO(data)) as source:
            source.verify()
        with Image.open(io.BytesIO(data)) as source:
            if source.format not in {'PNG', 'JPEG', 'WEBP'}:
                raise ValueError('Image must be PNG, JPEG, or WebP')
            if min(source.size) < 128 or source.width * source.height > 40_000_000:
                raise ValueError('Image must be at least 128 pixels on each side and no larger than 40 megapixels')
            img = ImageOps.exif_transpose(source).convert('RGB')
            side = min(img.size)
            x = (img.width - side) // 2
            y = (img.height - side) // 2
            img = img.crop((x, y, x + side, y + side)).resize((1024, 1024), Image.Resampling.LANCZOS)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError('Image could not be decoded') from exc

    dest = custom_cover_path(playlist_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix='.cover-', suffix='.jpg', dir=dest.parent)
    os.close(handle)
    try:
        img.save(temporary, format='JPEG', quality=90, optimize=True)
        os.replace(temporary, dest)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return dest


def remove_custom_cover(playlist_id: str) -> bool:
    path = custom_cover_path(playlist_id)
    if not path.is_file():
        return False
    path.unlink()
    return True
