"""Liveness and health details (FR-OBS-01; full legacy ``check`` parity arrives in Milestone 4b)."""

from __future__ import annotations

import os
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import text

from omnisync import __version__
from omnisync.api.deps import AuthContext, DbDep, StateDep, require
from omnisync.api.schemas import HealthCheck, HealthDetailsOut, HealthOut, InstanceOut
from omnisync.db.migrate import current_revision, head_revision
from omnisync.security.permissions import Permission

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthOut)
def health() -> HealthOut:
    return HealthOut(status="ok")


@router.get("/health/details", response_model=HealthDetailsOut)
def health_details(
    state: StateDep, db: DbDep, _ctx: Annotated[AuthContext, Depends(require(Permission.VIEW))]
) -> HealthDetailsOut:
    journal = str(db.execute(text("PRAGMA journal_mode")).scalar())
    current, head = current_revision(state.engine), head_revision(state.engine)
    data_dir = state.settings.data_dir
    checks = [
        HealthCheck(
            name="database", ok=journal.lower() == "wal", detail=f"SQLite journal mode {journal}"
        ),
        HealthCheck(
            name="schema",
            ok=current == head,
            detail=f"migration {current} (latest {head})",
        ),
        HealthCheck(
            name="data_directory",
            ok=data_dir.is_dir() and os.access(data_dir, os.W_OK),
            detail=str(data_dir),
        ),
    ]
    return HealthDetailsOut(
        version=__version__,
        instance=InstanceOut(id=state.instance_id, name=state.instance_name),
        checks=checks,
    )
