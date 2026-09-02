from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import get_current_user, require_admin
from ..db import get_db
from ..download_manager import DOWNLOAD_MANAGER, DownloadJob
from ..integrations.slskd import SlskdClient
from ..models import User
from ..quality_models import AdminNotification, QualityUpgradeJob
from ..realtime import schedule_quality_upgrades_changed
from ..settings_store import get_settings, patch_settings
from ..subsonic_permissions import can_import_to_subsonic

router = APIRouter(prefix="/api/quality-upgrades", tags=["quality-upgrades"])


def _require_access(db: Session, user: User) -> None:
    if not can_import_to_subsonic(db, user):
        raise HTTPException(status_code=403, detail="Subsonic add permission is required")


def _job_payload(job: QualityUpgradeJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "title": job.title,
        "artist": job.artist,
        "album": job.album,
        "art_url": job.art_url,
        "status": job.status,
        "attempts": job.attempts,
        "best_match_score": job.best_match_score,
        "last_error": job.last_error,
        "last_search_at": job.last_search_at,
        "next_search_at": job.next_search_at,
        "upgraded_at": job.upgraded_at,
        "reverted_at": job.reverted_at,
        "completion_source": job.completion_source,
        "original": {
            "codec": job.original_codec,
            "bitrate": job.original_bitrate,
            "sample_rate": job.original_sample_rate,
            "bit_depth": job.original_bit_depth,
        },
        "current": {
            "codec": job.current_codec,
            "bitrate": job.current_bitrate,
            "sample_rate": job.current_sample_rate,
            "bit_depth": job.current_bit_depth,
        },
    }


