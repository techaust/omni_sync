"""Request dependencies: database session, authentication, permissions, CSRF and origin checks."""

from __future__ import annotations

import hmac
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session as DbSession

from omnisync.api.state import AppState
from omnisync.db.models import Session, User
from omnisync.errors import AppError
from omnisync.security.permissions import Permission, role_has
from omnisync.services import accounts

SESSION_COOKIE = "omnisync_session"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
GENERAL_LIMIT_PER_MIN = 600


def get_state(request: Request) -> AppState:
    state: AppState = request.app.state.omnisync
    return state


StateDep = Annotated[AppState, Depends(get_state)]


def get_db(state: StateDep) -> Iterator[DbSession]:
    db = state.db_factory()
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


# scope="function": commit (or roll back) BEFORE the response is sent. FastAPI's default for
# yield dependencies is "request", which commits after the client already has its response, so a
# fast follow-up read could miss the write and a failed commit would look like success
# (tests/test_transactions.py).
DbDep = Annotated[DbSession, Depends(get_db, scope="function")]


def check_origin(request: Request, state: StateDep) -> None:
    """Reject cross-site state-changing requests (C-03).

    Browsers always send ``Origin`` (or ``Sec-Fetch-Site``) on cross-origin POSTs, so a
    request carrying neither did not originate from another web page.
    """
    if request.method in SAFE_METHODS:
        return
    origin = request.headers.get("origin")
    if origin is not None:
        if origin.lower() not in state.settings.allowed_origins:
            raise AppError("auth.csrf_failed", detail="Cross-origin request rejected.")
        return
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site is not None and fetch_site not in ("same-origin", "none"):
        raise AppError("auth.csrf_failed", detail="Cross-site request rejected.")


OriginDep = Depends(check_origin)


@dataclass(frozen=True)
class AuthContext:
    user: User
    session: Session


def _authenticate(request: Request, state: AppState, db: DbSession) -> AuthContext:
    now = state.clock.now()
    resolution = accounts.resolve_session(db, request.cookies.get(SESSION_COOKIE), now)
    if resolution.session is None:
        if resolution.expired:
            db.commit()  # persist deletion of the expired session before raising
            raise AppError("auth.session_expired")
        raise AppError("auth.required")
    return AuthContext(user=resolution.session.user, session=resolution.session)


class PermissionDependency:
    """``Depends(require(Permission.X))``; the attribute ``permission`` lets tests enumerate
    every route's permission for the permission-matrix test (FR-AUTH-03)."""

    def __init__(self, permission: Permission) -> None:
        self.permission = permission

    def __call__(self, request: Request, state: StateDep, db: DbDep) -> AuthContext:
        check_origin(request, state)
        ctx = _authenticate(request, state, db)
        if request.method not in SAFE_METHODS:
            supplied = request.headers.get(CSRF_HEADER, "")
            if not hmac.compare_digest(supplied, ctx.session.csrf_token):
                raise AppError("auth.csrf_failed")
        retry = state.rate_limiter.hit("session", ctx.session.id_hash, GENERAL_LIMIT_PER_MIN, 60)
        if retry is not None:
            raise AppError("rate_limited", headers={"Retry-After": str(retry)})
        if not role_has(ctx.user.role, self.permission):
            raise AppError(
                "auth.forbidden", detail=f"Requires permission '{self.permission.value}'."
            )
        return ctx


def require(permission: Permission) -> Callable[..., AuthContext]:
    return PermissionDependency(permission)


def client_user_agent(request: Request) -> str | None:
    return request.headers.get("user-agent")
