from __future__ import annotations

import asyncio
import os
import re
import subprocess
import time
import shutil
import logging
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, Optional, List, Set, Tuple

from .integrations.subsonic import SubsonicClient
from .validators import require_valid_yt_video_id

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
INBOUND_YT_TTL_MIN = int(os.getenv("HELIX_INBOUND_YT_TTL_MIN", "60"))
INBOUND_YT_CLEANUP_INTERVAL_S = int(os.getenv("HELIX_INBOUND_YT_CLEANUP_INTERVAL_S", "3600"))

LOG = logging.getLogger(__name__)


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
    persist_to_subsonic: bool = False
    user_id: str = ""
    priority: int = 10  # lower = higher priority
    created_at: float = 0.0

    def out_prefix(self) -> str:
        # Defensive: ensure video_id cannot be used for path traversal.
        safe_vid = require_valid_yt_video_id(self.video_id)
        return os.path.join(INBOUND_DIR, safe_vid)


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

        # video_id -> (failure_count, last_failure_ts) for finalize/import retries
        self._finalize_fail: Dict[str, Tuple[int, float]] = {}

        self._settings_getter = None  # set by app startup

        # batching knobs
        self.max_batch_tracks = int(os.getenv("HELIX_FINALIZE_BATCH_TRACKS", "20"))
        self.max_batch_age_s = int(os.getenv("HELIX_FINALIZE_BATCH_AGE_S", str(10 * 60)))

        # YouTube can get flaky when an entire album is requested quickly. Keep
        # explicit imports polite and retry transient yt-dlp failures.
        self.download_retry_attempts = max(1, int(os.getenv("HELIX_YTDLP_DOWNLOAD_RETRIES", "4")))
        self.import_download_delay_s = max(0.0, float(os.getenv("HELIX_YTDLP_IMPORT_DOWNLOAD_DELAY_S", "3.0")))

    def start(self) -> None:
        ensure_beets_config()
        if self._download_task is None:
            self._download_task = asyncio.create_task(self._download_worker(), name="helix-download-worker")
        if self._finalize_task is None:
            self._finalize_task = asyncio.create_task(self._finalize_worker(), name="helix-finalize-worker")
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(
                self._playback_file_cleanup_worker(),
                name="helix-playback-file-cleanup",
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

    def _mark_for_subsonic_import(self, job: DownloadJob) -> None:
        """Mark a downloaded job for explicit Subsonic import.

        Normal playback downloads are temporary and should not be imported.
        This is called only for user-commanded add-to-Subsonic work.
        """
        job.persist_to_subsonic = True
        self._jobs[job.video_id] = job
        if job.video_id not in self._downloaded_since_finalize:
            self._downloaded_since_finalize.append(job.video_id)

    def _video_id_from_inbound_name(self, filename: str) -> str:
        """Return a safe video id prefix from an inbound yt-dlp filename."""
        if not filename or filename.startswith("."):
            return ""
        base = filename
        if base.endswith(".part"):
            base = base[:-5]
        video_id = base.split(".", 1)[0]
        try:
            return require_valid_yt_video_id(video_id)
        except Exception:
            return ""

    def ensure_stream_cache(self, video_id: str, src_path: str) -> str:
        """Create/return a stable stream path for ASAP playback.

        We never stream directly from INBOUND_DIR, because Beets may move files
        during finalization. Instead we remux/copy into STREAM_CACHE_DIR and
        stream from there.
        """
        ensure_dirs()
        if not video_id or not src_path:
            return src_path

        # Defensive: never allow path traversal into stream cache.
        try:
            video_id = require_valid_yt_video_id(video_id)
        except Exception:
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

    async def _playback_file_cleanup_worker(self) -> None:
        """Periodically delete old playback cache and temporary YT files.

        Stream cache is bounded by TTL and max size. The inbound YT folder is
        also swept every hour by default, deleting temporary playback downloads
        older than one hour. User-commanded Subsonic imports are excluded while
        pending so explicit library adds are not lost before finalization.

        We avoid deleting files for video_ids that are currently marked as
        actively streaming.
        """
        ensure_dirs()
        ttl_s = max(1, STREAM_CACHE_TTL_MIN) * 60
        max_bytes = max(1, STREAM_CACHE_MAX_MB) * 1024 * 1024
        inbound_ttl_s = max(1, INBOUND_YT_TTL_MIN) * 60
        cleanup_interval_s = max(60, INBOUND_YT_CLEANUP_INTERVAL_S)

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

                inbound_removed = 0
                for p in Path(INBOUND_DIR).glob("*"):
                    if not p.is_file():
                        continue
                    video_id = self._video_id_from_inbound_name(p.name)
                    if not video_id:
                        continue
                    if video_id in self._active_streams:
                        continue
                    job = self._jobs.get(video_id)
                    if job is not None and getattr(job, "persist_to_subsonic", False):
                        # Explicit library imports should be handled by finalize, not TTL cleanup.
                        continue
                    try:
                        st = p.stat()
                    except Exception:
                        continue
                    if now - st.st_mtime <= inbound_ttl_s:
                        continue
                    try:
                        p.unlink()
                        inbound_removed += 1
                    except Exception:
                        continue

                # Clear stale in-memory pointers whose files were removed externally or by TTL.
                for video_id, path in list(self._ready.items()):
                    if path and not os.path.exists(path):
                        self._ready.pop(video_id, None)
                        job = self._jobs.get(video_id)
                        if job is None or not getattr(job, "persist_to_subsonic", False):
                            self._jobs.pop(video_id, None)
                            self._downloaded_since_finalize = [v for v in self._downloaded_since_finalize if v != video_id]

                if removed or inbound_removed:
                    self._stream_cache_log(
                        f"cleanup stream_removed={removed} inbound_removed={inbound_removed} "
                        f"stream_ttl_min={STREAM_CACHE_TTL_MIN} inbound_ttl_min={INBOUND_YT_TTL_MIN} max_mb={STREAM_CACHE_MAX_MB}"
                    )
            except Exception as e:
                self._stream_cache_log(f"cleanup error: {e!r}")

            await asyncio.sleep(cleanup_interval_s)

    async def ensure_downloaded(self, job: DownloadJob) -> str:
        """Ensure a given video_id is downloaded. Returns inbound path."""
        # Defensive: validate at the download boundary.
        job.video_id = require_valid_yt_video_id(job.video_id)
        if self.is_ready(job.video_id):
            return self.ready_path(job.video_id)

        # enqueue if needed
        if job.video_id not in self._jobs:
            job.created_at = time.time()
            self._jobs[job.video_id] = job
            await self._q.put((job.priority, job.created_at, job))
        self._notify_job_status(job, "QUEUED")

        while True:
            p = self._ready.get(job.video_id)
            if p and os.path.exists(p):
                return p
            await asyncio.sleep(0.25)

    async def ensure_started(
        self,
        job: DownloadJob,
        wait_s: float = 8.0,
        min_bytes: int | None = None,
    ) -> str:
        """Ensure a download has begun and return a streamable *current* path.

        For progressive playback, yt-dlp writes to a growing `<name>.<ext>.part` file
        while downloading. This function returns that `.part` path as soon as it exists.

        Returns:
            - ready (final) inbound path if already downloaded
            - `.part` path if download has started
            - "" if it could not be started/detected quickly
        """
        # Defensive: validate at the download boundary.
        job.video_id = require_valid_yt_video_id(job.video_id)

        # If already downloaded, return final path.
        if self.is_ready(job.video_id):
            return self._ready.get(job.video_id, "")

        # Enqueue if not already enqueued.
        if job.video_id not in self._jobs:
            job.created_at = time.time()
            self._jobs[job.video_id] = job
            await self._q.put((job.priority, job.created_at, job))
        self._notify_job_status(job, "QUEUED")

        # Wait briefly for yt-dlp to create the `.part` file.
        prefix = os.path.basename(job.out_prefix()) + "."
        deadline = time.time() + max(0.1, float(wait_s))
        while time.time() < deadline:
            # If finished while waiting, return final.
            if self.is_ready(job.video_id):
                return self._ready.get(job.video_id, "")

            try:
                for fn in os.listdir(INBOUND_DIR):
                    if fn.startswith(prefix) and fn.endswith(".part"):
                        p = os.path.join(INBOUND_DIR, fn)
                        if min_bytes is not None:
                            try:
                                if os.path.getsize(p) < int(min_bytes):
                                    continue
                            except Exception:
                                continue
                        return p
            except Exception:
                pass

            await asyncio.sleep(0.1)

        return ""


        # wait until ready
        while True:
            p = self._ready.get(job.video_id)
            if p and os.path.exists(p):
                return p
            await asyncio.sleep(0.25)

    def _merge_explicit_import_metadata(self, existing: DownloadJob, requested: DownloadJob) -> None:
        """Upgrade a temporary playback job using user-commanded import metadata.

        A track may already be queued or downloaded for playback when the user later
        clicks Add to Subsonic. In that case we must not only flip the persistence
        flag; we also need the richer title/artist/album/art/browse metadata from
        the explicit request so finalization can tag and move the audio correctly.
        """
        for field_name in ("title", "artist", "album", "album_artist", "browse_id", "art_url"):
            value = getattr(requested, field_name, "")
            if value:
                setattr(existing, field_name, value)
        if requested.track_no:
            existing.track_no = requested.track_no
        if requested.duration_ms:
            existing.duration_ms = requested.duration_ms
        existing.priority = min(existing.priority, requested.priority)
        existing.persist_to_subsonic = True

    def _fallback_library_move(self, job: DownloadJob, path: str) -> str:
        """Move a finalized inbound file into the music library if Beets did not.

        Beets is still the first-choice importer, but the manual Add to Subsonic
        path must not leave a correctly tagged file stranded in /inbound_yt. If
        `beet import` fails or returns without moving the file, this deterministic
        fallback uses the same foldering rules as our Beets config:

            $albumartist/$album/$track $title
        """
        if not path or not os.path.exists(path):
            return ""

        album_artist = _safe_name((getattr(job, "album_artist", "") or job.artist), "Unknown Artist")
        album = _safe_name(job.album, "Unknown Album")
        title = _safe_name(job.title, "Unknown Title")
        track_prefix = f"{int(job.track_no):02d} " if getattr(job, "track_no", 0) else ""
        ext = os.path.splitext(path)[1] or ".opus"

        album_dir = os.path.join(MUSIC_LIBRARY_ROOT, album_artist, album)
        os.makedirs(album_dir, exist_ok=True)

        candidate = os.path.join(album_dir, f"{track_prefix}{title}{ext}")
        if os.path.exists(candidate):
            stem = os.path.splitext(os.path.basename(candidate))[0]
            for i in range(2, 1000):
                next_candidate = os.path.join(album_dir, f"{stem} ({i}){ext}")
                if not os.path.exists(next_candidate):
                    candidate = next_candidate
                    break

        shutil.move(path, candidate)
        return candidate

    async def enqueue_normal(self, job: DownloadJob) -> None:
        # Defensive: validate at the download boundary.
        job.video_id = require_valid_yt_video_id(job.video_id)

        existing = self._jobs.get(job.video_id)
        if existing is not None:
            if getattr(job, "persist_to_subsonic", False):
                # A user explicitly requested this track be added after it had already
                # been queued/downloaded for temporary playback. Upgrade the existing
                # job instead of dropping the import request or losing metadata.
                self._merge_explicit_import_metadata(existing, job)
                if self.is_ready(job.video_id):
                    self._mark_for_subsonic_import(existing)
            return

        if self.is_ready(job.video_id):
            if getattr(job, "persist_to_subsonic", False):
                self._mark_for_subsonic_import(job)
            return

        job.created_at = time.time()
        self._jobs[job.video_id] = job
        await self._q.put((job.priority, job.created_at, job))
        self._notify_job_status(job, "QUEUED")

    def _notify_job_status(self, job: DownloadJob, status: str, error: str = "") -> None:
        user_id = str(getattr(job, "user_id", "") or "")
        if not user_id:
            return
        try:
            from .db import SessionLocal
            from .models import QueueItem
            from sqlalchemy import select
            db = SessionLocal()
            try:
                rows = db.execute(select(QueueItem).where(QueueItem.session_user_id == user_id, QueueItem.yt_video_id == job.video_id)).scalars().all()
                for row in rows:
                    row.download_status = status
                    if error:
                        row.error = error[:500]
                    elif status in {"QUEUED", "DOWNLOADING", "DOWNLOADED"} and row.error not in {"NOT_IN_LIBRARY", ""}:
                        row.error = ""
                if rows:
                    db.commit()
            finally:
                db.close()
            from .realtime import schedule_player_state_broadcast
            schedule_player_state_broadcast(user_id)
        except Exception:
            LOG.exception("Failed to publish download status user=%s video=%s status=%s", user_id, job.video_id, status)

    def _download_log(self, message: str) -> None:
        try:
            log_path = os.path.join(os.path.dirname(BEETS_CONFIG), "download.log")
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(message.rstrip() + "\n")
        except Exception:
            pass

    async def _download_worker(self):
        ensure_dirs()
        while True:
            prio, created, job = await self._q.get()
            try:
                # Might have been downloaded while waiting.
                if self.is_ready(job.video_id):
                    continue

                # Explicit Subsonic imports are not latency-sensitive. Throttle
                # them slightly so album imports do not look like a burst of
                # automated YouTube downloads.
                if getattr(job, "persist_to_subsonic", False) and self.import_download_delay_s > 0:
                    await asyncio.sleep(self.import_download_delay_s)

                self._notify_job_status(job, "DOWNLOADING")

                # Download best audio without converting during the ASAP path.
                # We prefer Opus streams, but keep container as provided by YouTube (often .webm).
                # Conversion/remux to .opus (Ogg Opus) happens only for explicit Subsonic imports.
                outtmpl = job.out_prefix() + ".%(ext)s"
                cmd = [
                    "yt-dlp",
                    "-f",
                    "bestaudio[acodec=opus]/bestaudio",
                    "--no-playlist",
                    "--no-progress",
                    "--newline",
                    "--retries",
                    "5",
                    "--fragment-retries",
                    "5",
                    "--retry-sleep",
                    "linear=2::8",
                    "-o",
                    outtmpl,
                    job.url,
                ]

                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                out_b, err_b = await proc.communicate()
                out_s = (out_b or b"").decode("utf-8", "ignore")
                err_s = (err_b or b"").decode("utf-8", "ignore")
                if proc.returncode != 0:
                    raise RuntimeError((err_s.strip() or out_s.strip() or "yt-dlp failed"))

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
                self._notify_job_status(job, "DOWNLOADED")
                if getattr(job, "persist_to_subsonic", False):
                    self._mark_for_subsonic_import(job)
                self._download_log(f"[download-worker] OK video_id={job.video_id} title={job.title!r} track_no={job.track_no}")
            except Exception as e:
                # Retry transient failures instead of silently dropping album tracks.
                self._ready.pop(job.video_id, None)
                attempt = int(getattr(job, "download_attempts", 0) or 0) + 1
                setattr(job, "download_attempts", attempt)
                self._download_log(
                    f"[download-worker] FAILED attempt={attempt}/{self.download_retry_attempts} "
                    f"video_id={job.video_id} title={job.title!r} track_no={job.track_no} url={job.url} err={e!r}"
                )
                print(f"[download-worker] FAILED attempt={attempt}/{self.download_retry_attempts} video_id={job.video_id} url={job.url} err={e!r}")
                if attempt < self.download_retry_attempts:
                    # Keep the same job metadata. Put it back behind nearby work
                    # with a small backoff so one bad track does not block the
                    # whole album forever.
                    await asyncio.sleep(min(30.0, 2.0 * attempt))
                    await self._q.put((prio + attempt, time.time(), job))
                else:
                    self._notify_job_status(job, "FAILED", str(e))
                    self._download_log(f"[download-worker] GAVE_UP video_id={job.video_id} title={job.title!r} track_no={job.track_no}")
            finally:
                self._q.task_done()

    async def _finalize_worker(self):
        """Finalize/import only user-commanded Subsonic additions one-at-a-time (FIFO).

        Normal playback downloads are temporary and are cleaned from INBOUND_DIR by TTL.
        """
        while True:
            try:
                await asyncio.sleep(1)

                if not self._downloaded_since_finalize:
                    continue

                # First downloaded video that is not currently being streamed.
                vid = None
                for v in list(dict.fromkeys(self._downloaded_since_finalize)):
                    if v not in self._active_streams:
                        vid = v
                        break
                if not vid:
                    continue

                # Finalize only this video id without swapping out the shared
                # pending list. Album imports can finish downloading additional
                # tracks while this await is in progress; replacing/restoring the
                # whole list here used to silently discard those newly completed
                # tracks and leave albums partially imported.
                await finalize_video_ids(self, [vid])
            except Exception:
                LOG.exception("Finalize worker failed for video_id=%s", vid if 'vid' in locals() else None)
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

            # Capture metadata for Subsonic re-check after import/scan (best-effort).
            _wait_targets: Dict[str, Tuple[str, str, Optional[int]]] = {}
            for _v in vids:
                _j = self._jobs.get(_v)
                if not _j:
                    continue
                _t = (_j.title or '').strip()
                _a = (_j.artist or '').strip()
                _d = int(_j.duration_ms) if getattr(_j, 'duration_ms', None) else None
                if _t and _a:
                    _wait_targets[_v] = (_t, _a, _d)

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

        from .integrations.ytmusic import get_album_full
        from .integrations.ytmusic import get_album_full

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
            # Backoff if prior finalize/import attempts failed.
            fc, ft = self._finalize_fail.get(vid, (0, 0.0))
            if fc and (time.time() - ft) < min(300, 5 * fc):
                continue

            job = self._jobs.get(vid)
            path = self._ready.get(vid)
            if not job or not path or not os.path.exists(path):
                continue

            # Repair missing/bad metadata using album context whenever possible.
            # This is critical for deterministic storage and Navidrome display.
            if job.browse_id:
                try:
                    full = get_album_full(job.browse_id) or {}
                    alb_title = (full.get('title') or '').strip()
                    alb_artist = (full.get('artist') or '').strip()
                    if alb_title and not job.album:
                        job.album = alb_title
                    if alb_artist and (not job.album_artist or _looks_like_views(job.album_artist)):
                        job.album_artist = alb_artist
                    # Album-level YTMusic artwork is authoritative.  Search/video
                    # thumbnails can be padded or widescreen even when the album
                    # metadata exposes a proper square cover.
                    album_thumb = (full.get("thumbnail_url") or full.get("thumbnail") or "").strip()
                    if album_thumb:
                        job.art_url = album_thumb
                    # Try to find the matching track entry by video_id and fill track artist/title/pos/duration.
                    for t in (full.get('tracks') or []):
                        if str(t.get('video_id') or '') == job.video_id:
                            t_title = (t.get('title') or '').strip()
                            t_artist = (t.get('artist') or '').strip()
                            if t_title and not job.title:
                                job.title = t_title
                            if t_artist and (not job.artist or _looks_like_views(job.artist)):
                                job.artist = t_artist
                            for key in ('track_no', 'trackNumber', 'track_number', 'pos', 'position'):
                                if t.get(key) and not job.track_no:
                                    try:
                                        job.track_no = int(t.get(key) or 0)
                                        break
                                    except Exception:
                                        pass
                            if not job.duration_ms:
                                try:
                                    if t.get('duration_ms') or t.get('lengthMs'):
                                        job.duration_ms = int(t.get('duration_ms') or t.get('lengthMs') or 0)
                                    elif t.get('duration_seconds'):
                                        job.duration_ms = int(t.get('duration_seconds') or 0) * 1000
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
            job = self._jobs.get(vid)
            beets_moved = False
            try:
                cmd = ["beet", "-c", BEETS_CONFIG, "import", "-q", "-s", path]
                r = subprocess.run(cmd, check=False, capture_output=True, text=True)
                beets_moved = r.returncode == 0 and not os.path.exists(path)
                try:
                    log_path = os.path.join(os.path.dirname(BEETS_CONFIG), "import.log")
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(r.stdout or "")
                        f.write(r.stderr or "")
                        if r.returncode != 0:
                            f.write(f"\n[helix] beets import failed for {vid} rc={r.returncode} path={path}\n")
                        elif os.path.exists(path):
                            f.write(f"\n[helix] beets import completed but did not move {vid}; using Helix fallback move path={path}\n")
                except Exception:
                    pass
            except Exception as e:
                try:
                    log_path = os.path.join(os.path.dirname(BEETS_CONFIG), "import.log")
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(f"\n[helix] beets import exception for {vid}: {e!r} path={path}\n")
                except Exception:
                    pass

            if not beets_moved and job is not None and os.path.exists(path):
                try:
                    moved_to = self._fallback_library_move(job, path)
                    try:
                        log_path = os.path.join(os.path.dirname(BEETS_CONFIG), "import.log")
                        with open(log_path, "a", encoding="utf-8") as f:
                            f.write(f"\n[helix] fallback moved {vid} to {moved_to}\n")
                    except Exception:
                        pass
                except Exception as e:
                    self._finalize_fail[vid] = (self._finalize_fail.get(vid, (0, 0.0))[0] + 1, time.time())
                    try:
                        log_path = os.path.join(os.path.dirname(BEETS_CONFIG), "import.log")
                        with open(log_path, "a", encoding="utf-8") as f:
                            f.write(f"\n[helix] fallback move failed for {vid}: {e!r} path={path}\n")
                    except Exception:
                        pass
                    continue

            # After import/fallback move, the inbound file should be gone; clear stale pointers
            # so future requests can re-resolve from Subsonic (or re-download if needed).
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
                        ok = await c.start_scan()
                        if not ok:
                            LOG.warning('[subsonic] startScan failed (continuing)')
                    finally:
                        # Wait briefly for newly imported tracks to become visible via Subsonic,
                        # to avoid re-downloading/re-importing due to index lag.
                        try:
                            for _v, (_t, _a, _d) in _wait_targets.items():
                                # Keep this bounded; we only need enough to make the next lookup succeed.
                                await c.wait_for_song_best(title=_t, artist=_a, duration_ms=_d, timeout_s=30, poll_s=2.0)
                        except Exception:
                            pass
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
        from .integrations.ytmusic import get_album_full
        # Deduplicate, preserve order
        vids = list(dict.fromkeys([v for v in vids if v]))

        # Capture metadata for Subsonic re-check after import/scan (best-effort).
        _wait_targets: Dict[str, Tuple[str, str, Optional[int]]] = {}
        for _v in vids:
            _j = self._jobs.get(_v)
            if not _j:
                continue
            _t = (_j.title or '').strip()
            _a = (_j.artist or '').strip()
            _d = int(_j.duration_ms) if getattr(_j, 'duration_ms', None) else None
            if _t and _a:
                _wait_targets[_v] = (_t, _a, _d)


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
            # Backoff if prior finalize/import attempts failed.
            fc, ft = self._finalize_fail.get(vid, (0, 0.0))
            if fc and (time.time() - ft) < min(300, 5 * fc):
                continue

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
                    thumb = (full.get("thumbnail_url") or full.get("thumbnail") or "").strip()
                    if alb_title and not job.album:
                        job.album = alb_title
                    if alb_artist and ((not getattr(job, "album_artist", "").strip()) or _looks_like_views(getattr(job, "album_artist", ""))):
                        job.album_artist = alb_artist
                    if thumb:
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

            # Import only this file (not the whole inbound folder). Beets can
            # legitimately return success while deciding to skip a duplicate, in
            # which case the inbound file is still present. Treat "source file is
            # gone" as the actual success condition. If Beets does not move it,
            # use Helix's deterministic library move so explicit album imports
            # cannot end up with only cover.jpg in the destination folder.
            beets_moved = False
            try:
                cmd = ["beet", "-c", BEETS_CONFIG, "import", "-q", "-s", path]
                r = subprocess.run(cmd, check=False, capture_output=True, text=True)
                beets_moved = r.returncode == 0 and not os.path.exists(path)
                try:
                    log_path = os.path.join(os.path.dirname(BEETS_CONFIG), "import.log")
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(r.stdout or "")
                        f.write(r.stderr or "")
                        if r.returncode != 0:
                            f.write(f"\n[helix] beets import failed for {vid} rc={r.returncode} path={path}\n")
                        elif os.path.exists(path):
                            f.write(
                                f"\n[helix] beets import completed but did not move {vid}; "
                                f"using Helix fallback move path={path}\n"
                            )
                except Exception:
                    pass
            except FileNotFoundError as e:
                try:
                    log_path = os.path.join(os.path.dirname(BEETS_CONFIG), "import.log")
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(f"\n[helix] beets executable not found for {vid}: {e!r}\n")
                except Exception:
                    pass
            except Exception as e:
                try:
                    log_path = os.path.join(os.path.dirname(BEETS_CONFIG), "import.log")
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(f"\n[helix] beets import exception for {vid}: {e!r} path={path}\n")
                except Exception:
                    pass

            finalized = beets_moved
            if not finalized and os.path.exists(path):
                try:
                    moved_to = self._fallback_library_move(job, path)
                    finalized = bool(moved_to and os.path.exists(moved_to))
                    try:
                        log_path = os.path.join(os.path.dirname(BEETS_CONFIG), "import.log")
                        with open(log_path, "a", encoding="utf-8") as f:
                            f.write(f"\n[helix] fallback moved {vid} to {moved_to}\n")
                    except Exception:
                        pass
                except Exception as e:
                    try:
                        log_path = os.path.join(os.path.dirname(BEETS_CONFIG), "import.log")
                        with open(log_path, "a", encoding="utf-8") as f:
                            f.write(f"\n[helix] fallback move failed for {vid}: {e!r} path={path}\n")
                    except Exception:
                        pass

            if not finalized:
                # Record failure and keep the job/ready pointers so finalize can retry later.
                fc, _ft = self._finalize_fail.get(vid, (0, 0.0))
                self._finalize_fail[vid] = (fc + 1, time.time())
                self._downloaded_since_finalize = [v for v in self._downloaded_since_finalize if v != vid] + [vid]
                continue

            # Success: clear any failure state and stale inbound pointers.
            self._finalize_fail.pop(vid, None)
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
                        ok = await c.start_scan()
                        if not ok:
                            LOG.warning('[subsonic] startScan failed (continuing)')
                        # Wait briefly for newly imported tracks to become visible via Subsonic.
                        try:
                            for _v, (_t, _a, _d) in _wait_targets.items():
                                await c.wait_for_song_best(title=_t, artist=_a, duration_ms=_d, timeout_s=30, poll_s=2.0)
                        except Exception:
                            pass
                    finally:
                        await c.close()
        except Exception:
            pass


DOWNLOAD_MANAGER = DownloadManager()
