from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import User, UserSetting
from .settings_store import get_settings

USER_IMPORT_OVERRIDE_KEY = "admin_allow_subsonic_import"


def _loads_bool(value_json: str) -> bool:
    try:
        return bool(json.loads(value_json))
    except Exception:
        return False


def allow_all_users(db: Session) -> bool:
    settings = get_settings(db)
    return bool(settings.get("allow_all_users_subsonic_import", False))


def user_import_override(db: Session, user_id: str) -> bool:
    row = db.execute(
        select(UserSetting).where(
            UserSetting.user_id == user_id,
            UserSetting.key == USER_IMPORT_OVERRIDE_KEY,
        )
    ).scalar_one_or_none()
    return _loads_bool(row.value_json) if row is not None else False


def set_user_import_override(db: Session, user_id: str, allowed: bool) -> bool:
    row = db.execute(
        select(UserSetting).where(
            UserSetting.user_id == user_id,
            UserSetting.key == USER_IMPORT_OVERRIDE_KEY,
        )
    ).scalar_one_or_none()
    if row is None:
        row = UserSetting(user_id=user_id, key=USER_IMPORT_OVERRIDE_KEY)
    row.value_json = json.dumps(bool(allowed))
    row.updated_at = datetime.utcnow()
    db.add(row)
    db.commit()
    return bool(allowed)


def can_import_to_subsonic(db: Session, user: User | None) -> bool:
    if user is None:
        return False
    if str(user.role or "").lower() == "admin":
        return True
    if allow_all_users(db):
        return True
    return user_import_override(db, str(user.id))
