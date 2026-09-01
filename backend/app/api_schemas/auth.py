from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


class SetupRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=256)


class LoginRequest(BaseModel):
    username: str
    password: str


class MeResponse(BaseModel):
    id: str
    username: str
    role: str


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)


class AdminCreateUserRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=256)
    role: str = Field(default="user")


class AdminResetPasswordRequest(BaseModel):
    new_password: str = Field(min_length=8, max_length=256)


class AdminUserResponse(BaseModel):
    id: str
    username: str
    role: str
    is_active: bool
    subsonic_import_override: bool = False
    can_import_subsonic: bool = False


class AdminUpdateUserRequest(BaseModel):
    is_active: Optional[bool] = None
    role: Optional[str] = None
    subsonic_import_override: Optional[bool] = None
