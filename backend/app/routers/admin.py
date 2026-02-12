import json
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from ..deps import get_db, require_admin
from ..models import User, Setting
from ..schemas import CreateUserRequest, UpdateUserRequest, UserOut, SettingPatch, SettingsOut
from ..security import hash_password

router = APIRouter(prefix="/admin", tags=["admin"])

def get_bool_setting(db: Session, key: str, default: bool) -> bool:
    s = db.query(Setting).filter(Setting.key == key).first()
    if not s:
        return default
    try:
        return bool(json.loads(s.value))
    except Exception:
        return default

def set_bool_setting(db: Session, key: str, value: bool):
    s = db.query(Setting).filter(Setting.key == key).first()
    if not s:
        s = Setting(key=key, value=json.dumps(bool(value)))
        db.add(s)
    else:
        s.value = json.dumps(bool(value))
    db.commit()

@router.get("/users", response_model=list[UserOut])
def list_users(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    users = db.query(User).order_by(User.created_at.desc()).all()
    return [UserOut(id=u.id, username=u.username, role=u.role, is_active=u.is_active) for u in users]

@router.post("/users", response_model=UserOut)
def create_user(payload: CreateUserRequest, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.username == payload.username).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already exists")
    u = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        role=payload.role,
        is_active=True,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return UserOut(id=u.id, username=u.username, role=u.role, is_active=u.is_active)

@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: str, payload: UpdateUserRequest, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if payload.role is not None:
        u.role = payload.role
    if payload.is_active is not None:
        u.is_active = payload.is_active
    if payload.password is not None and payload.password.strip():
        u.password_hash = hash_password(payload.password)

    db.commit()
    db.refresh(u)
    return UserOut(id=u.id, username=u.username, role=u.role, is_active=u.is_active)

@router.get("/settings", response_model=SettingsOut)
def get_settings(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    return SettingsOut(
        auto_approve_requests=get_bool_setting(db, "auto_approve_requests", False)
    )

@router.patch("/settings", response_model=SettingsOut)
def patch_settings(payload: SettingPatch, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    if payload.auto_approve_requests is not None:
        set_bool_setting(db, "auto_approve_requests", payload.auto_approve_requests)

    return SettingsOut(
        auto_approve_requests=get_bool_setting(db, "auto_approve_requests", False)
    )
