from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote, urlparse, unquote
import re

import httpx


@dataclass
class WikipediaRef:
    lang: str
    title: str


_WIKI_RE = re.compile(r"^https?://([a-z-]+)\.wikipedia\.org/wiki/(.+)$", re.IGNORECASE)


def parse_wikipedia_url(url: str) -> Optional[WikipediaRef]:
    """Parse a Wikipedia article URL into (lang, title).

    Supports URLs like:
      - https://en.wikipedia.org/wiki/The_Shins
      - https://en.wikipedia.org/wiki/The_Shins#History
    """
    if not url:
        return None
    m = _WIKI_RE.match(url)
    if not m:
        # Try a more generic parse as a fallback
        try:
            u = urlparse(url)
            host = (u.hostname or "").lower()
            if not host.endswith("wikipedia.org"):
                return None
            lang = host.split(".")[0]
            path = u.path or ""
            if not path.startswith("/wiki/"):
                return None
            title = path[len("/wiki/") :]
            title = title.split("#", 1)[0]
            title = unquote(title)
            return WikipediaRef(lang=lang or "en", title=title)
        except Exception:
            return None

    lang = (m.group(1) or "en").lower()
    title = m.group(2) or ""
    title = title.split("#", 1)[0]
    title = unquote(title)
    if not title:
        return None
    return WikipediaRef(lang=lang, title=title)


async def fetch_wikipedia_thumbnail(
    ref: WikipediaRef,
    user_agent: str,
    timeout_s: int = 12,
) -> Optional[str]:
    """Fetch a thumbnail image URL for a Wikipedia page.

    Uses the REST summary endpoint. Returns an image URL (typically on upload.wikimedia.org)
    or None if no thumbnail/original image is available.
    """
    if not ref or not ref.title:
        return None

    lang = ref.lang or "en"
    # REST endpoint expects the title URL-encoded
    title_enc = quote(ref.title.replace(" ", "_"), safe="")
    url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title_enc}"
    headers = {
        "Accept": "application/json",
        "User-Agent": user_agent or "Helix/0.1 (admin@example.invalid)",
    }
    try:
        async with httpx.AsyncClient(timeout=timeout_s, headers=headers) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return None
            data = r.json() or {}
    except Exception:
        return None

    thumb = (data.get("thumbnail") or {}).get("source")
    if isinstance(thumb, str) and thumb:
        return thumb
    orig = (data.get("originalimage") or {}).get("source")
    if isinstance(orig, str) and orig:
        return orig
    return None


async def search_wikipedia_article_url(
    query: str,
    user_agent: str,
    lang: str = "en",
    limit: int = 5,
    timeout_s: int = 12,
) -> Optional[str]:
    """Search Wikipedia by title and return a best-guess article URL.

    This is a fallback for cases where MusicBrainz does not provide a wikipedia
    URL relationship for an artist.
    """
    if not query:
        return None
    q = query.strip()
    if not q:
        return None

    lang = (lang or "en").lower()
    url = f"https://{lang}.wikipedia.org/w/rest.php/v1/search/title"
    headers = {
        "Accept": "application/json",
        "User-Agent": user_agent or "Helix/0.1 (admin@example.invalid)",
    }
    params = {"q": q, "limit": str(max(1, min(int(limit), 10)))}

    try:
        async with httpx.AsyncClient(timeout=timeout_s, headers=headers) as client:
            r = await client.get(url, params=params)
            if r.status_code != 200:
                return None
            data = r.json() or {}
    except Exception:
        return None

    pages = data.get("pages") or []
    if not pages:
        return None

    # Prefer an exact title match (case-insensitive), otherwise take the first result.
    target = q.lower()
    chosen = pages[0]
    for p in pages:
        t = (p.get("title") or "").strip()
        if t and t.lower() == target:
            chosen = p
            break

    title = (chosen.get("title") or "").strip()
    if not title:
        return None
    title_enc = quote(title.replace(" ", "_"), safe="")
    return f"https://{lang}.wikipedia.org/wiki/{title_enc}"


async def search_wikipedia_title(
    query: str,
    user_agent: str,
    lang: str = "en",
    limit: int = 5,
    timeout_s: int = 12,
) -> Optional[WikipediaRef]:
    """Search Wikipedia for a page title and return a WikipediaRef.

    This is a fallback when MusicBrainz doesn't provide a wikipedia URL relation.
    Uses the REST search endpoint and picks the best match.
    """
    q = (query or "").strip()
    if not q:
        return None

    # Wikipedia REST search endpoint
    url = f"https://{lang}.wikipedia.org/w/rest.php/v1/search/title"
    headers = {
        "Accept": "application/json",
        "User-Agent": user_agent or "Helix/0.1 (admin@example.invalid)",
    }
    params = {"q": q, "limit": str(max(1, min(int(limit), 20)))}
    try:
        async with httpx.AsyncClient(timeout=timeout_s, headers=headers) as client:
            r = await client.get(url, params=params)
            if r.status_code != 200:
                return None
            data = r.json() or {}
    except Exception:
        return None

    pages = data.get("pages") or []
    if not pages:
        return None

    # Prefer exact title match (case-insensitive) when available.
    q_norm = q.lower()
    best_title = None
    for p in pages:
        t = p.get("title")
        if isinstance(t, str) and t and t.lower() == q_norm:
            best_title = t
            break
    if best_title is None:
        t0 = pages[0].get("title")
        if isinstance(t0, str) and t0:
            best_title = t0
    if not best_title:
        return None
    return WikipediaRef(lang=lang, title=best_title)
