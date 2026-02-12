from __future__ import annotations

import asyncio
import math
import re
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query
from starlette.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..db import get_db
from ..models import User
from ..settings_store import get_settings
from ..cache import TTLCache
from ..integrations.musicbrainz import MusicBrainzClient
from ..integrations.wikipedia import parse_wikipedia_url, fetch_wikipedia_thumbnail, search_wikipedia_title
from ..image_proxy import fetch_cached_image


router = APIRouter(prefix="/api", tags=["search"])


_SEARCH_CACHE: TTLCache[Dict[str, Any]] = TTLCache(max_items=4096)
_ARTIST_IMG_CACHE: TTLCache[Optional[str]] = TTLCache(max_items=10000)


def _img_proxy_url(remote_url: str) -> str:
    return f"/api/img?u={urllib.parse.quote(remote_url, safe='')}" if remote_url else ""


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _norm_text(s: str) -> str:
    """Normalize text for intent-oriented matching.

    We want punctuation-insensitive matching so users don't need to type
    apostrophes (e.g., "rifles" should match "rifle's").
    """
    s = (s or "").lower().strip()
    # Normalize common apostrophe characters.
    s = re.sub(r"[’'`´]", "", s)
    # Collapse any remaining non-alphanumerics to spaces.
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _query_variants(q: str) -> List[str]:
    """Generate a small set of query variants to improve recall.

    MusicBrainz search can be sensitive to punctuation/possessives. We keep
    the variant set small to avoid extra latency.
    """
    base = (q or "").strip()
    if not base:
        return []

    variants: List[str] = []

    def add(v: str):
        v = re.sub(r"\s+", " ", (v or "").strip())
        if v and v not in variants:
            variants.append(v)

    add(base)

    # Strip apostrophes entirely.
    add(re.sub(r"[’'`´]", "", base))

    # Replace all punctuation with spaces.
    add(re.sub(r"[^0-9A-Za-z]+", " ", base))

    # Heuristic: convert plural-looking tokens into possessives (rifles -> rifle's).
    toks = re.split(r"\s+", base)
    poss = []
    changed = False
    for t in toks:
        if re.fullmatch(r"[A-Za-z]{4,}s", t) and not t.lower().endswith("ss"):
            poss.append(t[:-1] + "'s")
            changed = True
        else:
            poss.append(t)
    if changed:
        add(" ".join(poss))

    return variants


def _match_score(text: str, q: str) -> float:
    t = _norm_text(text)
    qn = _norm_text(q)
    if not t or not qn:
        return 0.0
    if t == qn:
        return 100.0
    if t.startswith(qn):
        return 70.0
    if qn in t:
        return 45.0
    # token overlap
    tt = set(t.split())
    qq = set(qn.split())
    if not tt or not qq:
        return 0.0
    inter = len(tt & qq)
    if inter == 0:
        return 0.0
    return 10.0 * (inter / max(1, len(qq)))


def cover_url_release_group(rg_id: str, size: int = 250) -> str:
    return f"https://coverartarchive.org/release-group/{rg_id}/front-{int(size)}"


def cover_url_release(rel_id: str, size: int = 250) -> str:
    return f"https://coverartarchive.org/release/{rel_id}/front-{int(size)}"


def _proxy_url(settings: Dict[str, Any], remote_url: str) -> str:
    if not remote_url:
        return ""
    if settings.get("image_proxy_enabled", True):
        return "/api/img?u=" + urllib.parse.quote(remote_url, safe="")
    return remote_url


def _pick_representative_release(releases: List[Dict[str, Any]], settings: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not releases:
        return None

    def_country = str(settings.get("search_default_country", "US") or "US").upper()
    hide_non_official = settings.get("search_hide_non_official", True) is not False
    prefer_original = bool(settings.get("search_prefer_original_release", False))

    candidates = releases
    if hide_non_official:
        candidates = [r for r in candidates if (r.get("status") or "").lower() in ("official", "")]
        if not candidates:
            candidates = releases

    def parse_date(d: str) -> Tuple[int, int, int]:
        if not d:
            return (9999, 12, 31)
        parts = d.split("-")
        try:
            y = int(parts[0])
        except Exception:
            return (9999, 12, 31)
        m = int(parts[1]) if len(parts) > 1 else 12
        day = int(parts[2]) if len(parts) > 2 else 31
        return (y, m, day)

    # Determine common track-count (if provided) to prefer "normal" releases.
    counts: Dict[int, int] = {}
    for r in candidates:
        tc = r.get("track-count")
        if isinstance(tc, int) and tc > 0:
            counts[tc] = counts.get(tc, 0) + 1
    common_tc = max(counts.items(), key=lambda kv: kv[1])[0] if counts else None

    def score(r: Dict[str, Any]) -> float:
        s = 0.0
        country = (r.get("country") or "").upper()
        if country == def_country:
            s += 50.0
        date = r.get("date") or ""
        y, m, d = parse_date(date)
        # prefer earliest if prefer_original else earlier within country subset still matters
        if prefer_original:
            s += max(0.0, 30.0 - (y - 1900) * 0.1)
        else:
            s += max(0.0, 20.0 - (y - 1900) * 0.05)
        if (r.get("status") or "").lower() == "official":
            s += 10.0
        if common_tc is not None and r.get("track-count") == common_tc:
            s += 8.0
        if r.get("id"):
            s += 1.0
        return s

    return max(candidates, key=score)


def _canonical_track_key(title: str, artist: str) -> str:
    t = re.sub(r"\([^\)]*\)", "", title or "")
    t = re.sub(r"\[[^\]]*\]", "", t)
    t = re.sub(r"\s+", " ", t).strip().lower()
    a = re.sub(r"\s+", " ", (artist or "").strip().lower())
    return f"{t}::{a}"



@router.get("/img")
async def img_proxy(
    u: str = Query(..., description="Remote image URL"),
    db: Session = Depends(get_db),
):
    settings = get_settings(db)
    # Public-but-safe image proxy: allowlist remote hosts to avoid open-proxy abuse.
    from urllib.parse import urlparse

    allowed_hosts = {
        "coverartarchive.org",
        "commons.wikimedia.org",
        "upload.wikimedia.org",
    }
    try:
        host = (urlparse(u).hostname or "").lower()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid image URL")
    if host not in allowed_hosts:
        raise HTTPException(status_code=400, detail="Image host not allowed")

    if not settings.get("image_proxy_enabled", True):
        # Let the client fetch directly.
        return RedirectResponse(url=u, status_code=302)

    max_mb = int(settings.get("image_cache_max_mb", 500) or 500)
    ttl_days = int(settings.get("image_cache_ttl_days", 90) or 90)
    try:
        data, ctype = await fetch_cached_image(
            u,
            max_mb=max_mb,
            ttl_days=ttl_days,
            user_agent=str(settings.get("http_user_agent") or settings.get("musicbrainz_user_agent") or "Helix/0.1 (admin@example.invalid)"),
        )
    except Exception as e:
        # If the upstream blocks our server-side fetch (common with Wikimedia), fall back to redirecting the client.
        try:
            import httpx
            if isinstance(e, httpx.HTTPStatusError):
                code = e.response.status_code
                if code in (403, 404):
                    return RedirectResponse(url=u, status_code=302)
        except Exception:
            pass
        raise HTTPException(status_code=400, detail=str(e))
    from fastapi import Response

    return Response(content=data, media_type=ctype, headers={"Cache-Control": "public, max-age=86400"})
