"""User management for admins (FR-AUTH-04, 05-API-SPEC section 4.1)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy import select

from omnisync.api.deps import AuthContext, DbDep, StateDep, require
from omnisync.api.schemas import UserCreateIn, UserList, UserOut, UserPatchIn
from omnisync.db.models import User
from omnisync.errors import AppError
from omnisync.security.permissions import Permission
from omnisync.services import accounts, audit

router = APIRouter(prefix="/users", tags=["users"])
Admin = Annotated[AuthContext, Depends(require(Permission.USERS_MANAGE))]


def user_out(user: User, now: datetime) -> UserOut:
    locked = user.locked_until is not None and user.locked_until > now
    return UserOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,  # type: ignore[arg-type]
        disabled=user.disabled_at is not None,
        locked=locked,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


@router.get("", response_model=UserList)
def list_users(state: StateDep, db: DbDep, _ctx: Admin) -> UserList:
    users = db.scalars(select(User).order_by(User.username)).all()
    now = state.clock.now()
    return UserList(items=[user_out(u, now) for u in users])


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreateIn, state: StateDep, db: DbDep, ctx: Admin) -> UserOut:
    now = state.clock.now()
    user = accounts.create_user(
        db,
        username=body.username,
        display_name=body.display_name,
        password=body.password,
        role=body.role,
        now=now,
    )
    audit.record(
        db,
        now=now,
        actor=ctx.user.username,
        instance_name=state.instance_name,
        action="user.created",
        target=user.username,
        details={"role": user.role},
    )
    return user_out(user, now)


@router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserPatchIn, state: StateDep, db: DbDep, ctx: Admin) -> UserOut:
    user = db.get(User, user_id)
    if user is None:
        raise AppError("resource.not_found", detail=f"User {user_id} does not exist.")
    now = state.clock.now()
    changed = accounts.update_user(
        db,
        user,
        now=now,
        display_name=body.display_name,
        role=body.role,
        disabled=body.disabled,
        new_password=body.new_password,
    )
    if changed:
        audit.record(
            db,
            now=now,
            actor=ctx.user.username,
            instance_name=state.instance_name,
            action="user.updated",
            target=user.username,
            details={"changed": changed},
        )
    return user_out(user, now)
