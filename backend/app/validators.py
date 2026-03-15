from __future__ import annotations

import re
from typing import Optional


# YouTube video IDs are 11 chars of URL-safe base64-ish characters.
_YT_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def normalize_yt_video_id(value: Optional[str]) -> str:
    """Normalize a provided YouTube video id.

    Returns a stripped string (may be empty).
    """
    return (value or "").strip()


def is_valid_yt_video_id(value: Optional[str]) -> bool:
    v = normalize_yt_video_id(value)
    if not v:
        return False
    return bool(_YT_VIDEO_ID_RE.match(v))


def require_valid_yt_video_id(value: Optional[str]) -> str:
    """Return a validated YT video id or raise ValueError."""
    v = normalize_yt_video_id(value)
    if not v:
        raise ValueError("yt_video_id is required")
    if not _YT_VIDEO_ID_RE.match(v):
        raise ValueError("Invalid yt_video_id")
    return v
