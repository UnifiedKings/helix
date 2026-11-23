"""Admin inspection/removal of persistent rejected-source records."""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..auth import require_admin
from ..db import get_db
from ..models import User
from ..quality_source_registry import RejectedQualitySource

router = APIRouter(prefix='/api/quality-upgrades/admin/rejected-sources', tags=['quality-upgrades'])


@router.get('')
def list_rejected_sources(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.execute(select(RejectedQualitySource).order_by(
        RejectedQualitySource.created_at.desc()).limit(500)).scalars().all()
    return {'items': [{'id':row.id, 'username':row.username, 'filename':row.filename,
                       'size':row.size, 'sha256':row.sha256, 'reason':row.reason,
                       'job_id':row.job_id, 'created_at':row.created_at}
                      for row in rows]}


@router.delete('/{rejection_id}')
def clear_rejected_source(rejection_id: int, admin: User = Depends(require_admin),
                          db: Session = Depends(get_db)):
    row = db.get(RejectedQualitySource, rejection_id)
    if row is None:
        raise HTTPException(404, 'Rejected source not found')
    db.delete(row)
    db.commit()
    return {'ok': True}
