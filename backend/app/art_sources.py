from __future__ import annotations

import os
from urllib.parse import urlparse
from typing import Optional


def yt_thumbnail_url(video_id: str) -> str:
    video_id = (video_id or "").strip()
    if not video_id:
        return ""
    # hqdefault is a good balance of quality + ubiquity.
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


def _allowed_hosts() -> set[str]:
    """Comma-separated host allowlist.

    Default allowlist includes common YouTube thumbnail hosts.
    """
    raw = os.getenv(
        "HELIX_ART_URL_ALLOWLIST",
        "i.ytimg.com,lh3.googleusercontent.com",
    )
    hosts = set()
    for h in (raw or "").split(","):
        h = (h or "").strip().lower()
        if h:
            hosts.add(h)
    return hosts


def is_allowed_art_url(url: Optional[str]) -> bool:
    """Return True if the URL is from a known safe art source."""
    u = (url or "").strip()
    if not u:
        return False
    try:
        p = urlparse(u)
    except Exception:
        return False
    if p.scheme not in ("https",):
        return False
    host = (p.hostname or "").lower()
    if not host:
        return False
    return host in _allowed_hosts()
