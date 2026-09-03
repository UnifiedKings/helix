from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select

from .db import SessionLocal
from .download_manager import DOWNLOAD_MANAGER, DownloadJob
from .integrations.slskd import SlskdCandidate, SlskdClient
from .integrations.subsonic import SubsonicClient
from .quality_models import AdminNotification, QualityUpgradeEvent, QualityUpgradeJob, QualityUpgradeMetadata
from .realtime import schedule_quality_upgrades_changed
from .settings_store import get_settings

LOG = logging.getLogger(__name__)

# Soulseek searches may run concurrently, but their *start requests* are
# staggered so multiple jobs do not POST /searches at the same instant.
_QUALITY_SEARCH_START_LOCK: asyncio.Lock | None = None
_QUALITY_LAST_SEARCH_START: float = 0.0
_QUALITY_SEARCH_START_INTERVAL_S = 2.0


def _quality_search_start_lock() -> asyncio.Lock:
    global _QUALITY_SEARCH_START_LOCK
    if _QUALITY_SEARCH_START_LOCK is None:
        _QUALITY_SEARCH_START_LOCK = asyncio.Lock()
    return _QUALITY_SEARCH_START_LOCK


async def _wait_for_search_start_slot() -> None:
    global _QUALITY_LAST_SEARCH_START
    async with _quality_search_start_lock():
        loop = asyncio.get_running_loop()
        now = loop.time()
        wait_s = max(
            0.0,
            (_QUALITY_LAST_SEARCH_START + _QUALITY_SEARCH_START_INTERVAL_S) - now,
        )
        if wait_s > 0:
            await asyncio.sleep(wait_s)
        _QUALITY_LAST_SEARCH_START = loop.time()


def _commit_quality_change(db: Session) -> None:
    db.commit()
    schedule_quality_upgrades_changed()


def _job_metadata(db: Session, job_id: str) -> QualityUpgradeMetadata:
    row = db.get(QualityUpgradeMetadata, job_id)
    if row is None:
        row = QualityUpgradeMetadata(job_id=job_id, provenance="helix_imported")
        db.add(row)
        db.flush()
    return row


def _record_event(
    db: Session,
    job_id: str,
    event: str,
    message: str = "",
    data: dict[str, Any] | None = None,
) -> None:
    db.add(QualityUpgradeEvent(
        job_id=job_id,
        event=event,
        message=message,
        data_json=json.dumps(data or {}, ensure_ascii=False),
    ))


def _persist_active_search(job_id: str, search_id: str, query: str) -> None:
    db = SessionLocal()
    try:
        meta = _job_metadata(db, job_id)
        meta.slskd_search_id = search_id
        meta.slskd_search_query = query
        meta.updated_at = datetime.utcnow()
        _record_event(db, job_id, "search_started", f"Soulseek search started: {query}", {"search_id": search_id, "query": query})
        _commit_quality_change(db)
    finally:
        db.close()


def _clear_active_search(db: Session, job_id: str) -> None:
    meta = _job_metadata(db, job_id)
    meta.slskd_search_id = ""
    meta.slskd_search_query = ""
    meta.updated_at = datetime.utcnow()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _candidate_to_dict(candidate: SlskdCandidate, score: float = 0.0) -> dict[str, Any]:
    return {
        "username": candidate.username,
        "filename": candidate.filename,
        "size": int(candidate.size or 0),
        "bitrate": int(candidate.bitrate or 0),
        "sample_rate": int(candidate.sample_rate or 0),
        "bit_depth": int(candidate.bit_depth or 0),
        "duration_ms": int(candidate.duration_ms or 0),
        "free_upload_slots": bool(candidate.free_upload_slots),
        "queue_length": int(candidate.queue_length or 0),
        "score": float(score),
    }


def _candidate_from_dict(data: dict[str, Any]) -> SlskdCandidate | None:
    username = str(data.get("username") or "").strip()
    filename = str(data.get("filename") or "").strip()
    if not username or not filename:
        return None
    return SlskdCandidate(
        username=username,
        filename=filename,
        size=int(data.get("size") or 0),
        bitrate=int(data.get("bitrate") or 0),
        sample_rate=int(data.get("sample_rate") or 0),
        bit_depth=int(data.get("bit_depth") or 0),
        duration_ms=int(data.get("duration_ms") or 0),
        free_upload_slots=bool(data.get("free_upload_slots")),
        queue_length=int(data.get("queue_length") or 0),
    )


def _candidate_meets_policy(candidate: SlskdCandidate, settings: dict[str, Any], job: Any | None = None) -> bool:
    if bool(settings.get("quality_upgrade_lossless_only", True)) and not _is_lossless(candidate):
        return False
    min_sr = max(0, int(settings.get("quality_upgrade_min_sample_rate") or 0))
    min_depth = max(0, int(settings.get("quality_upgrade_min_bit_depth") or 0))
    # Missing Soulseek attributes are not treated as proof of low quality; the
    # downloaded file is validated again with Mutagen before replacement.
    if candidate.sample_rate and min_sr and candidate.sample_rate < min_sr:
        return False
    if candidate.bit_depth and min_depth and candidate.bit_depth < min_depth:
        return False

    if job is not None:
        current_codec = str(getattr(job, "current_codec", "") or "").lower()
        current_lossless = current_codec in {"flac", "alac", "wav", "aiff", "aif"}
        if current_lossless:
            if not bool(settings.get("quality_upgrade_replace_lossless", False)):
                return False
            current_rank = (
                1,
                int(getattr(job, "current_bit_depth", 0) or 0),
                int(getattr(job, "current_sample_rate", 0) or 0),
                int(getattr(job, "current_bitrate", 0) or 0),
            )
            if _candidate_rank(candidate) <= current_rank:
                return False
    return True


def _quality_info_meets_policy(info: dict[str, Any], settings: dict[str, Any]) -> bool:
    codec = str(info.get("codec") or "").lower()
    if bool(settings.get("quality_upgrade_lossless_only", True)) and codec not in {"flac", "alac", "wav", "aiff", "aif"}:
        return False
    min_sr = max(0, int(settings.get("quality_upgrade_min_sample_rate") or 0))
    min_depth = max(0, int(settings.get("quality_upgrade_min_bit_depth") or 0))
    if min_sr and int(info.get("sample_rate") or 0) and int(info.get("sample_rate") or 0) < min_sr:
        return False
    if min_depth and int(info.get("bit_depth") or 0) and int(info.get("bit_depth") or 0) < min_depth:
        return False
    return True


def _set_search_job_status(job_id: str, status: str, *, mark_started: bool = False) -> None:
    """Persist search queue/searching state with a short-lived DB session."""
    status_db = SessionLocal()
    try:
        current = status_db.get(QualityUpgradeJob, job_id)
        if current is None:
            return
        if current.status in {"upgraded", "reverted", "satisfied", "externally_modified"}:
            return
        changed = current.status != status
        if changed:
            current.status = status
        if mark_started:
            current.last_search_at = datetime.utcnow()
            changed = True
        if not changed:
            return
        current.updated_at = datetime.utcnow()
        _commit_quality_change(status_db)
    finally:
        status_db.close()


def _set_transfer_job_status(job_id: str, status: str) -> None:
    """Persist a transfer-only status with a very short DB transaction.

    Network/peer waits must never retain the worker's long-lived ORM session or
    an underlying SQLAlchemy connection.
    """
    status_db = SessionLocal()
    try:
        current = status_db.get(QualityUpgradeJob, job_id)
        if current is None:
            return
        if current.status in {"upgraded", "reverted", "satisfied", "externally_modified"}:
            return
        if current.status == status:
            return
        current.status = status
        current.updated_at = datetime.utcnow()
        _commit_quality_change(status_db)
    finally:
        status_db.close()

_RETRY_DELAYS = (
    timedelta(days=1),
    timedelta(days=7),
    timedelta(days=30),
)

# Library-copy/path resolution is a local readiness problem, not a Soulseek
# search miss. Retry it a handful of times with increasing delays, then stop
# automatically until the user explicitly retries the job.
_LIBRARY_RESOLUTION_RETRY_DELAYS = (
    timedelta(seconds=5),
    timedelta(seconds=30),
    timedelta(minutes=2),
    timedelta(minutes=10),
    timedelta(hours=1),
    timedelta(hours=6),
    timedelta(hours=24),
)
_LIBRARY_RESOLUTION_MAX_ATTEMPTS = len(_LIBRARY_RESOLUTION_RETRY_DELAYS) + 1


def _schedule_library_resolution_retry(job: QualityUpgradeJob, final_error: str) -> None:
    """Back off unresolved library copies and eventually require manual retry."""
    job.attempts = int(job.attempts or 0) + 1
    if job.attempts >= _LIBRARY_RESOLUTION_MAX_ATTEMPTS:
        job.status = "failed"
        job.last_error = final_error
        job.next_search_at = None
        return

    delay_index = min(job.attempts - 1, len(_LIBRARY_RESOLUTION_RETRY_DELAYS) - 1)
    job.status = "pending"
    job.next_search_at = datetime.utcnow() + _LIBRARY_RESOLUTION_RETRY_DELAYS[delay_index]
_BAD_VERSION_WORDS = {
    "live": 45,
    "remix": 45,
    "instrumental": 55,
    "karaoke": 60,
    "cover": 55,
    "acoustic": 35,
    "sped up": 55,
    "slowed": 55,
    "nightcore": 60,
    "radio edit": 30,
    "demo": 35,
}


