"""Setup, login, logout, current user, password and preferences (05-API-SPEC section 4.1)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from omnisync.api.deps import (
    SESSION_COOKIE,
    AuthContext,
    DbDep,
    OriginDep,
    StateDep,
    client_user_agent,
    require,
)
from omnisync.api.schemas import (
    InstanceOut,
    LoginIn,
    MeOut,
    PasswordChangeIn,
    Preferences,
    PreferencesPatch,
    SetupIn,
    SetupStatusOut,
)
from omnisync.api.state import AppState
from omnisync.db.models import User
from omnisync.errors import AppError
from omnisync.security.permissions import ROLE_PERMISSIONS, Permission
from omnisync.services import accounts, audit

router = APIRouter(tags=["auth"])

LOGIN_LIMIT_PER_MIN = 10
SETUP_LIMIT_PER_MIN = 5


def me_out(state: AppState, ctx_user: User, csrf_token: str) -> MeOut:
    return MeOut(
        id=ctx_user.id,
        username=ctx_user.username,
        display_name=ctx_user.display_name,
        role=ctx_user.role,  # type: ignore[arg-type]
        permissions=sorted(p.value for p in ROLE_PERMISSIONS[ctx_user.role]),
        preferences=Preferences.model_validate(
            {**accounts.DEFAULT_PREFERENCES, **ctx_user.preferences}
        ),
        csrf_token=csrf_token,
        instance=InstanceOut(id=state.instance_id, name=state.instance_name),
    )


def _set_session_cookie(response: Response, raw_token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        raw_token,
        max_age=int(accounts.SESSION_ABSOLUTE.total_seconds()),
        httponly=True,
        samesite="strict",
        secure=False,  # plain HTTP on loopback (ADR-0012)
        path="/",
    )


@router.get("/setup/status", response_model=SetupStatusOut)
def setup_status(db: DbDep) -> SetupStatusOut:
    return SetupStatusOut(setup_required=accounts.setup_required(db))


@router.post(
    "/setup", response_model=MeOut, status_code=status.HTTP_201_CREATED, dependencies=[OriginDep]
)
def complete_setup(
    body: SetupIn, request: Request, response: Response, state: StateDep, db: DbDep
) -> MeOut:
    retry = state.rate_limiter.hit("setup", "global", SETUP_LIMIT_PER_MIN, 60)
    if retry is not None:
        raise AppError("rate_limited", headers={"Retry-After": str(retry)})
    if not accounts.setup_required(db):
        raise AppError("setup.already_done")
    if not state.setup_token.matches(body.setup_token):
        raise AppError(
            "setup.invalid_token", errors=[{"field": "setup_token", "message": "invalid"}]
        )
    now = state.clock.now()
    user = accounts.create_user(
        db,
        username=body.username,
        display_name=body.display_name,
        password=body.password,
        role="admin",
        now=now,
    )
    raw, session = accounts.create_session(db, user, now, client_user_agent(request))
    audit.record(
        db,
        now=now,
        actor=user.username,
        instance_name=state.instance_name,
        action="setup.completed",
        target=user.username,
    )
    db.commit()
    state.setup_token.consume()
    _set_session_cookie(response, raw)
    return me_out(state, user, session.csrf_token)


@router.post("/auth/login", response_model=MeOut, dependencies=[OriginDep])
def login(body: LoginIn, request: Request, response: Response, state: StateDep, db: DbDep) -> MeOut:
    username = body.username.strip().lower()
    retry = state.rate_limiter.hit("login", username, LOGIN_LIMIT_PER_MIN, 60)
    if retry is not None:
        raise AppError("rate_limited", headers={"Retry-After": str(retry)})
    now = state.clock.now()
    user = accounts.authenticate(db, username, body.password, now)
    if user is None:
        audit.record(
            db,
            now=now,
            actor=username[:32] or "unknown",
            instance_name=state.instance_name,
            action="auth.login",
            target=username[:256],
            outcome="failure",
        )
        db.commit()  # keep failed-attempt counters and the audit entry
        raise AppError("auth.invalid_credentials")
    raw, session = accounts.create_session(db, user, now, client_user_agent(request))
    audit.record(
        db,
        now=now,
        actor=user.username,
        instance_name=state.instance_name,
        action="auth.login",
        target=user.username,
    )
    _set_session_cookie(response, raw)
    return me_out(state, user, session.csrf_token)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    state: StateDep,
    db: DbDep,
    ctx: Annotated[AuthContext, Depends(require(Permission.ACCOUNT_SELF))],
) -> None:
    accounts.revoke_session(db, ctx.session)
    audit.record(
        db,
        now=state.clock.now(),
        actor=ctx.user.username,
        instance_name=state.instance_name,
        action="auth.logout",
        target=ctx.user.username,
    )
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="strict")


@router.get("/auth/me", response_model=MeOut)
def me(
    state: StateDep, ctx: Annotated[AuthContext, Depends(require(Permission.ACCOUNT_SELF))]
) -> MeOut:
    return me_out(state, ctx.user, ctx.session.csrf_token)


@router.post("/auth/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    body: PasswordChangeIn,
    state: StateDep,
    db: DbDep,
    ctx: Annotated[AuthContext, Depends(require(Permission.ACCOUNT_SELF))],
) -> None:
    now = state.clock.now()
    accounts.change_password(
        db, ctx.user, body.current_password, body.new_password, now, ctx.session.id_hash
    )
    audit.record(
        db,
        now=now,
        actor=ctx.user.username,
        instance_name=state.instance_name,
        action="auth.password.changed",
        target=ctx.user.username,
    )


@router.patch("/auth/preferences", response_model=MeOut)
def update_preferences(
    body: PreferencesPatch,
    state: StateDep,
    ctx: Annotated[AuthContext, Depends(require(Permission.ACCOUNT_SELF))],
) -> MeOut:
    changes = body.model_dump(exclude_none=True)
    ctx.user.preferences = {**accounts.DEFAULT_PREFERENCES, **ctx.user.preferences, **changes}
    ctx.user.updated_at = state.clock.now()
    return me_out(state, ctx.user, ctx.session.csrf_token)
