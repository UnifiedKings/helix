from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require_admin
from ..db import get_db
from ..models import User
from ..api_schemas.auth import AdminCreateUserRequest, AdminUpdateUserRequest, AdminUserResponse
from ..services.accounts import create_user, list_users

router = APIRouter(prefix="/admin", tags=["admin"])


def _to_response(user: User) -> AdminUserResponse:
    return AdminUserResponse(id=user.id, username=user.username, role=user.role, is_active=user.is_active)


@router.post("/users", response_model=AdminUserResponse)
def admin_create_user(payload: AdminCreateUserRequest, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    if payload.role not in ("admin", "user"):
        raise HTTPException(status_code=400, detail="Invalid role")

    existing = db.execute(select(User).where(User.username == payload.username)).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="Username already exists")

    return _to_response(create_user(db, username=payload.username, password=payload.password, role=payload.role))


@router.get("/users", response_model=list[AdminUserResponse])
def admin_list_users(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return [_to_response(user) for user in list_users(db)]


@router.patch("/users/{user_id}", response_model=AdminUserResponse)
def admin_update_user(user_id: str, payload: AdminUpdateUserRequest, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if payload.is_active is not None:
        user.is_active = payload.is_active
    if payload.role is not None:
        if payload.role not in ("admin", "user"):
            raise HTTPException(status_code=400, detail="role must be 'admin' or 'user'")
        user.role = payload.role

    db.commit()
    db.refresh(user)
    return _to_response(user)
