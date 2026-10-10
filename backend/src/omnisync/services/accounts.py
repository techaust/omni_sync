"""Users, sessions, lockout and first-run setup (FR-AUTH-01..04, 06-SECURITY section 3)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from omnisync.db.models import ROLES, Session, User
from omnisync.errors import AppError
from omnisync.security import passwords
from omnisync.security.tokens import new_csrf_token, new_session_token, token_hash

SESSION_IDLE = timedelta(hours=12)
SESSION_ABSOLUTE = timedelta(days=7)
SESSION_TOUCH_INTERVAL = timedelta(minutes=1)
LOCKOUT_THRESHOLD = 10
LOCKOUT_DURATION = timedelta(minutes=15)
USERNAME_RE = re.compile(r"^[a-z0-9._-]{3,32}$")

DEFAULT_PREFERENCES: dict[str, Any] = {
    "color_mode": "system",
    "density": "compact",
    "browser_notifications": False,
}


@dataclass(frozen=True)
class SessionResolution:
    session: Session | None
    expired: bool


def setup_required(db: DbSession) -> bool:
    return db.scalar(select(func.count()).select_from(User)) == 0


def validate_new_password(password: str, username: str) -> None:
    problems = passwords.policy_violations(password, username)
    if problems:
        raise AppError(
            "password.weak",
            detail="Password " + "; ".join(problems) + ".",
            errors=[{"field": "password", "message": p} for p in problems],
        )


def create_user(
    db: DbSession, *, username: str, display_name: str, password: str, role: str, now: datetime
) -> User:
    username = username.strip().lower()
    if not USERNAME_RE.fullmatch(username):
        raise AppError(
            "validation.failed",
            detail="Username must be 3-32 characters: a-z, 0-9, '.', '_' or '-'.",
            errors=[{"field": "username", "message": "invalid format"}],
        )
    if role not in ROLES:
        raise AppError("validation.failed", errors=[{"field": "role", "message": "unknown role"}])
    if db.scalar(select(User.id).where(User.username == username)) is not None:
        raise AppError("user.exists")
    validate_new_password(password, username)
    user = User(
        username=username,
        display_name=display_name.strip() or username,
        password_hash=passwords.hash_password(password),
        role=role,
        failed_logins=0,
        preferences=dict(DEFAULT_PREFERENCES),
        created_at=now,
        updated_at=now,
    )
    db.add(user)
    db.flush()
    return user


def authenticate(db: DbSession, username: str, password: str, now: datetime) -> User | None:
    """Return the user on success. Locked, disabled and unknown users all fail identically."""
    user = db.scalar(select(User).where(User.username == username.strip().lower()))
    ok = passwords.verify_password(user.password_hash if user else None, password)
    if user is None:
        return None
    locked = user.locked_until is not None and user.locked_until > now
    if not ok or locked or user.disabled_at is not None:
        if not locked:
            user.failed_logins += 1
            if user.failed_logins >= LOCKOUT_THRESHOLD:
                user.locked_until = now + LOCKOUT_DURATION
                user.failed_logins = 0
        return None
    user.failed_logins = 0
    user.locked_until = None
    if passwords.needs_rehash(user.password_hash):
        user.password_hash = passwords.hash_password(password)
    return user


def create_session(
    db: DbSession, user: User, now: datetime, user_agent: str | None
) -> tuple[str, Session]:
    raw = new_session_token()
    session = Session(
        id_hash=token_hash(raw),
        user_id=user.id,
        csrf_token=new_csrf_token(),
        created_at=now,
        last_seen_at=now,
        expires_at=now + SESSION_ABSOLUTE,
        user_agent=(user_agent or "")[:256] or None,
    )
    db.add(session)
    db.flush()
    return raw, session


def resolve_session(db: DbSession, raw_token: str | None, now: datetime) -> SessionResolution:
    if not raw_token:
        return SessionResolution(None, expired=False)
    session = db.get(Session, token_hash(raw_token))
    if session is None:
        return SessionResolution(None, expired=False)
    user = session.user
    if (
        now >= session.expires_at
        or now - session.last_seen_at >= SESSION_IDLE
        or user.disabled_at is not None
    ):
        db.delete(session)
        return SessionResolution(None, expired=True)
    if now - session.last_seen_at >= SESSION_TOUCH_INTERVAL:
        session.last_seen_at = now
    return SessionResolution(session, expired=False)


def revoke_session(db: DbSession, session: Session) -> None:
    db.delete(session)


def revoke_user_sessions(db: DbSession, user_id: int, keep_id_hash: str | None = None) -> int:
    sessions = db.scalars(select(Session).where(Session.user_id == user_id)).all()
    count = 0
    for session in sessions:
        if session.id_hash != keep_id_hash:
            db.delete(session)
            count += 1
    return count


def change_password(
    db: DbSession, user: User, current: str, new: str, now: datetime, keep_session: str
) -> None:
    if not passwords.verify_password(user.password_hash, current):
        raise AppError(
            "validation.failed",
            detail="Current password is incorrect.",
            errors=[{"field": "current_password", "message": "incorrect"}],
        )
    validate_new_password(new, user.username)
    user.password_hash = passwords.hash_password(new)
    user.updated_at = now
    revoke_user_sessions(db, user.id, keep_id_hash=keep_session)


def _active_admin_count(db: DbSession) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.role == "admin", User.disabled_at.is_(None))
        )
        or 0
    )


def update_user(
    db: DbSession,
    user: User,
    *,
    now: datetime,
    display_name: str | None = None,
    role: str | None = None,
    disabled: bool | None = None,
    new_password: str | None = None,
) -> list[str]:
    """Apply admin changes; returns the list of changed fields (for the audit record)."""
    changed: list[str] = []
    loses_admin = (
        user.role == "admin"
        and user.disabled_at is None
        and ((role is not None and role != "admin") or disabled is True)
    )
    if loses_admin and _active_admin_count(db) <= 1:
        raise AppError("user.last_admin")
    if display_name is not None and display_name.strip() != user.display_name:
        user.display_name = display_name.strip() or user.username
        changed.append("display_name")
    if role is not None and role != user.role:
        if role not in ROLES:
            raise AppError(
                "validation.failed", errors=[{"field": "role", "message": "unknown role"}]
            )
        user.role = role
        changed.append("role")
    if disabled is not None and disabled != (user.disabled_at is not None):
        user.disabled_at = now if disabled else None
        changed.append("disabled")
        if disabled:
            revoke_user_sessions(db, user.id)
    if new_password is not None:
        validate_new_password(new_password, user.username)
        user.password_hash = passwords.hash_password(new_password)
        user.failed_logins = 0
        user.locked_until = None
        revoke_user_sessions(db, user.id)
        changed.append("password")
    if changed:
        user.updated_at = now
    return changed
