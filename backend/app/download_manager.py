from __future__ import annotations

import asyncio
import os
import re
import subprocess
import time
import shutil
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, Optional, List, Set, Tuple

from .integrations.subsonic import SubsonicClient

try:
    import httpx
except Exception:  # pragma: no cover
    httpx = None  # type: ignore

INBOUND_DIR = os.getenv("HELIX_INBOUND_YT", "/inbound_yt")
BEETS_CONFIG = os.getenv("HELIX_BEETS_CONFIG", "/data/helix/beets/config.yml")
MUSIC_LIBRARY_ROOT = os.getenv("HELIX_MUSIC_LIBRARY_ROOT", "/data/music")
STREAM_CACHE_DIR = os.getenv("HELIX_STREAM_CACHE_DIR", "/data/helix/stream_cache")
STREAM_CACHE_TTL_MIN = int(os.getenv("HELIX_STREAM_CACHE_TTL_MIN", "30"))
STREAM_CACHE_MAX_MB = int(os.getenv("HELIX_STREAM_CACHE_MAX_MB", "1024"))


_FS_BAD = re.compile(r"[\\/:*?\"<>|\x00-\x1f]")


def _safe_name(s: str, fallback: str = "Unknown") -> str:
    s = (s or "").strip()
    if not s:
        return fallback
    s = _FS_BAD.sub("_", s)
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s or fallback


def ensure_dirs() -> None:
    os.makedirs(INBOUND_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(BEETS_CONFIG), exist_ok=True)
    os.makedirs(STREAM_CACHE_DIR, exist_ok=True)


def ensure_beets_config() -> None:
    """Create a Helix-owned beets config if it doesn't exist."""
    ensure_dirs()
    if os.path.exists(BEETS_CONFIG):
        return

    library_db = os.path.join(os.path.dirname(BEETS_CONFIG), "library.db")
    # Helix uses Beets as an organizer/mover. Tagging is done by Helix.
    # We keep autotag OFF so imports never block waiting for a match.
    config = f"""\
# Helix-managed Beets config
# NOTE: Helix may update this file in future versions.

directory: {MUSIC_LIBRARY_ROOT}
library: {library_db}

import:
  move: yes
  copy: no
  write: yes
  resume: no
  quiet: yes
  timid: no
  log: {os.path.join(os.path.dirname(BEETS_CONFIG), 'import.log')}

  autotag: no

# Prefer album-mode matches when importing batches triggered by album playback.
# (Helix will call beet with --group-albums on album batches.)

match:
  strong_rec_thresh: 0.12
  strong_album_thresh: 0.12
  max_rec_dist: 0.25
  max_album_dist: 0.25
  rec_gap_thresh: 0.25
  album_gap_thresh: 0.25

paths:
  # Deterministic: if Helix tags album+albumartist consistently, tracks land together.
  default: $albumartist/$album/$track $title
  # Also store "single" imports under their album folder so albums can fill in over time.
  singleton: $albumartist/$album/$track $title

plugins:
  - fetchart
fetchart:
  cautious: true
  minwidth: 0
"""
    with open(BEETS_CONFIG, "w", encoding="utf-8") as f:
        f.write(config)


@dataclass
class DownloadJob:
    video_id: str
    url: str
    title: str
    artist: str  # track artist
    album: str
    album_artist: str = ""  # album artist (for foldering)
    browse_id: str = ""  # YT Music album browseId (for metadata repair)
    art_url: str = ""
    track_no: int = 0
    duration_ms: int = 0
    priority: int = 10  # lower = higher priority
    created_at: float = 0.0

    def out_prefix(self) -> str:
        return os.path.join(INBOUND_DIR, self.video_id)


