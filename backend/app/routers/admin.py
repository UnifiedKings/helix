from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import require_admin
from ..db import get_db
from ..models import SessionToken, User
from ..api_schemas.auth import AdminCreateUserRequest, AdminResetPasswordRequest, AdminUpdateUserRequest, AdminUserResponse
from ..services.accounts import create_user, list_users
from ..security import hash_password
from ..subsonic_permissions import can_import_to_subsonic, set_user_import_override, user_import_override

router = APIRouter(prefix="/admin", tags=["admin"])


def _to_response(db: Session, user: User) -> AdminUserResponse:
    return AdminUserResponse(
        id=user.id,
        username=user.username,
        role=user.role,
        is_active=user.is_active,
        subsonic_import_override=user_import_override(db, str(user.id)),
        can_import_subsonic=can_import_to_subsonic(db, user),
    )


def _active_admin_count(db: Session) -> int:
    return int(db.execute(
        select(func.count(User.id)).where(User.role == "admin", User.is_active.is_(True))
    ).scalar_one())


def _protect_last_active_admin(db: Session, user: User, *, next_role: str | None = None, next_active: bool | None = None) -> None:
    if user.role != "admin" or not user.is_active:
        return
    role = user.role if next_role is None else next_role
    active = user.is_active if next_active is None else next_active
    if role == "admin" and active:
        return
    if _active_admin_count(db) <= 1:
        raise HTTPException(status_code=400, detail="Helix must keep at least one active administrator")


@router.post("/users", response_model=AdminUserResponse)
def admin_create_user(payload: AdminCreateUserRequest, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    if payload.role not in ("admin", "user"):
        raise HTTPException(status_code=400, detail="Invalid role")

    existing = db.execute(select(User).where(User.username == payload.username)).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="Username already exists")

    return _to_response(db, create_user(db, username=payload.username, password=payload.password, role=payload.role))


@router.get("/users", response_model=list[AdminUserResponse])
def admin_list_users(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return [_to_response(db, user) for user in list_users(db)]


@router.patch("/users/{user_id}", response_model=AdminUserResponse)
def admin_update_user(user_id: str, payload: AdminUpdateUserRequest, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    next_role = payload.role if payload.role is not None else user.role
    next_active = payload.is_active if payload.is_active is not None else user.is_active
    if next_role not in ("admin", "user"):
        raise HTTPException(status_code=400, detail="role must be 'admin' or 'user'")
    _protect_last_active_admin(db, user, next_role=next_role, next_active=next_active)

    if payload.is_active is not None:
        user.is_active = payload.is_active
    if payload.role is not None:
        user.role = payload.role

    db.commit()
    db.refresh(user)

    if payload.subsonic_import_override is not None:
        set_user_import_override(db, str(user.id), bool(payload.subsonic_import_override))
        db.refresh(user)

    return _to_response(db, user)


@router.post("/users/{user_id}/reset-password")
def admin_reset_user_password(
    user_id: str,
    payload: AdminResetPasswordRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.password_hash = hash_password(payload.new_password)
    db.query(SessionToken).filter(SessionToken.user_id == user.id).delete(synchronize_session=False)
    db.add(user)
    db.commit()
    return {"ok": True}


@router.delete("/users/{user_id}")
def admin_delete_user(user_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    if str(admin.id) == str(user_id):
        raise HTTPException(status_code=400, detail="You cannot delete your own account while signed in")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.role == "admin" and user.is_active and _active_admin_count(db) <= 1:
        raise HTTPException(status_code=400, detail="Helix must keep at least one active administrator")

    # Existing Helix user-owned tables declare ON DELETE CASCADE. SQLite only
    # performs those cascades when foreign keys are enabled on the connection.
    # The admin dependency has already performed a read, so end that transaction
    # first, enable FK enforcement, and then re-fetch/delete on the same session.
    db.commit()
    raw_connection = db.connection().connection
    raw_connection.execute("PRAGMA foreign_keys = ON")
    fk_enabled = raw_connection.execute("PRAGMA foreign_keys").fetchone()
    if not fk_enabled or int(fk_enabled[0]) != 1:
        raise HTTPException(status_code=500, detail="Could not safely enable cascading user deletion")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    db.delete(user)
    db.commit()
    return {"ok": True}