def _norm(value: str) -> str:
    value = (value or "").lower()
    value = value.replace("’", "'").replace("–", "-").replace("—", "-")
    value = re.sub(r"\bthe\b", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def _candidate_duration_ms(candidate: SlskdCandidate) -> int:
    # slskd exposes length on some builds through audio attributes, but this
    # adapter deliberately stays tolerant. Candidate duration is populated by
    # _candidate_from_raw in future builds when available.
    return int(getattr(candidate, "duration_ms", 0) or 0)


def _score_candidate(job: QualityUpgradeJob, candidate: SlskdCandidate) -> float:
    haystack = _norm(candidate.filename)
    title = _norm(job.title)
    artist = _norm(job.artist)
    album = _norm(job.album)

    score = 0.0
    if title and title in haystack:
        score += 38
    else:
        title_parts = [p for p in title.split() if len(p) > 2]
        if title_parts:
            score += 38 * (sum(p in haystack for p in title_parts) / len(title_parts))

    if artist and artist in haystack:
        score += 30
    else:
        artist_parts = [p for p in artist.split() if len(p) > 2]
        if artist_parts:
            score += 30 * (sum(p in haystack for p in artist_parts) / len(artist_parts))

    if album and album in haystack:
        score += 10

    requested = _norm(f"{job.title} {job.album}")
    for phrase, penalty in _BAD_VERSION_WORDS.items():
        if phrase in haystack and phrase not in requested:
            score -= penalty

    duration_ms = _candidate_duration_ms(candidate)
    if duration_ms and job.duration_ms:
        diff = abs(duration_ms - job.duration_ms)
        if diff <= 2000:
            score += 22
        elif diff <= 5000:
            score += 12
        elif diff > 10000:
            score -= 35

    ext = candidate.extension
    if ext in {".flac", ".alac"}:
        score += 5
    if candidate.free_upload_slots:
        score += 2
    if candidate.queue_length > 100:
        score -= 2
    return max(0.0, min(100.0, score))


def _is_lossless(candidate: SlskdCandidate) -> bool:
    return candidate.extension in {".flac", ".alac", ".wav", ".aiff", ".aif"}


def _candidate_rank(candidate: SlskdCandidate) -> tuple[int, int, int, int]:
    return (
        1 if _is_lossless(candidate) else 0,
        int(candidate.bit_depth or 0),
        int(candidate.sample_rate or 0),
        int(candidate.bitrate or 0),
    )


def create_upgrade_job(
    *,
    user_id: str,
    yt_video_id: str,
    yt_browse_id: str = "",
    title: str,
    artist: str,
    album: str = "",
    album_artist: str = "",
    duration_ms: int = 0,
    track_no: int = 0,
    art_url: str = "",
) -> None:
    db = SessionLocal()
    try:
        existing = db.execute(
            select(QualityUpgradeJob)
            .where(QualityUpgradeJob.yt_video_id == yt_video_id)
            .where(QualityUpgradeJob.status.notin_(["reverted", "upgraded", "satisfied"]))
            .order_by(QualityUpgradeJob.created_at.desc())
        ).scalars().first()
        if existing:
            return
        job = QualityUpgradeJob(
            requested_by_user_id=user_id,
            yt_video_id=yt_video_id,
            yt_browse_id=yt_browse_id,
            title=title,
            artist=artist,
            album=album,
            album_artist=album_artist or artist,
            duration_ms=int(duration_ms or 0),
            track_no=int(track_no or 0),
            art_url=art_url,
            status="pending",
        )
        db.add(job)
        db.flush()
        _job_metadata(db, job.id).provenance = "helix_imported"
        _record_event(db, job.id, "enrolled", "Track enrolled for Helix-owned quality upgrades")
        _commit_quality_change(db)
    finally:
        db.close()


def _subsonic(settings: dict[str, Any]) -> SubsonicClient | None:
    base = str(settings.get("subsonic_base_url") or "").strip()
    user = str(settings.get("subsonic_username") or "").strip()
    password = str(settings.get("subsonic_password") or "").strip()
    if not base or not user or not password:
        return None
    return SubsonicClient(
        base_url=base,
        username=user,
        password=password,
        client_name=str(settings.get("subsonic_client_name") or "Helix"),
        api_version=str(settings.get("subsonic_api_version") or "1.16.1"),
        timeout_s=int(settings.get("subsonic_timeout_s") or 20),
    )


def _audio_info(path: Path) -> dict[str, int | str]:
    try:
        from mutagen import File
        audio = File(str(path))
        info = getattr(audio, "info", None)
        codec = path.suffix.lower().lstrip(".")
        bitrate = int(getattr(info, "bitrate", 0) or 0)
        sample_rate = int(getattr(info, "sample_rate", 0) or 0)
        bit_depth = int(getattr(info, "bits_per_sample", 0) or 0)
        return {"codec": codec, "bitrate": bitrate, "sample_rate": sample_rate, "bit_depth": bit_depth}
    except Exception:
        return {"codec": path.suffix.lower().lstrip("."), "bitrate": 0, "sample_rate": 0, "bit_depth": 0}



def _direct_library_file_match(
    *,
    title: str,
    artist: str,
    album: str,
    duration_ms: int,
    title_variants: list[str],
) -> Path | None:
    """Conservatively find an imported track directly in Helix's music mount.

    This is a fallback for Subsonic servers whose search endpoint has not caught
    up with a file that is already present/indexed. Candidate filenames are
    shortlisted first, then audio tags and duration are verified with Mutagen.
    """
    from mutagen import File

    root = Path(os.getenv("HELIX_MUSIC_LIBRARY_ROOT", "/data/music"))
    if not root.exists():
        return None

    wanted_titles = {_norm(value) for value in title_variants if value}
    wanted_artist = _norm(artist)
    wanted_album = _norm(album)
    if not wanted_titles or not wanted_artist:
        return None

    def strip_track_prefix(name: str) -> str:
        stem = Path(name).stem
        stem = re.sub(r"^\s*\d{1,3}\s*(?:[-_.]\s*|\s+)", "", stem).strip()
        return stem

    # Filename shortlist. We still verify tags below, so this can be somewhat
    # permissive without risking a wrong replacement.
    candidates: list[Path] = []
    try:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            filename_title = _norm(strip_track_prefix(path.name))
            if any(
                filename_title == wanted
                or wanted in filename_title
                or filename_title in wanted
                for wanted in wanted_titles
            ):
                candidates.append(path)
    except OSError:
        return None

    verified: list[tuple[float, Path]] = []
    for path in candidates:
        try:
            audio = File(str(path), easy=True)
            if audio is None:
                continue

            tags = audio.tags or {}
            tag_title_raw = ""
            tag_artist_raw = ""
            tag_album_raw = ""

            title_value = tags.get("title")
            artist_value = tags.get("artist")
            album_value = tags.get("album")
            if isinstance(title_value, (list, tuple)):
                tag_title_raw = str(title_value[0] if title_value else "")
            else:
                tag_title_raw = str(title_value or "")
            if isinstance(artist_value, (list, tuple)):
                tag_artist_raw = str(artist_value[0] if artist_value else "")
            else:
                tag_artist_raw = str(artist_value or "")
            if isinstance(album_value, (list, tuple)):
                tag_album_raw = str(album_value[0] if album_value else "")
            else:
                tag_album_raw = str(album_value or "")

            tag_title = _norm(tag_title_raw)
            tag_artist = _norm(tag_artist_raw)
            tag_album = _norm(tag_album_raw)
            if tag_title not in wanted_titles:
                continue

            if tag_artist == wanted_artist:
                artist_score = 40.0
            else:
                want_parts = set(wanted_artist.split())
                cand_parts = set(tag_artist.split())
                overlap = (
                    len(want_parts & cand_parts) / max(1, len(want_parts | cand_parts))
                    if want_parts and cand_parts
                    else 0.0
                )
                if overlap < 0.67:
                    continue
                artist_score = 25.0 * overlap

            score = 60.0 + artist_score
            exact_album = bool(wanted_album and tag_album and tag_album == wanted_album)
            if exact_album:
                score += 25.0

            actual_duration_ms = int(round(float(getattr(audio.info, "length", 0.0) or 0.0) * 1000))
            if duration_ms and actual_duration_ms:
                diff = abs(int(duration_ms) - actual_duration_ms)
                if diff <= 3000:
                    score += 20.0
                elif diff <= 8000:
                    score += 10.0
                elif exact_album:
                    # Exact title + artist + album is strong enough to resolve
                    # the library copy even when YTMusic and the imported release
                    # disagree on duration metadata.
                    score += 2.0
                else:
                    continue

            verified.append((score, path))
        except Exception:
            continue

    if not verified:
        return None

    verified.sort(key=lambda item: item[0], reverse=True)
    top_score = verified[0][0]
    top = [path for score, path in verified if score == top_score]
    if len(top) != 1:
        LOG.warning(
            "[quality-upgrade] direct library fallback ambiguous title=%r artist=%r matches=%s",
            title,
            artist,
            len(top),
        )
        return None

    found = top[0]
    LOG.info(
        "[quality-upgrade] resolved library copy directly from mounted library "
        "title=%r artist=%r local_path=%r score=%.1f",
        title,
        artist,
        str(found),
        top_score,
    )
    return found


def _library_file(song: dict[str, Any]) -> Path | None:
    """Map a Subsonic/Navidrome song path to Helix's mounted music library.

    Navidrome normally returns a path relative to its music folder, but setups
    differ: some return a leading slash, a duplicated music-root directory, or
    an absolute path. Try the safe/common mappings before giving up.
    """
    raw = str(song.get("path") or "").strip()
    if not raw:
        return None

    root = Path(os.getenv("HELIX_MUSIC_LIBRARY_ROOT", "/data/music"))
    try:
        root_resolved = root.resolve()
    except Exception:
        root_resolved = root

    candidates: list[Path] = []

    raw_path = Path(raw)
    if raw_path.is_absolute():
        # Only accept the absolute path directly if it is actually inside the
        # configured Helix library root.
        candidates.append(raw_path)

    rel = raw.lstrip("/\\")
    if rel:
        candidates.append(root / rel)

        # A server may expose "music/Artist/..." while Helix's mount root is
        # already "/data/music". Avoid producing "/data/music/music/...".
        parts = Path(rel).parts
        if parts and parts[0].casefold() == root.name.casefold():
            candidates.append(root.joinpath(*parts[1:]))

    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            key = str(resolved)
            if key in seen:
                continue
            seen.add(key)
            resolved.relative_to(root_resolved)
            if resolved.exists() and resolved.is_file():
                return resolved
        except Exception:
            continue

    # Last conservative fallback: Navidrome knows the basename, but the relative
    # parent path may differ from Helix's mount view. First try the exact basename,
    # then tolerate only a leading track-number prefix such as:
    #   "00 Song.opus", "01 - Song.opus", "1. Song.opus"
    basename = Path(rel).name if rel else raw_path.name
    if basename and root.exists():
        try:
            exact_matches = [p for p in root.rglob(basename) if p.is_file()]
            if len(exact_matches) == 1:
                resolved = exact_matches[0].resolve()
                resolved.relative_to(root_resolved)
                LOG.info(
                    "[quality-upgrade] resolved library file by unique basename navidrome_path=%r local_path=%r",
                    raw,
                    str(resolved),
                )
                return resolved
            if len(exact_matches) > 1:
                LOG.warning(
                    "[quality-upgrade] library path fallback ambiguous navidrome_path=%r basename=%r matches=%s",
                    raw,
                    basename,
                    len(exact_matches),
                )
            else:
                def _strip_track_prefix(name: str) -> str:
                    return re.sub(
                        r"^\s*\d{1,3}\s*(?:[-_.]\s*|\s+)",
                        "",
                        name,
                    ).strip()

                wanted = _strip_track_prefix(basename).casefold()
                prefixed_matches: list[Path] = []
                for path in root.rglob("*"):
                    if not path.is_file():
                        continue
                    if _strip_track_prefix(path.name).casefold() == wanted:
                        prefixed_matches.append(path)

                if len(prefixed_matches) == 1:
                    resolved = prefixed_matches[0].resolve()
                    resolved.relative_to(root_resolved)
                    LOG.info(
                        "[quality-upgrade] resolved library file by track-prefix fallback "
                        "navidrome_path=%r local_path=%r",
                        raw,
                        str(resolved),
                    )
                    return resolved

                if len(prefixed_matches) > 1:
                    LOG.warning(
                        "[quality-upgrade] track-prefix library fallback ambiguous "
                        "navidrome_path=%r basename=%r matches=%s",
                        raw,
                        basename,
                        len(prefixed_matches),
                    )
        except Exception:
            LOG.exception(
                "[quality-upgrade] library basename fallback failed navidrome_path=%r root=%r",
                raw,
                str(root),
            )

    LOG.warning(
        "[quality-upgrade] could not map Navidrome library path navidrome_path=%r root=%r",
        raw,
        str(root),
    )
    return None


def _fingerprint_file(path: Path) -> tuple[int, int]:
    st = path.stat()
    return int(st.st_size), int(st.st_mtime_ns)


def _cleanup_slskd_staging(downloaded: Path, download_root: Path) -> None:
    """Remove a successfully-consumed slskd file and any empty parent folders.

    Cleanup is deliberately conservative: Helix only deletes the exact file it
    successfully validated/copied into the library, then removes empty
    directories between that file and the configured slskd download root. It
    never recursively deletes non-empty folders or unrelated slskd downloads.
    """
    try:
        root = download_root.resolve()
        path = downloaded.resolve()
        path.relative_to(root)
    except Exception:
        LOG.warning(
            "[quality-upgrade] refusing slskd cleanup outside configured root file=%r root=%r",
            str(downloaded),
            str(download_root),
        )
        return

    try:
        path.unlink(missing_ok=True)
        LOG.info(
            "[quality-upgrade] removed consumed slskd staging file path=%r",
            str(path),
        )
    except OSError:
        LOG.exception(
            "[quality-upgrade] could not remove consumed slskd staging file path=%r",
            str(path),
        )
        return

    parent = path.parent
    while parent != root:
        try:
            parent.rmdir()
            LOG.info(
                "[quality-upgrade] removed empty slskd staging directory path=%r",
                str(parent),
            )
        except OSError:
            # Directory is non-empty, disappeared, or cannot be removed. Stop
            # here; never delete unrelated contents to force cleanup.
            break
        parent = parent.parent


def _download_match_snapshot(download_root: Path, candidate: SlskdCandidate) -> dict[str, tuple[int, int]]:
    """Snapshot existing files with the candidate basename before enqueue.

    slskd normally stores downloads under a directory derived from the remote
    path, not the peer username. Keeping a snapshot lets us distinguish the
    newly downloaded file from an older file with the same basename.
    """
    basename = Path(candidate.filename.replace("\\", "/")).name
    out: dict[str, tuple[int, int]] = {}
    if not basename or not download_root.exists():
        return out
    try:
        for path in download_root.rglob(basename):
            try:
                if not path.is_file():
                    continue
                st = path.stat()
                out[str(path)] = (int(st.st_size), int(st.st_mtime_ns))
            except OSError:
                continue
    except OSError:
        pass
    return out


def _find_download_file_once(
    download_root: Path,
    candidate: SlskdCandidate,
    before: dict[str, tuple[int, int]] | None = None,
) -> Path | None:
    """Return a completed matching staging file if one is currently present."""
    basename = Path(candidate.filename.replace("\\", "/")).name
    if not basename:
        return None

    before = before or {}
    expected_size = int(candidate.size or 0)

    matches: list[Path] = []
    try:
        matches = [p for p in download_root.rglob(basename) if p.is_file()]
        # slskd may avoid overwriting an older staging file by appending a
        # numeric suffix such as " (1)". Accept that variant only when the
        # extension and expected byte size still match the selected candidate.
        if not matches:
            wanted = Path(basename)
            for p in download_root.rglob(f"*{wanted.suffix}"):
                if not p.is_file():
                    continue
                if p.stem == wanted.stem or re.fullmatch(re.escape(wanted.stem) + r" \(\d+\)", p.stem):
                    matches.append(p)
    except OSError:
        matches = []

    ready: list[tuple[int, int, int, Path]] = []
    for path in matches:
        try:
            st = path.stat()
        except OSError:
            continue

        size = int(st.st_size)
        mtime_ns = int(st.st_mtime_ns)
        prior = before.get(str(path))
        changed = prior is None or prior != (size, mtime_ns)
        exact_size = expected_size > 0 and size == expected_size
        size_ok = expected_size <= 0 or exact_size
        reusable_existing = prior is not None and exact_size

        if size_ok and (changed or reusable_existing):
            ready.append(
                (
                    2 if exact_size else 1,
                    1 if changed else 0,
                    mtime_ns,
                    path,
                )
            )

    if not ready:
        return None

    ready.sort(key=lambda row: (row[0], row[1], row[2]), reverse=True)
    return ready[0][3]


async def _wait_for_download_file(
    download_root: Path,
    candidate: SlskdCandidate,
    timeout_s: int = 900,
    before: dict[str, tuple[int, int]] | None = None,
) -> Path | None:
    """Compatibility filesystem watcher used by restart recovery."""
    basename = Path(candidate.filename.replace("\\", "/")).name
    if not basename:
        return None

    before = before or {}
    LOG.info(
        "[quality-upgrade] watching slskd downloads root=%r exists=%s basename=%r expected_size=%s preexisting_matches=%s",
        str(download_root),
        download_root.exists(),
        basename,
        int(candidate.size or 0),
        len(before),
    )
    deadline = asyncio.get_running_loop().time() + max(1, int(timeout_s))
    while asyncio.get_running_loop().time() < deadline:
        found = _find_download_file_once(download_root, candidate, before)
        if found is not None:
            LOG.info(
                "[quality-upgrade] slskd download materialized user=%r file=%r local_path=%r",
                candidate.username,
                candidate.filename,
                str(found),
            )
            return found
        await asyncio.sleep(3)
    return None


_TERMINAL_TRANSFER_FAILURE_WORDS = (
    "rejected",
    "timedout",
    "timed out",
    "errored",
    "error",
    "failed",
    "cancelled",
    "canceled",
)
_TRANSFER_SUCCESS_WORDS = ("completed, succeeded", "completed succeeded", "succeeded")
_REMOTE_QUEUE_WORDS = ("queued, remotely", "queued remotely", "remotely queued")
_LOCAL_QUEUE_WORDS = ("queued, locally", "queued locally", "locally queued")
_ACTIVE_TRANSFER_WORDS = (
    "inprogress",
    "in progress",
    "requested",
    "initializing",
)


def _transfer_state(transfer: dict | None) -> str:
    if not transfer:
        return ""
    return str(transfer.get("state") or transfer.get("status") or "").strip().lower()


async def _monitor_download_transfer(
    *,
    slskd: SlskdClient,
    download_root: Path,
    candidate: SlskdCandidate,
    before: dict[str, tuple[int, int]] | None,
    total_timeout_s: int,
    queued_timeout_s: int = 90,
    job_id: str | None = None,
) -> tuple[Path | None, str, dict | None]:
    """Monitor both slskd state and the shared staging directory.

    A queued peer is not treated as an active download. If it never begins
    within ``queued_timeout_s``, the caller can cancel it and try another strong
    candidate instead of burning the full download timeout.
    """
    loop = asyncio.get_running_loop()
    started = loop.time()
    deadline = started + max(30, int(total_timeout_s))
    queued_since: float | None = None
    missing_since: float | None = None
    succeeded_since: float | None = None
    last_state = ""
    last_transfer: dict | None = None

    while loop.time() < deadline:
        found = _find_download_file_once(download_root, candidate, before)
        if found is not None:
            LOG.info(
                "[quality-upgrade] slskd download materialized user=%r file=%r local_path=%r",
                candidate.username,
                candidate.filename,
                str(found),
            )
            return found, "completed", last_transfer

        transfer = await slskd.find_download_transfer(candidate)
        now = loop.time()
        if transfer is None:
            if missing_since is None:
                missing_since = now
            elif now - missing_since >= 15:
                return None, "transfer disappeared from slskd", last_transfer
            await asyncio.sleep(2)
            continue

        last_transfer = transfer
        missing_since = None
        state = _transfer_state(transfer)
        if state != last_state:
            LOG.info(
                "[quality-upgrade] slskd transfer state user=%r file=%r state=%r",
                candidate.username,
                candidate.filename,
                state,
            )
            last_state = state

        if any(word in state for word in _TERMINAL_TRANSFER_FAILURE_WORDS):
            return None, f"transfer ended in {state or 'a failed state'}", transfer

        if any(word in state for word in _TRANSFER_SUCCESS_WORDS):
            if succeeded_since is None:
                succeeded_since = now
            # slskd may mark success just before the final rename/move becomes
            # visible through the shared mount. Give the filesystem a short grace
            # period, but do not wait the full transfer timeout.
            if now - succeeded_since >= 20:
                return None, "slskd reported success but the completed file never appeared", transfer
            await asyncio.sleep(1)
            continue

        is_queued = (
            any(word in state for word in _REMOTE_QUEUE_WORDS)
            or any(word in state for word in _LOCAL_QUEUE_WORDS)
            or state == "queued"
        )
        if is_queued:
            if job_id:
                _set_transfer_job_status(job_id, "waiting_peer")
            if queued_since is None:
                queued_since = now
            elif now - queued_since >= max(15, int(queued_timeout_s)):
                return None, f"peer remained queued for {int(now - queued_since)} seconds", transfer
        else:
            # Once the transfer actually begins, stop applying the short queue
            # deadline. It can use the normal overall download timeout.
            if job_id and any(word in state for word in _ACTIVE_TRANSFER_WORDS):
                _set_transfer_job_status(job_id, "downloading")
            queued_since = None

        await asyncio.sleep(2)

    return None, f"download exceeded {int(total_timeout_s)} second timeout", last_transfer



def _copy_original_tags(original: Path, target: Path, job: QualityUpgradeJob) -> None:
    """Preserve the current library copy's complete metadata on the upgrade.

    Navidrome can mark a file missing or split an album when a file is renamed
    and its identifying tags change at the same time. The existing Helix-owned
    library file is therefore the canonical metadata source.
    """
    from mutagen import File

    source_raw = File(str(original), easy=False)
    target_raw = File(str(target), easy=False)
    if target_raw is None:
        raise RuntimeError("Downloaded Soulseek file is not a supported audio file")

    copied_raw = False
    if source_raw is not None and source_raw.tags is not None:
        if target_raw.tags is None:
            try:
                target_raw.add_tags()
            except Exception:
                pass

        if target_raw.tags is not None:
            try:
                target_raw.tags.clear()
                for key in source_raw.tags.keys():
                    value = source_raw.tags[key]
                    try:
                        target_raw.tags[key] = list(value) if isinstance(value, (list, tuple)) else value
                    except Exception:
                        continue
                target_raw.save()
                copied_raw = True
            except Exception:
                LOG.exception(
                    "[quality-upgrade] raw tag copy failed original=%r target=%r; falling back to easy tags",
                    str(original),
                    str(target),
                )

    source_easy = File(str(original), easy=True)
    target_easy = File(str(target), easy=True)
    if target_easy is None:
        raise RuntimeError("Downloaded Soulseek file is not a supported audio file")
    if target_easy.tags is None:
        try:
            target_easy.add_tags()
        except Exception:
            pass

    if source_easy is not None and source_easy.tags is not None and target_easy.tags is not None:
        if not copied_raw:
            try:
                target_easy.tags.clear()
            except Exception:
                pass
        for key, values in source_easy.tags.items():
            try:
                target_easy[key] = list(values) if isinstance(values, (list, tuple)) else [str(values)]
            except Exception:
                continue

    def ensure(key: str, value: str) -> None:
        if not value or target_easy.tags is None:
            return
        try:
            current = target_easy.get(key)
        except Exception:
            current = None
        if current:
            return
        try:
            target_easy[key] = [value]
        except Exception:
            pass

    # Only fill missing essentials. Existing album identity is preserved exactly,
    # including date/year, discnumber, MusicBrainz IDs, compilation flags,
    # albumartist, sort tags, genres, and beets metadata.
    ensure("title", job.title)
    ensure("artist", job.artist)
    ensure("album", job.album)
    ensure("albumartist", job.album_artist or job.artist)
    if job.track_no:
        ensure("tracknumber", str(job.track_no))

    target_easy.save()



async def _finish_downloaded_upgrade(
    *,
    db: Session,
    job: QualityUpgradeJob,
    sub: Any,
    downloaded: Path,
    download_root: Path,
) -> None:
    """Finish a persisted quality upgrade from its downloaded staging file.

    This function is deliberately restart-safe: all destructive work happens
    only after the downloaded file has been rediscovered and validated.
    """
    job.status = "validating"
    job.updated_at = datetime.utcnow()
    _commit_quality_change(db)

    original = Path(job.original_path)
    if original.exists():
        _copy_original_tags(original, downloaded, job)

    new_info = _audio_info(downloaded)
    settings_db = SessionLocal()
    try:
        policy_settings = get_settings(settings_db)
    finally:
        settings_db.close()
    if not _quality_info_meets_policy(new_info, policy_settings):
        raise RuntimeError("Selected Soulseek result does not meet the configured quality policy")

    # Re-check ownership immediately before destructive replacement.
    if not original.exists():
        # A restart may have happened after os.replace but before the DB status
        # was committed. If the expected upgraded sibling is already present,
        # treat that as a resumable replacement instead of "external".
        expected_target = original.with_suffix(downloaded.suffix.lower())
        if expected_target.exists():
            existing_info = _audio_info(expected_target)
            if str(existing_info["codec"]).lower() in {"flac", "alac", "wav", "aiff", "aif"}:
                job.library_path = str(expected_target)
                job.current_codec = str(existing_info["codec"])
                job.current_bitrate = int(existing_info["bitrate"])
                job.current_sample_rate = int(existing_info["sample_rate"])
                job.current_bit_depth = int(existing_info["bit_depth"])
                job.status = "upgraded"
                job.completion_source = "slskd"
                job.upgraded_at = job.upgraded_at or datetime.utcnow()
                job.updated_at = datetime.utcnow()
                job.next_search_at = None
                job.last_error = ""
                _commit_quality_change(db)
                _cleanup_slskd_staging(downloaded, download_root)
                await sub.start_scan()
                return

        job.status = "externally_modified"
        job.updated_at = datetime.utcnow()
        _commit_quality_change(db)
        return

    size, mtime = _fingerprint_file(original)
    meta = _job_metadata(db, job.id)
    ownership_changed = False
    if size != job.original_size or mtime != job.original_mtime_ns:
        if meta.original_sha256:
            try:
                ownership_changed = _sha256_file(original) != meta.original_sha256
            except Exception:
                ownership_changed = size != job.original_size
        else:
            ownership_changed = size != job.original_size

    if ownership_changed:
        job.status = "externally_modified"
        job.updated_at = datetime.utcnow()
        _record_event(db, job.id, "ownership_changed", "Library copy changed outside Helix before replacement")
        _commit_quality_change(db)
        return

    if size != job.original_size or mtime != job.original_mtime_ns:
        job.original_size = size
        job.original_mtime_ns = mtime
        job.updated_at = datetime.utcnow()
        _commit_quality_change(db)

    job.status = "replacing"
    job.updated_at = datetime.utcnow()
    _commit_quality_change(db)

    target = original.with_suffix(downloaded.suffix.lower())
    temp_target = target.with_name(target.name + ".helix-upgrade")
    shutil.copy2(downloaded, temp_target)
    os.replace(temp_target, target)
    if target != original:
        original.unlink(missing_ok=True)

    job.library_path = str(target)
    job.current_codec = str(new_info["codec"])
    job.current_bitrate = int(new_info["bitrate"])
    job.current_sample_rate = int(new_info["sample_rate"])
    job.current_bit_depth = int(new_info["bit_depth"])
    meta = _job_metadata(db, job.id)
    try:
        meta.current_sha256 = _sha256_file(target)
    except Exception:
        LOG.exception("[quality-upgrade] could not fingerprint upgraded file job=%s path=%r", job.id, str(target))
    meta.updated_at = datetime.utcnow()
    job.status = "upgraded"
    job.completion_source = "slskd"
    job.upgraded_at = datetime.utcnow()
    job.updated_at = datetime.utcnow()
    job.next_search_at = None
    job.last_error = ""

    body = (
        f"{job.artist} — {job.title}\n"
        f"{job.original_codec.upper() or 'Original'} → "
        f"{job.current_codec.upper() or 'Lossless'}\n"
        "Source: slskd"
    )
    db.add(AdminNotification(
        kind="quality_upgrade",
        title="Track quality upgraded",
        body=body,
        data_json=json.dumps({
            "quality_upgrade_job_id": job.id,
            "artist": job.artist,
            "title": job.title,
            "before": {
                "codec": job.original_codec,
                "bitrate": job.original_bitrate,
                "sample_rate": job.original_sample_rate,
                "bit_depth": job.original_bit_depth,
            },
            "after": {
                "codec": job.current_codec,
                "bitrate": job.current_bitrate,
                "sample_rate": job.current_sample_rate,
                "bit_depth": job.current_bit_depth,
            },
            "match_confidence": job.best_match_score,
        }),
    ))
    _record_event(db, job.id, "upgraded", "Track quality upgraded from Helix-owned source", {
        "source": "slskd",
        "path": str(target),
        "match_confidence": float(job.best_match_score or 0.0),
    })
    _commit_quality_change(db)

    # slskd's API transfer history is intentionally untouched; this removes only
    # the consumed staging file after the library replacement is durable.
    _cleanup_slskd_staging(downloaded, download_root)
    await sub.start_scan()
    try:
        await sub.wait_for_scan_complete(timeout_s=120, poll_s=1.0)
    except Exception:
        LOG.exception(
            "[quality-upgrade] Navidrome scan wait failed after replacement job=%s",
            job.id,
        )


def _candidate_from_persisted_job(job: QualityUpgradeJob) -> SlskdCandidate | None:
    username = str(job.slskd_username or "").strip()
    filename = str(job.slskd_filename or "").strip()
    size = int(job.slskd_size or 0)
    if not username or not filename:
        return None
    return SlskdCandidate(
        username=username,
        filename=filename,
        size=size,
    )


def _schedule_no_match(job: QualityUpgradeJob) -> None:
    job.attempts += 1
    job.last_search_at = datetime.utcnow()
    job.last_error = ""
    if job.attempts <= len(_RETRY_DELAYS):
        job.status = "no_match"
        job.next_search_at = datetime.utcnow() + _RETRY_DELAYS[job.attempts - 1]
    else:
        job.status = "dormant"
        job.next_search_at = None
    job.updated_at = datetime.utcnow()


async def _process_job(job_id: str) -> None:
    db = SessionLocal()
    sub = None
    slskd = None
    try:
        job = db.get(QualityUpgradeJob, job_id)
        if not job:
            return
        settings = get_settings(db)

        if job.status == "reverting":
            # Recovery for a Helix restart during a revert. The atomic file swap
            # happens before the DB commit, so filesystem state tells us which
            # side of that boundary we were on.
            original = Path(job.original_path or "")
            upgraded = Path(job.library_path or "")
            if original.exists() and (not upgraded.exists() or original == upgraded):
                info = _audio_info(original)
                size, mtime = _fingerprint_file(original)
                meta = _job_metadata(db, job.id)
                job.library_path = str(original)
                job.original_size = size
                job.original_mtime_ns = mtime
                job.current_codec = str(info.get("codec") or "")
                job.current_bitrate = int(info.get("bitrate") or 0)
                job.current_sample_rate = int(info.get("sample_rate") or 0)
                job.current_bit_depth = int(info.get("bit_depth") or 0)
                job.status = "reverted"
                job.reverted_at = job.reverted_at or datetime.utcnow()
                job.completion_source = "user_revert"
                job.next_search_at = None
                job.last_error = ""
                try:
                    digest = _sha256_file(original)
                    meta.original_sha256 = digest
                    meta.current_sha256 = digest
                except Exception:
                    pass
                meta.updated_at = datetime.utcnow()
                _record_event(db, job.id, "revert_recovered", "Recovered a completed revert after Helix restart")
                _commit_quality_change(db)
            else:
                job.status = "upgraded"
                job.last_error = "Revert was interrupted by a Helix restart before replacement; retry the revert."
                job.updated_at = datetime.utcnow()
                _record_event(db, job.id, "revert_interrupted", job.last_error)
                _commit_quality_change(db)
            return

        if not settings.get("slskd_enabled"):
            return

        slskd_url = str(settings.get("slskd_url") or "").strip()
        api_key = str(settings.get("slskd_api_key") or "").strip()
        download_root_raw = str(settings.get("slskd_downloads_path") or "").strip()
        if not slskd_url or not api_key or not download_root_raw:
            job.status = "failed"
            job.last_error = "slskd is enabled but URL, API key, or downloads path is missing"
            job.updated_at = datetime.utcnow()
            _commit_quality_change(db)
            return

        download_root = Path(download_root_raw)

        sub = _subsonic(settings)
        if sub is None:
            return

        # The library may have changed since another Helix request cached a miss.
        # Quality-upgrade discovery must always start from a fresh view because it
        # specifically waits for a just-imported track to become visible.
        try:
            sub.invalidate_song_resolve_cache(
                title=str(job.title or ""),
                artist=str(job.artist or ""),
                negative_only=True,
            )
        except Exception:
            # Older/alternate Subsonic client implementations can still proceed;
            # wait_for_song_best below is itself uncached.
            pass

        # Do not search until the reliable YTMusic copy is visible in Subsonic.
        # Reuse an already-resolved Subsonic ID first; this is authoritative and
        # avoids depending on search ranking every time the worker revisits a job.
        song = None
        known_subsonic_id = str(job.subsonic_song_id or "").strip()
        if known_subsonic_id:
            try:
                song = await sub.get_song(known_subsonic_id)
            except Exception:
                song = None

        # Library metadata frequently contains version text that is absent from
        # the imported file (or vice versa), e.g. "Bad Moon Rising (Mono Single)"
        # vs "Bad Moon Rising". Try conservative title variants before assuming
        # the YTMusic copy is missing.
        def _library_title_variants(value: str) -> list[str]:
            value = str(value or "").strip()
            raw = [value]
            # Strip trailing parenthetical/bracketed release/version annotations.
            stripped = re.sub(r"\s*[\(\[][^\)\]]+[\)\]]\s*$", "", value).strip()
            if stripped and stripped != value:
                raw.append(stripped)
            # Also strip common trailing dash-version forms without touching the
            # core title itself.
            dash_stripped = re.sub(
                r"\s+[-–—]\s+(mono|stereo|single|album|radio|remaster(?:ed)?|version|edit|mix).*$",
                "",
                value,
                flags=re.I,
            ).strip()
            if dash_stripped and dash_stripped != value:
                raw.append(dash_stripped)

            out: list[str] = []
            seen: set[str] = set()
            for item in raw:
                key = _norm(item)
                if item and key and key not in seen:
                    seen.add(key)
                    out.append(item)
            return out

        title_variants = _library_title_variants(job.title)

        # Library resolution can spend many seconds in Subsonic. Detach the
        # already-loaded job and release SQLite before any network waits.
        db.close()
        db = None

        if not song:
            for title_variant in title_variants:
                song = await sub.wait_for_song_best(
                    title=title_variant,
                    artist=job.artist,
                    duration_ms=job.duration_ms or None,
                    timeout_s=8,
                    poll_s=2.0,
                )
                if song:
                    if title_variant != job.title:
                        LOG.info(
                            "[quality-upgrade] job=%s resolved library copy using title variant original=%r variant=%r id=%r",
                            job.id,
                            job.title,
                            title_variant,
                            str(song.get("id") or ""),
                        )
                    break

        if not song:
            # Some Subsonic servers rank search3 results oddly enough that an
            # exact library track can fall outside the normal resolver result
            # set. Search all safe title variants and apply Helix's own
            # conservative identity check before declaring the copy unresolved.
            try:
                want_titles = {_norm(value) for value in title_variants if value}
                want_artist = _norm(job.artist)
                want_duration = int(job.duration_ms or 0)
                best_song = None
                best_score = -1.0

                broad_candidates: list[dict] = []
                seen_song_ids: set[str] = set()
                for title_variant in title_variants:
                    res = await sub.search3(title_variant, song_count=500)
                    for candidate_song in (res.get("song") or []):
                        sid = str(candidate_song.get("id") or "")
                        dedupe_key = sid or f"{candidate_song.get('artist')}|{candidate_song.get('title')}|{candidate_song.get('duration')}"
                        if dedupe_key in seen_song_ids:
                            continue
                        seen_song_ids.add(dedupe_key)
                        broad_candidates.append(candidate_song)

                for candidate_song in broad_candidates:
                    cand_title = _norm(str(candidate_song.get("title") or ""))
                    cand_artist = _norm(str(candidate_song.get("artist") or ""))
                    if not cand_title or not cand_artist:
                        continue

                    # Candidate title may match either the source title or a safe
                    # version-stripped variant. Artist/duration checks still keep
                    # this conservative.
                    if cand_title not in want_titles:
                        continue

                    if cand_artist == want_artist:
                        artist_score = 40.0
                    else:
                        want_parts = set(want_artist.split())
                        cand_parts = set(cand_artist.split())
                        overlap = (
                            len(want_parts & cand_parts) / max(1, len(want_parts | cand_parts))
                            if want_parts and cand_parts
                            else 0.0
                        )
                        if overlap < 0.67:
                            continue
                        artist_score = 25.0 * overlap

                    score = 60.0 + artist_score

                    cand_album = _norm(str(candidate_song.get("album") or ""))
                    exact_album = bool(job.album and cand_album and cand_album == _norm(job.album))
                    if exact_album:
                        score += 25.0

                    cand_duration = int(candidate_song.get("duration") or 0) * 1000
                    if want_duration and cand_duration:
                        diff = abs(want_duration - cand_duration)
                        if diff <= 3000:
                            score += 20.0
                        elif diff <= 8000:
                            score += 10.0
                        elif exact_album:
                            # Exact title/artist/album is enough to identify the
                            # imported track even if duration metadata differs.
                            score += 2.0
                        else:
                            continue

                    if score > best_score:
                        best_score = score
                        best_song = candidate_song

                if best_song is not None:
                    song = best_song
                    LOG.info(
                        "[quality-upgrade] job=%s resolved library copy via broad Subsonic fallback id=%r score=%.1f",
                        job.id,
                        str(song.get("id") or ""),
                        best_score,
                    )
            except Exception:
                LOG.exception(
                    "[quality-upgrade] broad Subsonic fallback failed job=%s",
                    job.id,
                )

        direct_library_path: Path | None = None
        if not song:
            direct_library_path = _direct_library_file_match(
                title=str(job.title or ""),
                artist=str(job.artist or ""),
                album=str(job.album or ""),
                duration_ms=int(job.duration_ms or 0),
                title_variants=title_variants,
            )
            if direct_library_path is not None:
                root = Path(os.getenv("HELIX_MUSIC_LIBRARY_ROOT", "/data/music"))
                try:
                    rel = direct_library_path.resolve().relative_to(root.resolve())
                    song = {"path": str(rel)}
                except Exception:
                    song = {"path": str(direct_library_path)}

        # Re-open only when state must be persisted. All Subsonic waits above
        # ran without holding a pooled DB connection.
        db = SessionLocal()
        job = db.get(QualityUpgradeJob, job_id)
        if job is None:
            return

        if not song:
            # Do not leave a permanently-unresolvable job looking like it is
            # simply "waiting" forever. Retry automatically for a while, then
            # surface a normal retryable failure.
            _schedule_library_resolution_retry(
                job,
                "Library copy exists but could not be resolved in Subsonic/Navidrome",
            )
            job.updated_at = datetime.utcnow()
            _commit_quality_change(db)
            LOG.warning(
                "[quality-upgrade] job=%s library copy unresolved attempt=%s",
                job.id,
                job.attempts,
            )
            return

        resolved_subsonic_id = str(song.get("id") or "").strip()
        if resolved_subsonic_id and job.subsonic_song_id != resolved_subsonic_id:
            job.subsonic_song_id = resolved_subsonic_id
            job.updated_at = datetime.utcnow()
            _commit_quality_change(db)

        current_path = direct_library_path if direct_library_path is not None else _library_file(song)
        if current_path is None or not current_path.exists():
            # A valid Navidrome row can still carry a path that doesn't map
            # cleanly into Helix's mount. Before treating that as unresolved,
            # identify the same recording directly from tags in the mounted
            # library.
            direct_library_path = _direct_library_file_match(
                title=str(job.title or ""),
                artist=str(job.artist or ""),
                album=str(job.album or ""),
                duration_ms=int(job.duration_ms or 0),
                title_variants=title_variants,
            )
            if direct_library_path is not None:
                current_path = direct_library_path

        if current_path is None or not current_path.exists():
            # Navidrome can expose the DB row slightly before the file mapping is
            # usable from Helix. This used to retry forever without changing the
            # visible state. Count mapping misses and eventually surface a
            # retryable failure instead.
            _schedule_library_resolution_retry(
                job,
                (
                    "Navidrome found the track, but Helix could not map its library "
                    "path to a mounted file. Check HELIX_MUSIC_LIBRARY_ROOT."
                ),
            )
            job.updated_at = datetime.utcnow()
            _commit_quality_change(db)
            LOG.warning(
                "[quality-upgrade] job=%s Navidrome song resolved but local file mapping failed "
                "attempt=%s navidrome_path=%r library_root=%r",
                job.id,
                job.attempts,
                str(song.get("path") or ""),
                os.getenv("HELIX_MUSIC_LIBRARY_ROOT", "/data/music"),
            )
            return

        if not job.original_path:
            size, mtime = _fingerprint_file(current_path)
            info = _audio_info(current_path)
            job.subsonic_song_id = str(song.get("id") or "")
            job.library_path = str(current_path)
            job.original_path = str(current_path)
            job.original_size = size
            job.original_mtime_ns = mtime
            job.original_codec = str(info["codec"])
            job.original_bitrate = int(info["bitrate"])
            job.original_sample_rate = int(info["sample_rate"])
            job.original_bit_depth = int(info["bit_depth"])
            job.current_codec = job.original_codec
            job.current_bitrate = job.original_bitrate
            job.current_sample_rate = job.original_sample_rate
            job.current_bit_depth = job.original_bit_depth
            meta = _job_metadata(db, job.id)
            meta.provenance = meta.provenance or "helix_imported"
            try:
                meta.original_sha256 = _sha256_file(current_path)
                meta.current_sha256 = meta.original_sha256
            except Exception:
                LOG.exception("[quality-upgrade] could not fingerprint original job=%s path=%r", job.id, str(current_path))
            meta.updated_at = datetime.utcnow()
            _record_event(db, job.id, "library_copy_resolved", "Helix-owned library copy resolved", {"path": str(current_path), "codec": job.original_codec})
            if str(job.original_codec or "").lower() in {"flac", "alac", "wav", "aiff", "aif"} and not bool(settings.get("quality_upgrade_replace_lossless", False)):
                job.status = "satisfied"
                job.completion_source = "helix_library"
                job.next_search_at = None
                _record_event(db, job.id, "satisfied", "Existing Helix-owned file already satisfies the configured lossless policy")
            _commit_quality_change(db)
            if job.status == "satisfied":
                return
        else:
            # If the user replaced/touched Helix's original file, respect it.
            original = Path(job.original_path)
            if not original.exists():
                # Same-stem replacement (e.g. user manually supplied FLAC).
                siblings = list(original.parent.glob(original.stem + ".*"))
                audio_siblings = [p for p in siblings if p.is_file() and p.suffix.lower() in {".flac", ".alac", ".mp3", ".m4a", ".opus", ".ogg", ".wav"}]
                if audio_siblings:
                    replacement = max(audio_siblings, key=lambda p: p.stat().st_mtime_ns)
                    info = _audio_info(replacement)
                    if str(info["codec"]).lower() in {"flac", "alac", "wav", "aiff", "aif"}:
                        job.status = "satisfied"
                        job.completion_source = "external"
                    else:
                        job.status = "externally_modified"
                    job.library_path = str(replacement)
                    job.current_codec = str(info["codec"])
                    job.current_bitrate = int(info["bitrate"])
                    job.current_sample_rate = int(info["sample_rate"])
                    job.current_bit_depth = int(info["bit_depth"])
                    job.updated_at = datetime.utcnow()
                    _commit_quality_change(db)
                    return

            if original.exists():
                size, mtime = _fingerprint_file(original)

                # mtime alone is not reliable evidence of a user replacement.
                # Taggers, importers, filesystem operations, and backup/sync
                # tools can legitimately touch the timestamp while leaving the
                # actual audio file unchanged. Treat an identical-size file as
                # still owned by Helix and refresh the stored timestamp.
                #
                # A size change remains a conservative stop signal until Helix
                # gains an audio-content fingerprint that can distinguish
                # metadata-only edits from a replaced recording.
                meta = _job_metadata(db, job.id)
                fingerprint_changed = False
                if size != job.original_size or mtime != job.original_mtime_ns:
                    if meta.original_sha256:
                        try:
                            fingerprint_changed = _sha256_file(original) != meta.original_sha256
                        except Exception:
                            # If the strong check itself fails, remain conservative.
                            fingerprint_changed = size != job.original_size
                    else:
                        fingerprint_changed = size != job.original_size

                if fingerprint_changed:
                    job.status = "externally_modified"
                    job.updated_at = datetime.utcnow()
                    _record_event(db, job.id, "ownership_changed", "Library copy changed outside Helix")
                    _commit_quality_change(db)
                    return

                if size != job.original_size or mtime != job.original_mtime_ns:
                    # Metadata-only edits / timestamp changes can alter file size
                    # without changing the recording. Refresh the bookkeeping
                    # when the strong audio file hash still matches.
                    job.original_size = size
                    job.original_mtime_ns = mtime
                    job.updated_at = datetime.utcnow()
                    _commit_quality_change(db)

        # Resume interrupted transient jobs from persisted DB state. The selected
        # Soulseek peer, remote filename, expected size, and current stage were
        # committed before the original await, so a Helix restart does not lose
        # the identity of the in-flight/completed transfer.
        transient_resume_states = {"waiting_peer", "downloading", "validating", "tagging", "replacing"}
        if job.status in transient_resume_states:
            persisted_candidate = _candidate_from_persisted_job(job)
            if persisted_candidate is None:
                # Searching can safely be repeated, but a later stage without a
                # persisted candidate is incomplete state. Return it to pending.
                LOG.warning(
                    "[quality-upgrade] job=%s transient status=%s had no persisted "
                    "slskd candidate; restarting search",
                    job.id,
                    job.status,
                )
                job.status = "pending"
                job.next_search_at = None
                job.updated_at = datetime.utcnow()
                _commit_quality_change(db)
                return

            slskd = SlskdClient(
                slskd_url,
                api_key,
                timeout_s=float(settings.get("slskd_timeout_s") or 20),
            )

            LOG.info(
                "[quality-upgrade] resuming persisted job=%s status=%s user=%r "
                "file=%r expected_size=%s",
                job.id,
                job.status,
                persisted_candidate.username,
                persisted_candidate.filename,
                persisted_candidate.size,
            )

            # First rediscover an already-completed staging file. Passing an
            # empty snapshot intentionally allows an exact-size existing file to
            # be reused immediately after restart.
            downloaded = await _wait_for_download_file(
                download_root,
                persisted_candidate,
                timeout_s=2,
                before={},
            )

            if downloaded is None:
                # If the file is not complete yet, see whether slskd still owns
                # the transfer. slskd is a separate service and normally keeps
                # downloading while Helix restarts.
                transfer = await slskd.find_download_transfer(persisted_candidate)
                transfer_state = ""
                if transfer is not None:
                    transfer_state = str(
                        transfer.get("state") or transfer.get("status") or ""
                    ).lower()

                terminal_failure_words = (
                    "rejected", "timedout", "timed out", "errored", "error",
                    "failed", "cancelled", "canceled",
                )

                if transfer is not None and not any(
                    word in transfer_state for word in terminal_failure_words
                ):
                    job.status = "downloading"
                    job.updated_at = datetime.utcnow()
                    _commit_quality_change(db)
                    db.close()
                    db = None
                    downloaded, resume_reason, resume_transfer = await _monitor_download_transfer(
                        slskd=slskd,
                        download_root=download_root,
                        candidate=persisted_candidate,
                        before={},
                        total_timeout_s=int(settings.get("slskd_download_timeout_s") or 900),
                        queued_timeout_s=90,
                        job_id=job_id,
                    )
                    db = SessionLocal()
                    job = db.get(QualityUpgradeJob, job_id)
                    if job is None:
                        return
                    if downloaded is None:
                        await slskd.cancel_download_transfer(
                            persisted_candidate,
                            resume_transfer,
                        )
                        LOG.warning(
                            "[quality-upgrade] persisted peer stalled job=%s user=%r file=%r reason=%s; returning to search",
                            job.id,
                            persisted_candidate.username,
                            persisted_candidate.filename,
                            resume_reason,
                        )
                        job.status = "pending"
                        job.next_search_at = None
                        job.last_error = f"Previous Soulseek peer stalled: {resume_reason}. Searching for another source."
                        job.updated_at = datetime.utcnow()
                        _commit_quality_change(db)
                        return
                else:
                    # The transfer disappeared (for example slskd was also
                    # restarted) but we still know exactly what Helix selected.
                    # Requeue that same candidate instead of performing a new
                    # Soulseek search and potentially selecting another recording.
                    LOG.info(
                        "[quality-upgrade] persisted transfer missing/terminal for "
                        "job=%s state=%r; requeueing same candidate",
                        job.id,
                        transfer_state,
                    )
                    job.status = "downloading"
                    job.updated_at = datetime.utcnow()
                    _commit_quality_change(db)
                    db.close()
                    db = None
                    await slskd.enqueue_download(persisted_candidate)
                    transfer = await slskd.wait_for_download_transfer(
                        persisted_candidate,
                        timeout_s=8.0,
                    )
                    if transfer is None:
                        raise RuntimeError(
                            "Could not restore persisted slskd transfer after restart"
                        )
                    downloaded, resume_reason, resume_transfer = await _monitor_download_transfer(
                        slskd=slskd,
                        download_root=download_root,
                        candidate=persisted_candidate,
                        before={},
                        total_timeout_s=int(settings.get("slskd_download_timeout_s") or 900),
                        queued_timeout_s=90,
                        job_id=job_id,
                    )
                    db = SessionLocal()
                    job = db.get(QualityUpgradeJob, job_id)
                    if job is None:
                        return
                    if downloaded is None:
                        await slskd.cancel_download_transfer(
                            persisted_candidate,
                            resume_transfer,
                        )
                        job.status = "pending"
                        job.next_search_at = None
                        job.last_error = f"Previous Soulseek peer stalled: {resume_reason}. Searching for another source."
                        job.updated_at = datetime.utcnow()
                        _commit_quality_change(db)
                        return

            if downloaded is None:
                raise RuntimeError(
                    "Persisted Soulseek download did not complete before timeout"
                )

            await _finish_downloaded_upgrade(
                db=db,
                job=job,
                sub=sub,
                downloaded=downloaded,
                download_root=download_root,
            )
            return

        # Build everything that needs ORM-backed job attributes before commit.
        # SQLAlchemy expires attributes on commit by default, so the entire
        # Soulseek search plan and a detached scoring snapshot are prepared now.
        search_artist = str(job.artist or "").strip()
        search_title = str(job.title or "").strip()
        search_album = str(job.album or "").strip()
        search_duration_ms = int(job.duration_ms or 0)

        def _clean_search_text(value: str) -> str:
            value = re.sub(r"[\\[\\]{}()]+", " ", value)
            value = re.sub(r"[-–—_:;,.!?/\\\\]+", " ", value)
            return re.sub(r"\\s+", " ", value).strip()

        artist_clean = _clean_search_text(search_artist)
        title_clean = _clean_search_text(search_title)
        album_clean = _clean_search_text(search_album)

        # Keep network searches deliberately small. Soulseek searches can each
        # take tens of seconds, so Helix does a few high-value discovery queries
        # and relies on its own strict local candidate scoring for precision.
        #
        # 1. Artist + title: strongest direct recording lookup.
        # 2. Artist + album: finds album-folder shares when track search misses.
        # 3. Title only: catches oddly tagged / compilation copies.
        # 4. Artist only: broad final fallback; inspect a large result set locally.
        raw_queries = [
            " ".join(x for x in [search_artist, search_title] if x),
            " ".join(x for x in [search_artist, search_album] if x),
            search_title,
            search_artist,
        ]

        search_queries: list[str] = []
        seen_queries: set[str] = set()
        for raw_query in raw_queries:
            raw_query = re.sub(r"\\s+", " ", raw_query).strip()
            key = raw_query.casefold()
            if not raw_query or key in seen_queries:
                continue
            seen_queries.add(key)
            search_queries.append(raw_query)

        # _score_candidate only needs these track identity fields. Keeping them
        # detached prevents a DB connection being reacquired and held while the
        # next broad Soulseek query runs.
        score_track = SimpleNamespace(
            artist=search_artist,
            title=search_title,
            album=search_album,
            duration_ms=search_duration_ms,
        )

        meta = _job_metadata(db, job.id)
        resume_search_id = ""
        resume_search_query = ""
        if job.status in {"searching", "waiting_search"} and meta.slskd_search_id:
            resume_search_id = str(meta.slskd_search_id or "").strip()
            resume_search_query = str(meta.slskd_search_query or "").strip()
            LOG.info(
                "[quality-upgrade] resuming persisted Soulseek search job=%s search_id=%s query=%r",
                job.id, resume_search_id, resume_search_query,
            )
        elif job.status in {"searching", "waiting_search"}:
            LOG.info(
                "[quality-upgrade] restarting Soulseek search job=%s status=%s (no persisted search id)",
                job.id,
                job.status,
            )
        job.status = "waiting_search"
        job.updated_at = datetime.utcnow()
        _commit_quality_change(db)
        db.close()
        db = None

        slskd = SlskdClient(
            slskd_url,
            api_key,
            timeout_s=float(settings.get("slskd_timeout_s") or 20),
        )
        search_timeout = float(settings.get("slskd_search_timeout_s") or 35)
        max_results = int(settings.get("slskd_max_results") or 200)
        min_score = float(settings.get("slskd_match_threshold") or 78)

        LOG.info(
            "[quality-upgrade] job=%s Soulseek search plan queries=%r",
            job_id,
            search_queries,
        )

        candidates_by_key: dict[tuple[str, str, int], SlskdCandidate] = {}
        artist_only_keys = {
            value.casefold()
            for value in (search_artist.strip(), artist_clean.strip())
            if value.strip()
        }
        for query in search_queries:
            # Artist-only is intentionally the broadest and final fallback. Give
            # it a larger inspection window because a popular artist may expose
            # many album folders/tracks before the requested recording appears.
            query_max_results = max_results
            if query.casefold() in artist_only_keys:
                query_max_results = max(max_results, 1000)

            _set_search_job_status(job_id, "waiting_search")
            LOG.info(
                "[quality-upgrade] job=%s waiting for staggered Soulseek search start query=%r",
                job_id,
                query,
            )
            await _wait_for_search_start_slot()
            _set_search_job_status(job_id, "searching", mark_started=True)
            LOG.info(
                "[quality-upgrade] job=%s starting Soulseek search query=%r",
                job_id,
                query,
            )
            # The start gate is already released here, so another quality job
            # can begin its own search after the short interval while this one
            # continues waiting for Soulseek responses.
            active_resume_id = resume_search_id if resume_search_id and query.casefold() == resume_search_query.casefold() else ""
            try:
                batch = await slskd.search(
                    query,
                    timeout_s=search_timeout,
                    max_results=query_max_results,
                    search_id=active_resume_id,
                    on_search_started=lambda sid, q=query: _persist_active_search(job_id, sid, q),
                )
            except FileNotFoundError:
                # slskd may have pruned the persisted search while Helix was
                # offline. Start the same query again instead of skipping it.
                LOG.info(
                    "[quality-upgrade] persisted slskd search disappeared job=%s search_id=%s; starting replacement search query=%r",
                    job_id, active_resume_id, query,
                )
                batch = await slskd.search(
                    query,
                    timeout_s=search_timeout,
                    max_results=query_max_results,
                    on_search_started=lambda sid, q=query: _persist_active_search(job_id, sid, q),
                )
            resume_search_id = ""
            resume_search_query = ""
            clear_db = SessionLocal()
            try:
                _clear_active_search(clear_db, job_id)
                clear_db.commit()
            finally:
                clear_db.close()
            for candidate in batch:
                key = (
                    str(candidate.username or ""),
                    str(candidate.filename or ""),
                    int(candidate.size or 0),
                )
                candidates_by_key[key] = candidate

            scored_so_far = [
                (float(_score_candidate(score_track, candidate)), candidate)
                for candidate in candidates_by_key.values()
            ]
            strong_lossless = [
                candidate
                for score, candidate in scored_so_far
                if score >= min_score and _is_lossless(candidate)
            ]
            LOG.info(
                "[quality-upgrade] job=%s Soulseek query=%r batch=%s unique=%s strong_lossless=%s max_results=%s",
                job_id,
                query,
                len(batch),
                len(candidates_by_key),
                len(strong_lossless),
                query_max_results,
            )

            # Easy tracks stop after enough redundant high-confidence choices.
            # Hard tracks keep widening through title-only variants.
            if len(strong_lossless) >= 3:
                break

        candidates = list(candidates_by_key.values())
        scored = [(float(_score_candidate(score_track, c)), c) for c in candidates]

        db = SessionLocal()
        job = db.get(QualityUpgradeJob, job_id)
        if job is None:
            return
        if scored:
            job.best_match_score = max(s for s, _ in scored)

        LOG.info(
            "[quality-upgrade] job=%s track=%r artist=%r parsed_candidates=%s best_score=%.1f threshold=%.1f",
            job.id,
            job.title,
            job.artist,
            len(scored),
            float(job.best_match_score or 0.0),
            min_score,
        )
        for score, candidate in sorted(scored, key=lambda item: item[0], reverse=True)[:5]:
            LOG.info(
                "[quality-upgrade] candidate score=%.1f lossless=%s duration_ms=%s file=%r user=%r",
                score,
                _is_lossless(candidate),
                int(getattr(candidate, "duration_ms", 0) or 0),
                candidate.filename,
                candidate.username,
            )

        valid = [(s, c) for s, c in scored if s >= min_score and _candidate_meets_policy(c, settings, job)]
        LOG.info("[quality-upgrade] job=%s acceptable_candidates=%s", job.id, len(valid))
        if not valid:
            _schedule_no_match(job)
            _commit_quality_change(db)
            return

        # Prefer peers with free upload slots when identity confidence is equal,
        # then quality. A slightly lower-ranked peer is still retained as a
        # fallback if the first one never starts transferring.
        valid.sort(
            key=lambda item: (
                item[0],
                1 if item[1].free_upload_slots else 0,
                -int(item[1].queue_length or 0),
                _candidate_rank(item[1]),
            ),
            reverse=True,
        )

        candidates_to_try = valid[:5]
        meta = _job_metadata(db, job.id)
        meta.candidate_pool_json = json.dumps([_candidate_to_dict(candidate, score) for score, candidate in candidates_to_try])
        meta.candidate_index = 0
        meta.updated_at = datetime.utcnow()
        _record_event(db, job.id, "candidates_ranked", f"{len(candidates_to_try)} verified Soulseek candidates retained", {
            "count": len(candidates_to_try),
            "threshold": min_score,
        })
        _commit_quality_change(db)
        download_timeout_s = int(settings.get("slskd_download_timeout_s") or 900)
        failure_reasons: list[str] = []
        downloaded: Path | None = None
        chosen_candidate: SlskdCandidate | None = None

        for candidate_index, (score, candidate) in enumerate(candidates_to_try, start=1):
            job.status = "downloading"
            job.best_match_score = score
            job.slskd_username = candidate.username
            job.slskd_filename = candidate.filename
            job.slskd_size = candidate.size
            job.last_error = ""
            job.updated_at = datetime.utcnow()
            meta = _job_metadata(db, job.id)
            meta.candidate_index = candidate_index - 1
            meta.updated_at = datetime.utcnow()
            _record_event(db, job.id, "candidate_selected", f"Trying Soulseek peer {candidate.username}", {
                "candidate_index": candidate_index - 1,
                "score": float(score),
                "username": candidate.username,
                "filename": candidate.filename,
            })
            _commit_quality_change(db)
            db.close()
            db = None

            LOG.info(
                "[quality-upgrade] job=%s trying download candidate %s/%s score=%.1f free_slot=%s queue=%s user=%r file=%r",
                job_id,
                candidate_index,
                len(candidates_to_try),
                score,
                candidate.free_upload_slots,
                candidate.queue_length,
                candidate.username,
                candidate.filename,
            )

            download_snapshot = _download_match_snapshot(download_root, candidate)

            try:
                await slskd.enqueue_download(candidate)
                transfer = await slskd.wait_for_download_transfer(candidate, timeout_s=8.0)
                if transfer is None:
                    reason = "slskd accepted the request but no transfer appeared"
                    failure_reasons.append(f"{candidate.username}: {reason}")
                    LOG.warning(
                        "[quality-upgrade] job=%s candidate %s/%s unusable: %s user=%r file=%r",
                        job_id,
                        candidate_index,
                        len(candidates_to_try),
                        reason,
                        candidate.username,
                        candidate.filename,
                    )
                    continue

                transfer_state = _transfer_state(transfer)
                if any(word in transfer_state for word in _TERMINAL_TRANSFER_FAILURE_WORDS):
                    reason = f"transfer immediately entered {transfer_state or 'a failed state'}"
                    failure_reasons.append(f"{candidate.username}: {reason}")
                    LOG.warning(
                        "[quality-upgrade] job=%s candidate %s/%s unusable: %s",
                        job_id,
                        candidate_index,
                        len(candidates_to_try),
                        reason,
                    )
                    continue

                downloaded, reason, last_transfer = await _monitor_download_transfer(
                    slskd=slskd,
                    download_root=download_root,
                    candidate=candidate,
                    before=download_snapshot,
                    total_timeout_s=download_timeout_s,
                    queued_timeout_s=90,
                    job_id=job_id,
                )
                if downloaded is not None:
                    chosen_candidate = candidate
                    break

                failure_reasons.append(f"{candidate.username}: {reason}")
                event_db = SessionLocal()
                try:
                    _record_event(event_db, job_id, "candidate_failed", f"Soulseek peer {candidate.username} failed: {reason}", {"username": candidate.username, "reason": reason})
                    event_db.commit()
                finally:
                    event_db.close()
                LOG.warning(
                    "[quality-upgrade] job=%s candidate %s/%s stalled/failed: %s; trying next acceptable peer",
                    job_id,
                    candidate_index,
                    len(candidates_to_try),
                    reason,
                )
                await slskd.cancel_download_transfer(candidate, last_transfer)
            except Exception as candidate_exc:
                reason = str(candidate_exc)[:500]
                failure_reasons.append(f"{candidate.username}: {reason}")
                LOG.exception(
                    "[quality-upgrade] job=%s candidate %s/%s raised during transfer; trying next acceptable peer",
                    job_id,
                    candidate_index,
                    len(candidates_to_try),
                )
                try:
                    await slskd.cancel_download_transfer(candidate)
                except Exception:
                    pass
            finally:
                if db is None:
                    db = SessionLocal()
                    job = db.get(QualityUpgradeJob, job_id)
                    if job is None:
                        return

        if downloaded is None or chosen_candidate is None:
            detail = "; ".join(failure_reasons[-3:])
            raise RuntimeError(
                f"All {len(candidates_to_try)} acceptable Soulseek peers failed or stalled"
                + (f": {detail}" if detail else "")
            )

        await _finish_downloaded_upgrade(
            db=db,
            job=job,
            sub=sub,
            downloaded=downloaded,
            download_root=download_root,
        )

    except Exception as exc:
        LOG.exception("Quality upgrade failed job_id=%s", job_id)
        if db is None:
            db = SessionLocal()
        job = db.get(QualityUpgradeJob, job_id)
        if job:
            job.status = "failed"
            job.last_error = str(exc)[:2000]
            job.updated_at = datetime.utcnow()
            # Infrastructure failures retry sooner than true no-match.
            job.next_search_at = datetime.utcnow() + timedelta(hours=1)
            _commit_quality_change(db)
    finally:
        if slskd is not None:
            await slskd.close()
        if sub is not None:
            await sub.close()
        if db is not None:
            db.close()


async def revert_quality_upgrade_job(job_id: str, user_id: str) -> None:
    """Restore an upgraded track to a fresh Helix/YTMusic Opus copy safely.

    The upgraded file remains untouched while the fresh source downloads and is
    remuxed/tagged. Only after the replacement is fully validated do we atomically
    place the .opus file, remove the upgraded sibling, and request one Navidrome
    scan. Automatic upgrades remain suppressed afterward.
    """
    db = SessionLocal()
    try:
        job = db.get(QualityUpgradeJob, job_id)
        if job is None:
            raise LookupError("Upgrade job not found")
        if job.status != "upgraded":
            raise RuntimeError("Only upgraded tracks can be reverted")
        current_path = Path(job.library_path or "")
        original_path = Path(job.original_path or "")
        if not current_path.exists():
            raise RuntimeError("Current upgraded library file is missing")
        if not original_path.name:
            raise RuntimeError("Original Helix library path is unavailable")

        meta = _job_metadata(db, job.id)
        expected_current_hash = str(meta.current_sha256 or "")
        snapshot = {
            "yt_video_id": job.yt_video_id,
            "yt_browse_id": job.yt_browse_id,
            "title": job.title,
            "artist": job.artist,
            "album": job.album,
            "album_artist": job.album_artist,
            "art_url": job.art_url,
            "track_no": job.track_no,
            "duration_ms": job.duration_ms,
            "current_path": str(current_path),
            "original_path": str(original_path),
        }
        job.status = "reverting"
        job.last_error = ""
        job.updated_at = datetime.utcnow()
        _record_event(db, job.id, "revert_started", "Downloading a fresh YTMusic copy for revert")
        _commit_quality_change(db)
    finally:
        db.close()

    download_job = DownloadJob(
        video_id=str(snapshot["yt_video_id"]),
        url=f"https://music.youtube.com/watch?v={snapshot['yt_video_id']}",
        title=str(snapshot["title"]),
        artist=str(snapshot["artist"]),
        album=str(snapshot["album"]),
        album_artist=str(snapshot["album_artist"]),
        browse_id=str(snapshot["yt_browse_id"]),
        art_url=str(snapshot["art_url"]),
        track_no=int(snapshot["track_no"] or 0),
        duration_ms=int(snapshot["duration_ms"] or 0),
        persist_to_subsonic=False,
        user_id=str(user_id),
        priority=5,
    )

    fresh_source: Path | None = None
    temp_target: Path | None = None
    replaced = False
    try:
        fresh_source = Path(await DOWNLOAD_MANAGER.ensure_downloaded(download_job))
        if not fresh_source.exists():
            raise RuntimeError("Fresh YTMusic revert download did not materialize")

        current_path = Path(str(snapshot["current_path"]))
        original_path = Path(str(snapshot["original_path"]))
        original_path.parent.mkdir(parents=True, exist_ok=True)
        temp_target = original_path.with_name(original_path.stem + ".helix-revert.opus")
        temp_target.unlink(missing_ok=True)

        # YTMusic normally yields Opus-in-WebM. First try a lossless stream-copy
        # remux into Ogg Opus; fall back to a normal Opus encode if necessary.
        copy_cmd = ["ffmpeg", "-y", "-i", str(fresh_source), "-vn", "-c:a", "copy", str(temp_target)]
        copied = subprocess.run(copy_cmd, capture_output=True, text=True)
        if copied.returncode != 0 or not temp_target.exists():
            encode_cmd = ["ffmpeg", "-y", "-i", str(fresh_source), "-vn", "-c:a", "libopus", "-b:a", "160k", str(temp_target)]
            encoded = subprocess.run(encode_cmd, capture_output=True, text=True)
            if encoded.returncode != 0 or not temp_target.exists():
                raise RuntimeError("Could not prepare fresh Opus file for revert")

        tag_source = current_path if current_path.exists() else fresh_source
        tag_job = SimpleNamespace(
            title=snapshot["title"], artist=snapshot["artist"], album=snapshot["album"],
            album_artist=snapshot["album_artist"], track_no=snapshot["track_no"],
        )
        _copy_original_tags(tag_source, temp_target, tag_job)
        new_info = _audio_info(temp_target)
        if str(new_info.get("codec") or "").lower() not in {"opus", "oggopus"}:
            raise RuntimeError("Fresh revert copy is not Opus")

        # Re-check the upgraded file before replacing it. This closes the race
        # where a user edits the file while the fresh source is downloading.
        if not current_path.exists():
            raise RuntimeError("Current upgraded file changed while revert was downloading")
        if expected_current_hash:
            current_hash = _sha256_file(current_path)
            if current_hash != expected_current_hash:
                raise RuntimeError("Current upgraded file changed outside Helix while revert was downloading")

        os.replace(temp_target, original_path)
        replaced = True
        if current_path != original_path:
            current_path.unlink(missing_ok=True)

        final_size, final_mtime = _fingerprint_file(original_path)
        final_hash = _sha256_file(original_path)

        db = SessionLocal()
        try:
            job = db.get(QualityUpgradeJob, job_id)
            if job is None:
                raise LookupError("Upgrade job disappeared during revert")
            meta = _job_metadata(db, job.id)
            job.library_path = str(original_path)
            job.original_path = str(original_path)
            job.original_size = final_size
            job.original_mtime_ns = final_mtime
            job.current_codec = str(new_info.get("codec") or "opus")
            job.current_bitrate = int(new_info.get("bitrate") or 0)
            job.current_sample_rate = int(new_info.get("sample_rate") or 0)
            job.current_bit_depth = int(new_info.get("bit_depth") or 0)
            job.status = "reverted"
            job.reverted_at = datetime.utcnow()
            job.completion_source = "user_revert"
            job.next_search_at = None
            job.last_error = ""
            job.updated_at = datetime.utcnow()
            meta.original_sha256 = final_hash
            meta.current_sha256 = final_hash
            meta.slskd_search_id = ""
            meta.slskd_search_query = ""
            meta.candidate_pool_json = "[]"
            meta.candidate_index = 0
            meta.updated_at = datetime.utcnow()
            _record_event(db, job.id, "reverted", "Restored a fresh YTMusic Opus copy; automatic upgrades suppressed", {"path": str(original_path)})
            _commit_quality_change(db)
            settings = get_settings(db)
        finally:
            db.close()

        sub = _subsonic(settings)
        if sub is not None:
            try:
                await sub.start_scan()
                try:
                    await sub.wait_for_scan_complete(timeout_s=120, poll_s=1.0)
                except Exception:
                    LOG.exception("[quality-upgrade] Navidrome scan wait failed after revert job=%s", job_id)
            finally:
                await sub.close()
    except Exception as exc:
        LOG.exception("Quality upgrade revert failed job_id=%s", job_id)
        if temp_target is not None and temp_target.exists() and not replaced:
            temp_target.unlink(missing_ok=True)
        db = SessionLocal()
        try:
            job = db.get(QualityUpgradeJob, job_id)
            if job is not None and job.status == "reverting":
                # The upgraded copy is still intact unless replacement already
                # completed. If it did complete, preserve the reverted state.
                if replaced:
                    job.status = "reverted"
                    job.reverted_at = datetime.utcnow()
                    job.completion_source = "user_revert"
                else:
                    job.status = "upgraded"
                job.last_error = f"Revert failed: {str(exc)[:1500]}"
                job.updated_at = datetime.utcnow()
                _record_event(db, job.id, "revert_failed", job.last_error)
                _commit_quality_change(db)
        finally:
            db.close()
        raise


async def quality_upgrade_worker_loop() -> None:
    while True:
        try:
            db = SessionLocal()
            try:
                settings = get_settings(db)
                enabled = bool(settings.get("slskd_enabled"))
                now = datetime.utcnow()
                jobs = [] if not enabled else db.execute(
                    select(QualityUpgradeJob)
                    .where(QualityUpgradeJob.status.in_([
                        "pending", "no_match",
                        "searching", "waiting_search", "waiting_peer", "downloading", "validating", "tagging", "replacing", "reverting",
                    ]))
                    .where((QualityUpgradeJob.next_search_at.is_(None)) | (QualityUpgradeJob.next_search_at <= now))
                    .order_by(QualityUpgradeJob.created_at.asc())
                    .limit(max(1, int(settings.get("slskd_concurrent_searches") or 2)))
                ).scalars().all() if enabled else []
                ids = [j.id for j in jobs]
            finally:
                db.close()

            if not enabled:
                await asyncio.sleep(10)
                continue

            if ids:
                await asyncio.gather(*(_process_job(job_id) for job_id in ids))
                # Never spin the worker continuously, even if a processing path
                # returns early. Individual jobs also carry next_search_at, but
                # this protects against future early-return regressions.
                await asyncio.sleep(1)
            else:
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOG.exception("Quality upgrade worker loop failed")
            await asyncio.sleep(10)
