"""Request and response models for the Milestone 3 endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Role = Literal["admin", "operator", "viewer"]
ColorMode = Literal["system", "light", "dark"]
Density = Literal["compact", "comfortable"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InstanceOut(BaseModel):
    id: str
    name: str


class Preferences(BaseModel):
    color_mode: ColorMode = "system"
    density: Density = "compact"
    browser_notifications: bool = False


class MeOut(BaseModel):
    id: int
    username: str
    display_name: str
    role: Role
    permissions: list[str]
    preferences: Preferences
    csrf_token: str
    instance: InstanceOut


class HealthOut(BaseModel):
    status: Literal["ok"]


class HealthCheck(BaseModel):
    name: str
    ok: bool
    detail: str


class HealthDetailsOut(BaseModel):
    version: str
    instance: InstanceOut
    checks: list[HealthCheck]


class SetupStatusOut(BaseModel):
    setup_required: bool


class SetupIn(StrictModel):
    setup_token: str = Field(min_length=1, max_length=64)
    username: str = Field(min_length=3, max_length=32)
    display_name: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class LoginIn(StrictModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class PasswordChangeIn(StrictModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class PreferencesPatch(StrictModel):
    color_mode: ColorMode | None = None
    density: Density | None = None
    browser_notifications: bool | None = None


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str
    role: Role
    disabled: bool
    locked: bool
    created_at: datetime
    updated_at: datetime


class UserCreateIn(StrictModel):
    username: str = Field(min_length=3, max_length=32)
    display_name: str = Field(min_length=1, max_length=64)
    role: Role
    password: str = Field(min_length=1, max_length=256)


class UserPatchIn(StrictModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=64)
    role: Role | None = None
    disabled: bool | None = None
    new_password: str | None = Field(default=None, min_length=1, max_length=256)


class UserList(BaseModel):
    items: list[UserOut]
