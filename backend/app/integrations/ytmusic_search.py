from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from yt_dlp import YoutubeDL


def _norm(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"\s+", " ", s).strip()
    # strip punctuation (keep hyphens)
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _ytmusic_url(video_id: str) -> str:
    return f"https://music.youtube.com/watch?v={video_id}" if video_id else ""


def _youtube_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}" if video_id else ""


@dataclass
class YTResult:
    found: bool
    confidence: float
    video_id: str = ""
    title: str = ""
    uploader: str = ""
    duration_seconds: Optional[int] = None

    @property
    def youtube_url(self) -> str:
        return _youtube_url(self.video_id)

    @property
    def ytmusic_url(self) -> str:
        return _ytmusic_url(self.video_id)


def _score_track(
    *,
    want_title: str,
    want_artist: str,
    want_duration_s: Optional[int],
    cand_title: str,
    cand_uploader: str,
    cand_duration_s: Optional[int],
) -> float:
    want = _norm(f"{want_artist} {want_title}")
    have = _norm(f"{cand_uploader} {cand_title}")
    wt = set(want.split())
    ht = set(have.split())
    if not wt or not ht:
        base = 0.0
    else:
        base = len(wt & ht) / max(1, len(wt))

    boost = 0.0
    up = (cand_uploader or "").lower()
    if " - topic" in up or "vevo" in up or "official" in up:
        boost += 0.12

    # Duration closeness helps a lot when we have it.
    dur = 0.0
    if want_duration_s and cand_duration_s:
        diff = abs(int(want_duration_s) - int(cand_duration_s))
        if diff <= 2:
            dur += 0.25
        elif diff <= 10:
            dur += 0.12
        elif diff <= 30:
            dur += 0.04
        else:
            dur -= 0.10

    # Penalize common wrong-version indicators.
    penalty = 0.0
    t = (cand_title or "").lower()
    if any(x in t for x in ["live", "concert", "cover", "karaoke", "instrumental", "reaction"]):
        penalty -= 0.12

    return float(base + boost + dur + penalty)


def _search_yt(query: str, *, limit: int = 7) -> List[Dict[str, Any]]:
    """Fast search using yt-dlp's ytsearch.

    Returns flat entries with id/title/uploader/duration.
    """
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": True,
        "noplaylist": True,
    }
    term = f"ytsearch{int(limit)}:{query}"
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(term, download=False)
    entries = info.get("entries") if isinstance(info, dict) else None
    if not entries:
        return []
    out: List[Dict[str, Any]] = []
    for e in entries:
        if isinstance(e, dict) and e.get("id"):
            out.append(e)
    return out


def find_track(
    *,
    title: str,
    artist: str,
    album: Optional[str] = None,
    duration_seconds: Optional[int] = None,
    limit: int = 7,
) -> YTResult:
    # Query format that tends to behave well.
    q = f"{artist} - {title}"
    if album:
        q = f"{q} \"{album}\""

    cands = _search_yt(q, limit=limit)
    if not cands:
        return YTResult(found=False, confidence=0.0)

    scored: List[Tuple[float, Dict[str, Any]]] = []
    for c in cands:
        s = _score_track(
            want_title=title,
            want_artist=artist,
            want_duration_s=duration_seconds,
            cand_title=str(c.get("title") or ""),
            cand_uploader=str(c.get("uploader") or ""),
            cand_duration_s=c.get("duration") if isinstance(c.get("duration"), (int, float)) else None,
        )
        scored.append((s, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    best_s, best = scored[0]

    # Decide if it's "found" based on a conservative threshold.
    found = best_s >= 0.45
    return YTResult(
        found=found,
        confidence=float(best_s),
        video_id=str(best.get("id") or ""),
        title=str(best.get("title") or ""),
        uploader=str(best.get("uploader") or ""),
        duration_seconds=best.get("duration") if isinstance(best.get("duration"), (int, float)) else None,
    )


def find_album(
    *,
    album_title: str,
    artist: str,
    limit: int = 7,
) -> YTResult:
    # Album results on YT Music are often surfaced as videos ("Full Album") or auto-generated uploads.
    q = f"{artist} {album_title} full album"
    cands = _search_yt(q, limit=limit)
    if not cands:
        return YTResult(found=False, confidence=0.0)

    # Reuse track-ish scoring but with album tokens.
    scored: List[Tuple[float, Dict[str, Any]]] = []
    for c in cands:
        s = _score_track(
            want_title=album_title,
            want_artist=artist,
            want_duration_s=None,
            cand_title=str(c.get("title") or ""),
            cand_uploader=str(c.get("uploader") or ""),
            cand_duration_s=c.get("duration") if isinstance(c.get("duration"), (int, float)) else None,
        )
        # Prefer things that explicitly look like album uploads.
        t = (str(c.get("title") or "")).lower()
        if "full album" in t or "album" in t:
            s += 0.08
        scored.append((s, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    best_s, best = scored[0]
    found = best_s >= 0.40
    return YTResult(
        found=found,
        confidence=float(best_s),
        video_id=str(best.get("id") or ""),
        title=str(best.get("title") or ""),
        uploader=str(best.get("uploader") or ""),
        duration_seconds=best.get("duration") if isinstance(best.get("duration"), (int, float)) else None,
    )
