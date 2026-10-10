"""Append-only audit records (FR-AUD-01). Written in the caller's transaction so that the
audit entry and the change it describes commit or roll back together."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from sqlalchemy.orm import Session as DbSession

from omnisync.db.models import AuditLog

Outcome = Literal["success", "failure", "denied"]
Via = Literal["ui", "api", "ai_approved", "cli", "system"]


def record(
    db: DbSession,
    *,
    now: datetime,
    actor: str,
    instance_name: str,
    action: str,
    target: str | None = None,
    outcome: Outcome = "success",
    details: dict[str, Any] | None = None,
    via: Via = "ui",
) -> AuditLog:
    entry = AuditLog(
        ts=now,
        actor_user=actor,
        actor_instance=instance_name,
        action=action,
        target=target,
        outcome=outcome,
        details=details or {},
        via=via,
    )
    db.add(entry)
    return entry
