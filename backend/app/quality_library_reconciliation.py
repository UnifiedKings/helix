"""Conservative reconciliation of Helix import intents and existing library files.

Never assumes that an arbitrary file in a shared Subsonic music mount belongs to
Helix. New import intents are captured independently of slskd availability.
An intent is enrolled only after an unambiguous, matching library file exists.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, SessionLocal, engine
from .quality_models import QualityUpgradeJob, QualityUpgradeEvent, QualityUpgradeMetadata

LOG = logging.getLogger(__name__)
AUDIO_EXTS = frozenset({'.opus', '.ogg', '.mp3', '.flac', '.m4a', '.aac', '.wav', '.aiff', '.aif', '.alac'})
_SCAN_LOCK = threading.Lock()
_LAST_RESULT: dict[str, Any] = {'status': 'not_run'}


class QualityImportIntent(Base):
    __tablename__ = 'quality_import_intents'

    video_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, default='')
    title: Mapped[str] = mapped_column(Text, nullable=False, default='')
    artist: Mapped[str] = mapped_column(Text, nullable=False, default='')
    album: Mapped[str] = mapped_column(Text, nullable=False, default='')
    album_artist: Mapped[str] = mapped_column(Text, nullable=False, default='')
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    track_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    browse_id: Mapped[str] = mapped_column(String(128), nullable=False, default='')
    art_url: Mapped[str] = mapped_column(Text, nullable=False, default='')
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    enrolled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


def initialize_import_intents() -> None:
    Base.metadata.create_all(bind=engine, tables=[QualityImportIntent.__table__])


def record_import_intent(job: Any) -> None:
    """Record only explicit persistent library imports, never stream-cache jobs."""
    if not bool(getattr(job, 'persist_to_subsonic', False)):
        return
    video_id = str(getattr(job, 'video_id', '') or '').strip()
    if not video_id:
        return
    db = SessionLocal()
    try:
        row = db.get(QualityImportIntent, video_id)
        if row is None:
            row = QualityImportIntent(video_id=video_id)
            db.add(row)
        if row.enrolled_at is not None:
            return
        for column, attr in (
            ('user_id', 'user_id'), ('title', 'title'), ('artist', 'artist'),
            ('album', 'album'), ('album_artist', 'album_artist'),
            ('browse_id', 'browse_id'), ('art_url', 'art_url'),
            ('duration_ms', 'duration_ms'), ('track_no', 'track_no'),
        ):
            value = getattr(job, attr, None)
            if value not in (None, '', 0):
                setattr(row, column, value)
        db.commit()
    except Exception:
        db.rollback()
        LOG.exception('Could not record Helix import intent %s', video_id)
    finally:
        db.close()


def confirm_completed_imports(jobs: list[Any]) -> None:
    """Only a successful Helix finalization proves an imported file was created."""
    if not jobs:
        return
    db = SessionLocal()
    try:
        for job in jobs:
            vid = str(getattr(job, 'video_id', '') or '')
            if not vid:
                continue
            row = db.get(QualityImportIntent, vid)
            if row is None:
                continue
            for column in ('title', 'artist', 'album', 'album_artist', 'browse_id', 'art_url'):
                value = getattr(job, column, None)
                if value:
                    setattr(row, column, str(value))
            if getattr(job, 'duration_ms', 0):
                row.duration_ms = int(job.duration_ms)
            if getattr(job, 'track_no', 0):
                row.track_no = int(job.track_no)
            row.completed_at = datetime.utcnow()
        db.commit()
    except Exception:
        db.rollback()
        LOG.exception('Failed recording successful Helix finalization')
    finally:
        db.close()


def install_import_tracking() -> None:
    """Record explicit import intents and confirmed finalization independently of slskd.

    No playback/download ordering is modified and the original finalize methods
    remain the sole authority for deciding whether the import succeeded.
    """
    from .download_manager import DOWNLOAD_MANAGER
    if getattr(DOWNLOAD_MANAGER, '_quality_intent_tracking_installed', False):
        return
    original_mark = DOWNLOAD_MANAGER._mark_for_subsonic_import

    def tracked(job: Any) -> None:
        original_mark(job)
        record_import_intent(job)

    DOWNLOAD_MANAGER._mark_for_subsonic_import = tracked

    def wrap_finalize(original):
        async def finalize(*args, **kwargs):
            # Snapshot only fully downloaded, explicit library imports. A
            # missing temp file cannot count as successful finalization.
            pending = {
                video_id: job for video_id, job in list(DOWNLOAD_MANAGER._jobs.items())
                if getattr(job, 'persist_to_subsonic', False)
                and DOWNLOAD_MANAGER._ready.get(video_id)
                and os.path.isfile(DOWNLOAD_MANAGER._ready[video_id])
            }
            result = await original(*args, **kwargs)
            completed = [job for video_id, job in pending.items()
                         if video_id not in DOWNLOAD_MANAGER._jobs
                         and video_id not in DOWNLOAD_MANAGER._finalize_fail]
            confirm_completed_imports(completed)
            return result
        return finalize

    DOWNLOAD_MANAGER.finalize_batch = wrap_finalize(DOWNLOAD_MANAGER.finalize_batch)
    DOWNLOAD_MANAGER._quality_intent_tracking_installed = True


def _normal(value: Any) -> str:
    return ' '.join(str(value or '').casefold().split())


def _audio_identity(path: Path) -> dict[str, Any] | None:
    try:
        from mutagen import File
        audio = File(str(path), easy=True)
        if audio is None:
            return None
        def tag(key: str) -> str:
            values = audio.get(key, [])
            return str(values[0]) if values else ''
        return {
            'title': tag('title'), 'artist': tag('artist'),
            'album': tag('album'), 'album_artist': tag('albumartist'),
            'duration_ms': round(float(audio.info.length) * 1000) if audio.info else 0,
        }
    except Exception:
        return None


def _identity_matches(intent: QualityImportIntent, audio: dict[str, Any]) -> bool:
    """Require matching artist/title/album, and duration when recorded."""
    if not all((_normal(intent.title), _normal(intent.artist), _normal(intent.album))):
        return False
    if _normal(intent.title) != _normal(audio.get('title')):
        return False
    if _normal(intent.album) != _normal(audio.get('album')):
        return False
    artists = {_normal(audio.get('artist')), _normal(audio.get('album_artist'))}
    expected_artists = {_normal(intent.artist), _normal(intent.album_artist)} - {''}
    if not (expected_artists & artists):
        return False
    desired = int(intent.duration_ms or 0)
    actual = int(audio.get('duration_ms') or 0)
    if desired and (not actual or abs(desired - actual) > 5000):
        return False
    return True


def _safe_library_files(root: Path, limit: int) -> tuple[list[Path], bool]:
    """Do not follow symlinks, escape the configured library, or scan endlessly."""
    output: list[Path] = []
    truncated = False
    if not root.is_dir():
        return output, False
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if not d.startswith('.') and not (Path(directory) / d).is_symlink()]
        for filename in files:
            path = Path(directory) / filename
            if path.suffix.lower() not in AUDIO_EXTS or path.is_symlink():
                continue
            output.append(path)
            if len(output) >= limit:
                truncated = True
                return output, truncated
    return output, truncated


def reconcile_library(*, max_files: int = 100000) -> dict[str, Any]:
    """Inventory and enroll uniquely verified, previously recorded imports.

    Any unrelated/unattributed library files remain untracked. Scanning does NOT
    call Soulseek; only the existing budgeted worker can process enrolled jobs.
    """
    global _LAST_RESULT
    if not _SCAN_LOCK.acquire(blocking=False):
        return {'status': 'already_running'}
    started = datetime.utcnow()
    db = SessionLocal()
    try:
        root = Path(os.getenv('HELIX_MUSIC_LIBRARY_ROOT', '/data/music')).resolve()
        files, truncated = _safe_library_files(root, max(1, min(int(max_files), 200000)))
        jobs = db.execute(select(QualityUpgradeJob)).scalars().all()
        intents = db.execute(select(QualityImportIntent)).scalars().all()
        # Historical upgrade jobs constitute recorded provenance, irrespective
        # of whether they were completed under modern quality profiles.
        known_paths: set[Path] = set()
        for job in jobs:
            for raw in (job.library_path, job.original_path):
                if not raw:
                    continue
                try:
                    p = Path(raw).resolve()
                    p.relative_to(root)
                    known_paths.add(p)
                except (ValueError, OSError):
                    pass
        known_video_ids = {job.yt_video_id for job in jobs if job.yt_video_id}
        unresolved = [intent for intent in intents if intent.completed_at is not None and not intent.enrolled_at and intent.video_id not in known_video_ids]
        # Only inspect tags of not-yet-known paths. This can be slow for large
        # libraries, hence scans run in a thread and are limited in size.
        matches: dict[str, list[Path]] = {item.video_id: [] for item in unresolved}
        lookups: dict[tuple[str, str], list[QualityImportIntent]] = {}
        for intent in unresolved:
            lookups.setdefault((_normal(intent.title), _normal(intent.album)), []).append(intent)
        unknown = 0
        unreadable = 0
        for path in files:
            if path.resolve() in known_paths:
                continue
            unknown += 1
            if not unresolved:
                continue
            # Only read audio metadata for files that could match an import.
            # Filename often includes a track-number prefix, so no exact-path
            # assumption is made here.
            audio = _audio_identity(path)
            if not audio:
                unreadable += 1
                continue
            for intent in lookups.get((_normal(audio['title']), _normal(audio['album'])), []):
                if _identity_matches(intent, audio):
                    matches[intent.video_id].append(path)
        enrolled = 0
        ambiguous = 0
        missing = 0
        for intent in intents:
            if intent.video_id in known_video_ids:
                if intent.enrolled_at is None:
                    intent.enrolled_at = started
                continue
            if intent.completed_at is None:
                continue  # Never infer ownership from an uncompleted import request.
            matches_for_id = matches.get(intent.video_id, [])
            if len(matches_for_id) > 1:
                ambiguous += 1
                continue
            if len(matches_for_id) == 0:
                missing += 1
                continue
            # Do not auto-enroll ambiguous legacy files with no import intent.
            # The sole matched file must be unclaimed by another existing job.
            matched = matches_for_id[0].resolve()
            if matched in known_paths:
                ambiguous += 1
                continue
            job = QualityUpgradeJob(
                requested_by_user_id=intent.user_id, yt_video_id=intent.video_id,
                yt_browse_id=intent.browse_id, title=intent.title,
                artist=intent.artist, album=intent.album,
                album_artist=intent.album_artist, duration_ms=intent.duration_ms,
                track_no=intent.track_no, art_url=intent.art_url,
                library_path=str(matched), status='pending',
            )
            db.add(job)
            db.flush()
            db.add(QualityUpgradeMetadata(job_id=job.id, provenance='helix_imported'))
            db.add(QualityUpgradeEvent(
                job_id=job.id, event='reconciled_import',
                message='Recorded Helix import matched a unique library file; enrolled for quality upgrades',
            ))
            known_paths.add(matched)
            known_video_ids.add(intent.video_id)
            intent.enrolled_at = started
            enrolled += 1
        db.commit()
        from .quality_continuous_upgrades import reevaluate_quality_jobs
        quality_report = reevaluate_quality_jobs()
        result = {
            'status': 'complete', 'scanned_at': started.isoformat() + 'Z',
            'files_examined': len(files), 'scan_truncated': truncated,
            'existing_upgrade_jobs': len(jobs), 'newly_enrolled': enrolled,
            'below_target': quality_report['below_target'],
            'target_satisfied': quality_report['target_satisfied'],
            'reactivated': quality_report['reactivated'],
            'upgrade_deferred': quality_report['deferred'],
            'ownership_unverified': quality_report['unverified'],
            'untracked_library_files': max(0, unknown - enrolled), 'unmatched_import_intents': missing, 'unconfirmed_import_intents': sum(i.completed_at is None for i in intents),
            'ambiguous_imports': ambiguous, 'unreadable_files': unreadable,
            'message': 'Untracked files were not adopted automatically; only recorded Helix imports are eligible.',
        }
        _LAST_RESULT = result
        return result
    except Exception:
        db.rollback()
        LOG.exception('Helix library reconciliation failed')
        _LAST_RESULT = {'status': 'failed', 'scanned_at': started.isoformat() + 'Z'}
        raise
    finally:
        db.close()
        _SCAN_LOCK.release()


def reconciliation_status() -> dict[str, Any]:
    return dict(_LAST_RESULT)


async def periodic_reconciliation() -> None:
    """Run after startup and periodically; keep disk scanning off event loop."""
    await asyncio.sleep(30)
    while True:
        try:
            await asyncio.to_thread(reconcile_library)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOG.exception('Background quality library inventory failed')
        await asyncio.sleep(max(3600, int(os.getenv('HELIX_QUALITY_RECONCILE_INTERVAL_S', '21600'))))