class DownloadManager:
    """A simple in-process download queue with batching + finalization hooks."""

    def __init__(self):
        self._q: "asyncio.PriorityQueue[Tuple[int, float, DownloadJob]]" = asyncio.PriorityQueue()
        self._jobs: Dict[str, DownloadJob] = {}
        self._ready: Dict[str, str] = {}  # video_id -> inbound path
        self._active_streams: Set[str] = set()  # video_ids currently being streamed

        self._download_task: Optional[asyncio.Task] = None
        self._finalize_task: Optional[asyncio.Task] = None
        self._cleanup_task: Optional[asyncio.Task] = None

        self._downloaded_since_finalize: List[str] = []
        self._last_finalize_at: float = time.time()
        self._finalize_lock = asyncio.Lock()

        self._settings_getter = None  # set by app startup

        # batching knobs
        self.max_batch_tracks = int(os.getenv("HELIX_FINALIZE_BATCH_TRACKS", "20"))
        self.max_batch_age_s = int(os.getenv("HELIX_FINALIZE_BATCH_AGE_S", str(10 * 60)))

    def start(self) -> None:
        ensure_beets_config()
        if self._download_task is None:
            self._download_task = asyncio.create_task(self._download_worker(), name="helix-download-worker")
        if self._finalize_task is None:
            self._finalize_task = asyncio.create_task(self._finalize_worker(), name="helix-finalize-worker")
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(
                self._stream_cache_cleanup_worker(),
                name="helix-stream-cache-cleanup",
            )

    def set_settings_getter(self, fn) -> None:
        """Provide a callable that returns current settings dict.

        We use this to trigger Navidrome/Subsonic scans after batched imports.
        """
        self._settings_getter = fn

    def mark_streaming(self, video_id: str, is_streaming: bool) -> None:
        if not video_id:
            return
        if is_streaming:
            self._active_streams.add(video_id)
        else:
            self._active_streams.discard(video_id)

    def is_ready(self, video_id: str) -> bool:
        return video_id in self._ready and os.path.exists(self._ready[video_id])

    def ready_path(self, video_id: str) -> str:
        return self._ready.get(video_id, "")

    def ensure_stream_cache(self, video_id: str, src_path: str) -> str:
        """Create/return a stable stream path for ASAP playback.

        We never stream directly from INBOUND_DIR, because Beets may move files
        during finalization. Instead we remux/copy into STREAM_CACHE_DIR and
        stream from there.
        """
        ensure_dirs()
        if not video_id or not src_path:
            return src_path

        # Prefer streaming an Ogg Opus file for consistent browser playback.
        cache_opus = os.path.join(STREAM_CACHE_DIR, f"{video_id}.opus")
        if os.path.exists(cache_opus):
            return cache_opus

        # If source is already .opus, just copy it.
        if src_path.lower().endswith(".opus") and os.path.exists(src_path):
            try:
                shutil.copy2(src_path, cache_opus)
                return cache_opus
            except Exception:
                return src_path

        # Otherwise attempt a fast remux to .opus (no re-encode).
        try:
            cmd_copy = [
                "ffmpeg", "-y", "-i", src_path,
                "-vn",
                "-c:a", "copy",
                cache_opus,
            ]
            r = subprocess.run(cmd_copy, capture_output=True, text=True)
            if r.returncode == 0 and os.path.exists(cache_opus):
                return cache_opus

            # Fallback: re-encode to Opus if copy fails.
            cmd_enc = [
                "ffmpeg", "-y", "-i", src_path,
                "-vn",
                "-c:a", "libopus", "-b:a", "160k",
                cache_opus,
            ]
            r2 = subprocess.run(cmd_enc, capture_output=True, text=True)
            if r2.returncode == 0 and os.path.exists(cache_opus):
                return cache_opus
        except Exception:
            pass

        # Last resort: copy the original file (whatever container it is)
        try:
            ext = os.path.splitext(src_path)[1] or ".bin"
            cache_path = os.path.join(STREAM_CACHE_DIR, f"{video_id}{ext}")
            if not os.path.exists(cache_path):
                shutil.copy2(src_path, cache_path)
            return cache_path
        except Exception:
            return src_path

    def _stream_cache_log(self, msg: str) -> None:
        """Best-effort logging for cache cleanup."""
        try:
            ensure_dirs()
            log_path = os.path.join(STREAM_CACHE_DIR, "cleanup.log")
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{ts}] {msg}\n")
        except Exception:
            pass

    async def _stream_cache_cleanup_worker(self) -> None:
        """Periodically delete old stream-cache files.

        This directory is purely a playback cache. We keep it bounded by:
        - TTL (default 30 minutes)
        - Optional max size (default 1024 MB)

        We also avoid deleting files for video_ids that are currently marked as
        actively streaming.
        """
        ensure_dirs()
        ttl_s = max(1, STREAM_CACHE_TTL_MIN) * 60
        max_bytes = max(1, STREAM_CACHE_MAX_MB) * 1024 * 1024

        while True:
            try:
                now = time.time()
                entries: List[Tuple[str, float, int]] = []  # (path, mtime, size)
                total = 0

                for p in Path(STREAM_CACHE_DIR).glob("*"):
                    if not p.is_file():
                        continue
                    vid = p.name.split(".")[0]
                    if vid in self._active_streams:
                        continue
                    try:
                        st = p.stat()
                        entries.append((str(p), st.st_mtime, int(st.st_size)))
                        total += int(st.st_size)
                    except Exception:
                        continue

                removed = 0

                # TTL cleanup
                for path, mtime, size in list(entries):
                    if now - mtime > ttl_s:
                        try:
                            os.remove(path)
                            removed += 1
                            total -= size
                        except Exception:
                            pass

                # Size-based cleanup (remove oldest first)
                if total > max_bytes:
                    entries.sort(key=lambda t: t[1])
                    for path, mtime, size in entries:
                        if total <= max_bytes:
                            break
                        try:
                            if os.path.exists(path):
                                os.remove(path)
                                removed += 1
                                total -= size
                        except Exception:
                            pass

                if removed:
                    self._stream_cache_log(
                        f"cleanup removed={removed} ttl_min={STREAM_CACHE_TTL_MIN} max_mb={STREAM_CACHE_MAX_MB}"
                    )
            except Exception as e:
                self._stream_cache_log(f"cleanup error: {e!r}")

            await asyncio.sleep(300)

    async def ensure_downloaded(self, job: DownloadJob) -> str:
        """Ensure a given video_id is downloaded. Returns inbound path."""
        if self.is_ready(job.video_id):
            return self.ready_path(job.video_id)

        # enqueue if needed
        if job.video_id not in self._jobs:
            job.created_at = time.time()
            self._jobs[job.video_id] = job
            await self._q.put((job.priority, job.created_at, job))

        # wait until ready
        while True:
            p = self._ready.get(job.video_id)
            if p and os.path.exists(p):
                return p
            await asyncio.sleep(0.25)

    async def enqueue_normal(self, job: DownloadJob) -> None:
        if job.video_id in self._jobs or self.is_ready(job.video_id):
            return
        job.created_at = time.time()
        self._jobs[job.video_id] = job
        await self._q.put((job.priority, job.created_at, job))

    async def _download_worker(self):
        ensure_dirs()
        while True:
            prio, created, job = await self._q.get()
            try:
                # Might have been downloaded while waiting.
                if self.is_ready(job.video_id):
                    continue

                # Download best audio without converting during the ASAP path.
                # We prefer Opus streams, but keep container as provided by YouTube (often .webm).
                # Conversion/remux to .opus (Ogg Opus) happens during finalize_batch.
                outtmpl = job.out_prefix() + ".%(ext)s"
                cmd = [
                    "yt-dlp",
                    "-f",
                    "bestaudio[acodec=opus]/bestaudio",
                    "--no-playlist",
                    "--no-progress",
                    "--newline",
                    "-o",
                    outtmpl,
                    job.url,
                ]

                proc = subprocess.run(cmd, capture_output=True, text=True)
                if proc.returncode != 0:
                    raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "yt-dlp failed")

                # Find produced file: <id>.<ext> (ignore any lingering .part files)
                produced = ""
                prefix = os.path.basename(job.out_prefix()) + "."
                for fn in os.listdir(INBOUND_DIR):
                    if fn.startswith(prefix) and ".part" not in fn:
                        produced = os.path.join(INBOUND_DIR, fn)
                        break
                if not produced:
                    matches = [
                        str(p)
                        for p in Path(INBOUND_DIR).glob(os.path.basename(job.out_prefix()) + ".*")
                        if ".part" not in p.name
                    ]
                    if matches:
                        produced = matches[0]

                if not produced or not os.path.exists(produced):
                    raise RuntimeError("yt-dlp finished but output file not found")

                self._ready[job.video_id] = produced
                self._downloaded_since_finalize.append(job.video_id)
            except Exception as e:
                # Keep job record (for debugging) but don't block worker.
                self._ready.pop(job.video_id, None)
                print(f"[download-worker] FAILED video_id={job.video_id} url={job.url} err={e!r}")
            finally:
                self._q.task_done()

    async def _finalize_worker(self):
        while True:
            try:
                await asyncio.sleep(2)

                # Trigger finalize when idle OR thresholds reached
                idle = self._q.empty()
                age = time.time() - self._last_finalize_at
                count = len(self._downloaded_since_finalize)

                if count == 0:
                    continue

                if idle or count >= self.max_batch_tracks or age >= self.max_batch_age_s:
                    await self.finalize_batch()
            except Exception:
                # never crash worker
                continue

    async def finalize_batch(self):
        async with self._finalize_lock:
            vids = list(dict.fromkeys(self._downloaded_since_finalize))
            if not vids:
                return

            # Only finalize files not actively being streamed.
            vids = [v for v in vids if v not in self._active_streams]
            if not vids:
                return

            # Remux downloaded audio into Ogg Opus (.opus) before tagging/import.
            # We avoid converting during ASAP playback to keep start time fast.
            # yt-dlp typically downloads Opus-in-WebM; we remux (copy) to .opus.
            def _ensure_ogg_opus(src_path: str, out_prefix: str) -> str:
                try:
                    if src_path.lower().endswith('.opus') and os.path.exists(src_path):
                        return src_path

                    dst_path = out_prefix + '.opus'

                    # Fast path: stream copy (no quality loss, very fast)
                    cmd_copy = [
                        'ffmpeg', '-y', '-i', src_path,
                        '-vn',
                        '-c:a', 'copy',
                        dst_path,
                    ]
                    r = subprocess.run(cmd_copy, capture_output=True, text=True)
                    if r.returncode == 0 and os.path.exists(dst_path):
                        return dst_path

                    # Fallback: re-encode to Opus if copy fails
                    cmd_enc = [
                        'ffmpeg', '-y', '-i', src_path,
                        '-vn',
                        '-c:a', 'libopus', '-b:a', '160k',
                        dst_path,
                    ]
                    r2 = subprocess.run(cmd_enc, capture_output=True, text=True)
                    if r2.returncode == 0 and os.path.exists(dst_path):
                        return dst_path
                except Exception:
                    pass
                return src_path

            # Tag files (best-effort)
            try:
                from mutagen.oggopus import OggOpus
            except Exception:
                OggOpus = None  # type: ignore

        from .integrations.ytmusic_api import get_album_full
        from .integrations.ytmusic_api import get_album_full

        def _looks_like_views(s: str) -> bool:
            ss = (s or "").strip().lower()
            if not ss:
                return True
            if "view" in ss or "play" in ss:
                return True
            # '123k', '1.2m', '3,421'
            if re.fullmatch(r"\d+[\d,\.]*\s*[kmb]?", ss):
                return True
            return False

        # Track album art URLs so we can write cover.jpg into the final album folder.
        # Keyed by (albumartist, album).
        album_art: Dict[Tuple[str, str], str] = {}

        for vid in vids:
            job = self._jobs.get(vid)
            path = self._ready.get(vid)
            if not job or not path or not os.path.exists(path):
                continue

            # Repair missing/bad metadata using album context whenever possible.
            # This is critical for deterministic storage and Navidrome display.
            if (not job.album_artist or _looks_like_views(job.album_artist) or not job.album or not job.artist or _looks_like_views(job.artist)) and job.browse_id:
                try:
                    full = get_album_full(job.browse_id) or {}
                    alb_title = (full.get('title') or '').strip()
                    alb_artist = (full.get('artist') or '').strip()
                    if alb_title and not job.album:
                        job.album = alb_title
                    if alb_artist and (not job.album_artist or _looks_like_views(job.album_artist)):
                        job.album_artist = alb_artist
                    # Try to find the matching track entry by video_id and fill track artist/title/pos/duration.
                    for t in (full.get('tracks') or []):
                        if str(t.get('video_id') or '') == job.video_id:
                            t_title = (t.get('title') or '').strip()
                            t_artist = (t.get('artist') or '').strip()
                            if t_title and not job.title:
                                job.title = t_title
                            if t_artist and (not job.artist or _looks_like_views(job.artist)):
                                job.artist = t_artist
                            if t.get('pos') and not job.track_no:
                                try:
                                    job.track_no = int(t.get('pos') or 0)
                                except Exception:
                                    pass
                            if t.get('lengthMs') and not job.duration_ms:
                                try:
                                    job.duration_ms = int(t.get('lengthMs') or 0)
                                except Exception:
                                    pass
                            break
                except Exception:
                    pass

            # Final fallbacks: never allow blank ARTIST if we have ALBUMARTIST.
            if (not job.artist or _looks_like_views(job.artist)) and job.album_artist and not _looks_like_views(job.album_artist):
                job.artist = job.album_artist

            aa = _safe_name((job.album_artist or job.artist), "Unknown Artist")
            al = _safe_name(job.album, "Unknown Album")
            if job.art_url and (aa, al) not in album_art:
                album_art[(aa, al)] = job.art_url

            # Remux to .opus so mutagen + beets can work reliably.
            new_path = _ensure_ogg_opus(path, job.out_prefix())
            if new_path != path and os.path.exists(new_path):
                # Prefer the remuxed file for the rest of the pipeline.
                self._ready[vid] = new_path
                # Remove the original container to keep inbound clean.
                try:
                    os.remove(path)
                except Exception:
                    pass
                path = new_path

            if OggOpus is None:
                continue
            try:
                audio = OggOpus(path)
                if audio.tags is None:
                    audio.add_tags()
                # Write minimal tags. (Helix owns tags; beets is used for moving/organizing.)
                title = (job.title or '').strip()
                artist = (job.artist or '').strip()
                albumartist = (job.album_artist or artist or '').strip()
                album = (job.album or '').strip()
                if title:
                    audio.tags["TITLE"] = [title]
                if artist:
                    audio.tags["ARTIST"] = [artist]
                if albumartist:
                    audio.tags["ALBUMARTIST"] = [albumartist]
                if album:
                    audio.tags["ALBUM"] = [album]
                if job.track_no:
                    audio.tags["TRACKNUMBER"] = [str(job.track_no)]
                audio.save()
            except Exception:
                pass

        # Import each finalized file individually. This avoids bulk importing unrelated files
        # and prevents stale _ready/_jobs pointers when Beets moves the inbound file.
        for vid in vids:
            path = self._ready.get(vid)
            if not path or not os.path.exists(path):
                # If Beets already moved it, clear stale pointers.
                self._ready.pop(vid, None)
                self._jobs.pop(vid, None)
                continue
            try:
                cmd = ["beet", "-c", BEETS_CONFIG, "import", "-q", "-s", path]
                r = subprocess.run(cmd, check=False, capture_output=True, text=True)
                try:
                    log_path = os.path.join(os.path.dirname(BEETS_CONFIG), "import.log")
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(r.stdout or "")
                        f.write(r.stderr or "")
                except Exception:
                    pass
            except Exception:
                pass

            # After import, the inbound file is moved; clear stale pointers so future requests
            # can re-resolve from Subsonic (or re-download if needed).
            self._ready.pop(vid, None)
            self._jobs.pop(vid, None)

        # Write cover.jpg into each album folder in the library (best-effort).

        # Navidrome will pick up folder artwork without requiring embedded images.
        if httpx is not None:
            for (aa, al), url in album_art.items():
                try:
                    album_dir = os.path.join(MUSIC_LIBRARY_ROOT, aa, al)
                    cover_path = os.path.join(album_dir, "cover.jpg")
                    if os.path.exists(cover_path):
                        continue
                    os.makedirs(album_dir, exist_ok=True)
                    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as h:
                        resp = await h.get(url)
                        if resp.status_code == 200 and resp.content:
                            with open(cover_path, "wb") as f:
                                f.write(resp.content)
                except Exception:
                    continue

        # Ask Subsonic/Navidrome to scan (best-effort).
        try:
            if self._settings_getter is not None:
                settings = self._settings_getter() or {}
                base_url = str(settings.get("subsonic_base_url") or "").strip()
                username = str(settings.get("subsonic_username") or "").strip()
                password = str(settings.get("subsonic_password") or "").strip()
                if base_url and username and password:
                    client_name = str(settings.get("subsonic_client_name") or "Helix")
                    api_version = str(settings.get("subsonic_api_version") or "1.16.1")
                    timeout_s = int(settings.get("subsonic_timeout_s") or 20)
                    c = SubsonicClient(base_url=base_url, username=username, password=password, client_name=client_name, api_version=api_version, timeout_s=timeout_s)
                    try:
                        await c.start_scan()
                    finally:
                        await c.close()
        except Exception:
            pass
        # We do not require this to succeed.
        self._downloaded_since_finalize = [v for v in self._downloaded_since_finalize if v not in vids]
        self._last_finalize_at = time.time()


