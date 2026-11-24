"""Admin-only library reconciliation endpoints."""
from __future__ import annotations

import asyncio
from fastapi import APIRouter, Depends

from ..auth import require_admin
from ..models import User
from ..quality_library_reconciliation import reconcile_library, reconciliation_status

router = APIRouter(prefix='/api/quality-upgrades/library', tags=['quality-upgrades'])


@router.get('/reconcile')
def get_reconciliation_status(admin: User = Depends(require_admin)):
    return reconciliation_status()


@router.post('/reconcile')
async def scan_library(admin: User = Depends(require_admin)):
    return await asyncio.to_thread(reconcile_library)