@router.get("")
def list_upgrades(
    status: str = Query(default="all"),
    q: str = Query(default=""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_access(db, user)
    stmt = select(QualityUpgradeJob).order_by(QualityUpgradeJob.created_at.desc())
    if status and status != "all":
        stmt = stmt.where(QualityUpgradeJob.status == status)
    jobs = db.execute(stmt).scalars().all()
    query = q.strip().lower()
    if query:
        jobs = [j for j in jobs if query in f"{j.artist} {j.title} {j.album}".lower()]
    return {"items": [_job_payload(j) for j in jobs]}


@router.post("/{job_id}/retry")
def retry_upgrade(
    job_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_access(db, user)
    job = db.get(QualityUpgradeJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Upgrade job not found")
    if job.status == "upgraded":
        raise HTTPException(status_code=409, detail="Track is already upgraded")

    # Prevent double-clicks / repeated refreshes from issuing the same Soulseek
    # search in a tight loop. This is intentionally short; normal automatic
    # retries remain governed by the much longer backoff schedule.
    if job.last_search_at and (datetime.utcnow() - job.last_search_at).total_seconds() < 10:
        raise HTTPException(status_code=429, detail="Please wait a few seconds before searching this track again")

    job.status = "pending"
    job.next_search_at = None
    job.last_error = ""
    job.updated_at = datetime.utcnow()
    db.commit()
    schedule_quality_upgrades_changed()
    return _job_payload(job)


@router.post("/{job_id}/enable")
def enable_upgrade(
    job_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_access(db, user)
    job = db.get(QualityUpgradeJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Upgrade job not found")
    job.status = "pending"
    job.next_search_at = None
    job.updated_at = datetime.utcnow()
    db.commit()
    schedule_quality_upgrades_changed()
    return _job_payload(job)


@router.post("/{job_id}/revert")
async def revert_upgrade(
    job_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_access(db, user)
    job = db.get(QualityUpgradeJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Upgrade job not found")
    if job.status != "upgraded":
        raise HTTPException(status_code=409, detail="Only upgraded tracks can be reverted")

    # Revert deliberately uses Helix's normal reliable YTMusic pipeline.
    await DOWNLOAD_MANAGER.enqueue_normal(DownloadJob(
        video_id=job.yt_video_id,
        url=f"https://music.youtube.com/watch?v={job.yt_video_id}",
        title=job.title,
        artist=job.artist,
        album=job.album,
        album_artist=job.album_artist,
        browse_id=job.yt_browse_id,
        art_url=job.art_url,
        track_no=job.track_no,
        duration_ms=job.duration_ms,
        persist_to_subsonic=True,
        user_id=str(user.id),
        priority=30,
    ))

    # Remove the upgraded file first only after the fresh YT download has been
    # queued; the current file remains available during the download. The
    # finalize pipeline may need to replace it after import, so mark this job
    # suppressed immediately to prevent the slskd worker racing it.
    job.status = "reverted"
    job.reverted_at = datetime.utcnow()
    job.completion_source = "user_revert"
    job.next_search_at = None
    job.updated_at = datetime.utcnow()
    db.commit()
    schedule_quality_upgrades_changed()
    return _job_payload(job)


@router.get("/admin/notifications")
def quality_notifications(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        select(AdminNotification)
        .where(AdminNotification.kind == "quality_upgrade")
        .order_by(AdminNotification.created_at.desc())
        .limit(100)
    ).scalars().all()
    return {
        "items": [
            {
                "id": row.id,
                "title": row.title,
                "body": row.body,
                "data_json": row.data_json,
                "created_at": row.created_at,
            }
            for row in rows
        ]
    }


@router.post("/admin/test-connection")
async def test_slskd(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    settings = get_settings(db)
    url = str(settings.get("slskd_url") or "").strip()
    key = str(settings.get("slskd_api_key") or "").strip()
    if not url or not key:
        raise HTTPException(status_code=409, detail="Configure slskd URL and API key first")
    client = SlskdClient(url, key, timeout_s=float(settings.get("slskd_timeout_s") or 20))
    try:
        return await client.test_connection()
    finally:
        await client.close()



@router.delete("/{job_id}")
def delete_upgrade_job(
    job_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_access(db, user)
    job = db.get(QualityUpgradeJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Quality upgrade job not found")

    db.delete(job)
    db.commit()
    schedule_quality_upgrades_changed()
    return {"ok": True}


@router.get("/admin/config")
def get_slskd_config(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    settings = get_settings(db)
    return {
        "slskd_enabled": bool(settings.get("slskd_enabled")),
        "slskd_url": settings.get("slskd_url") or "",
        "slskd_api_key_configured": bool(settings.get("slskd_api_key")),
        "slskd_downloads_path": settings.get("slskd_downloads_path") or "",
        "slskd_concurrent_searches": int(settings.get("slskd_concurrent_searches") or 2),
        "slskd_match_threshold": float(settings.get("slskd_match_threshold") or 78),
        "slskd_url_locked": bool(os.getenv("SLSKD_URL")),
        "slskd_api_key_locked": bool(os.getenv("SLSKD_API_KEY")),
        "slskd_downloads_path_locked": bool(os.getenv("SLSKD_DOWNLOADS_PATH")),
    }


@router.patch("/admin/config")
def patch_slskd_config(
    payload: dict[str, Any] = Body(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    allowed = {
        "slskd_enabled",
        "slskd_url",
        "slskd_api_key",
        "slskd_downloads_path",
        "slskd_concurrent_searches",
        "slskd_match_threshold",
    }
    clean = {k: v for k, v in payload.items() if k in allowed}
    if os.getenv("SLSKD_URL"):
        clean.pop("slskd_url", None)
    if os.getenv("SLSKD_API_KEY"):
        clean.pop("slskd_api_key", None)
    if os.getenv("SLSKD_DOWNLOADS_PATH"):
        clean.pop("slskd_downloads_path", None)
    if clean.get("slskd_api_key") in ("", None, "********"):
        clean.pop("slskd_api_key", None)
    if "slskd_concurrent_searches" in clean:
        clean["slskd_concurrent_searches"] = max(1, min(3, int(clean["slskd_concurrent_searches"])))
    if "slskd_match_threshold" in clean:
        clean["slskd_match_threshold"] = max(50.0, min(100.0, float(clean["slskd_match_threshold"])))
    patch_settings(db, clean)
    return get_slskd_config(admin=admin, db=db)