async def finalize_video_ids(self, vids: List[str]) -> None:
    """Finalize specific video_ids immediately (remux/tag -> beets import -> scan).

    Used for album background fill where we want download/tag/move per track.
    """
    async with self._finalize_lock:
        if not vids:
            return
        ensure_beets_config()
        # Deduplicate, preserve order
        vids = list(dict.fromkeys([v for v in vids if v]))

        # Remux/tag each file and import it individually so it is moved out of INBOUND_DIR.
        try:
            from mutagen.oggopus import OggOpus
        except Exception:
            OggOpus = None  # type: ignore

        def _ensure_ogg_opus(src_path: str, out_prefix: str) -> str:
            try:
                if src_path.lower().endswith('.opus') and os.path.exists(src_path):
                    return src_path
                dst_path = out_prefix + '.opus'
                cmd_copy = ['ffmpeg', '-y', '-i', src_path, '-vn', '-c:a', 'copy', dst_path]
                r = subprocess.run(cmd_copy, capture_output=True, text=True)
                if r.returncode == 0 and os.path.exists(dst_path):
                    return dst_path
                cmd_enc = ['ffmpeg', '-y', '-i', src_path, '-vn', '-c:a', 'libopus', '-b:a', '160k', dst_path]
                r2 = subprocess.run(cmd_enc, capture_output=True, text=True)
                if r2.returncode == 0 and os.path.exists(dst_path):
                    return dst_path
            except Exception:
                pass
            return src_path

        album_art: Dict[Tuple[str, str], str] = {}

        for vid in vids:
            job = self._jobs.get(vid)
            path = self._ready.get(vid)
            if not job or not path or not os.path.exists(path):
                continue

            # Repair metadata using structured album fields when possible.
            try:
                def _looks_like_views(s: str) -> bool:
                    ss = (s or "").strip().lower()
                    if not ss:
                        return True
                    if "view" in ss or "play" in ss:
                        return True
                    if re.fullmatch(r"\d+[\d,\.]*\s*[kmb]?", ss):
                        return True
                    return False

                if ((not getattr(job, "album_artist", "").strip()) or _looks_like_views(getattr(job, "album_artist", "")) or (not job.album) or (not job.artist) or _looks_like_views(job.artist)) and getattr(job, "browse_id", ""):
                    full = get_album_full(getattr(job, "browse_id", "")) or {}
                    alb_title = (full.get("title") or "").strip()
                    alb_artist = (full.get("artist") or "").strip()
                    thumb = (full.get("thumbnail") or "").strip()
                    if alb_title and not job.album:
                        job.album = alb_title
                    if alb_artist and ((not getattr(job, "album_artist", "").strip()) or _looks_like_views(getattr(job, "album_artist", ""))):
                        job.album_artist = alb_artist
                    if thumb and not getattr(job, "art_url", ""):
                        job.art_url = thumb
                    for t in (full.get("tracks") or []):
                        if str(t.get("video_id") or "") == job.video_id:
                            t_title = (t.get("title") or "").strip()
                            t_artist = (t.get("artist") or "").strip()
                            if t_title and not job.title:
                                job.title = t_title
                            if t_artist and ((not job.artist) or _looks_like_views(job.artist)):
                                job.artist = t_artist
                            if t.get("pos") and not job.track_no:
                                try:
                                    job.track_no = int(t.get("pos") or 0)
                                except Exception:
                                    pass
                            if t.get("lengthMs") and not job.duration_ms:
                                try:
                                    job.duration_ms = int(t.get("lengthMs") or 0)
                                except Exception:
                                    pass
                            break
                if ((not job.artist) or _looks_like_views(job.artist)) and getattr(job, "album_artist", "") and not _looks_like_views(getattr(job, "album_artist", "")):
                    job.artist = job.album_artist
            except Exception:
                pass

            aa = _safe_name((getattr(job, "album_artist", "") or job.artist), "Unknown Artist")
            al = _safe_name(job.album, "Unknown Album")
            if job.art_url and (aa, al) not in album_art:
                album_art[(aa, al)] = job.art_url

            new_path = _ensure_ogg_opus(path, job.out_prefix())
            if new_path != path and os.path.exists(new_path):
                self._ready[vid] = new_path
                try:
                    os.remove(path)
                except Exception:
                    pass
                path = new_path

            if OggOpus is not None:
                try:
                    audio = OggOpus(path)
                    if audio.tags is None:
                        audio.add_tags()
                    if job.title:
                        audio.tags["TITLE"] = [job.title]
                    artist_tag = (job.artist or getattr(job, "album_artist", "") or "").strip()
                    if artist_tag:
                        audio.tags["ARTIST"] = [artist_tag]
                    aa_tag = (getattr(job, "album_artist", "") or job.artist or "").strip()
                    if aa_tag:
                        audio.tags["ALBUMARTIST"] = [aa_tag]
                    if job.album:
                        audio.tags["ALBUM"] = [job.album]
                    if job.track_no:
                        audio.tags["TRACKNUMBER"] = [str(job.track_no)]
                    audio.save()
                except Exception:
                    pass

            # Import only this file (not the whole inbound folder).
            try:
                cmd = ["beet", "-c", BEETS_CONFIG, "import", "-q", "-s", path]
                r = subprocess.run(cmd, check=False, capture_output=True, text=True)
                try:
                    log_path = os.path.join(os.path.dirname(BEETS_CONFIG), "import.log")
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(r.stdout or "")
                        f.write(r.stderr or "")
                except Exception:
                    pass
            except Exception:
                pass

            # After import, the inbound file is moved; clear stale pointers so future requests can re-enqueue if needed.
            self._ready.pop(vid, None)
            self._jobs.pop(vid, None)
            self._downloaded_since_finalize = [v for v in self._downloaded_since_finalize if v != vid]

        # Write cover.jpg into each album folder (best-effort).
        if httpx is not None:
            for (aa, al), url in album_art.items():
                try:
                    album_dir = os.path.join(MUSIC_LIBRARY_ROOT, aa, al)
                    cover_path = os.path.join(album_dir, "cover.jpg")
                    if os.path.exists(cover_path):
                        continue
                    os.makedirs(album_dir, exist_ok=True)
                    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as h:
                        resp = await h.get(url)
                        if resp.status_code == 200 and resp.content:
                            with open(cover_path, "wb") as f:
                                f.write(resp.content)
                except Exception:
                    continue

        # Trigger a scan (best-effort).
        try:
            if self._settings_getter is not None:
                settings = self._settings_getter() or {}
                base_url = str(settings.get("subsonic_base_url") or "").strip()
                username = str(settings.get("subsonic_username") or "").strip()
                password = str(settings.get("subsonic_password") or "").strip()
                if base_url and username and password:
                    client_name = str(settings.get("subsonic_client_name") or "Helix")
                    api_version = str(settings.get("subsonic_api_version") or "1.16.1")
                    timeout_s = int(settings.get("subsonic_timeout_s") or 20)
                    c = SubsonicClient(base_url=base_url, username=username, password=password, client_name=client_name, api_version=api_version, timeout_s=timeout_s)
                    try:
                        await c.start_scan()
                    finally:
                        await c.close()
        except Exception:
            pass


DOWNLOAD_MANAGER = DownloadManager()
