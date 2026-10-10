"""Baseline schema for Milestone 3: instance, users, sessions, settings, audit_log.

Revision ID: 0001
Revises:
Create Date: 2026-10-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from omnisync.db.types import UTCDateTime

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

AUDIT_COLUMNS = (
    "id",
    "ts",
    "actor_user",
    "actor_instance",
    "action",
    "target",
    "outcome",
    "details",
    "via",
)


def upgrade() -> None:
    op.create_table(
        "instance",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("instance_id", sa.String(36), nullable=False),
        sa.Column("instance_name", sa.String(64), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint("id = 1", name="instance_singleton"),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(32), nullable=False, unique=True),
        sa.Column("display_name", sa.String(64), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("failed_logins", sa.Integer(), nullable=False),
        sa.Column("locked_until", UTCDateTime(), nullable=True),
        sa.Column("disabled_at", UTCDateTime(), nullable=True),
        sa.Column("preferences", sa.JSON(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint("role IN ('admin', 'operator', 'viewer')", name="users_role"),
        sa.CheckConstraint("length(username) BETWEEN 3 AND 32", name="users_username_length"),
    )
    op.create_table(
        "sessions",
        sa.Column("id_hash", sa.String(64), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("csrf_token", sa.String(64), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("last_seen_at", UTCDateTime(), nullable=False),
        sa.Column("expires_at", UTCDateTime(), nullable=False),
        sa.Column("user_agent", sa.String(256), nullable=True),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_table(
        "settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("updated_by", sa.String(32), nullable=True),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ts", UTCDateTime(), nullable=False),
        sa.Column("actor_user", sa.String(32), nullable=False),
        sa.Column("actor_instance", sa.String(64), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target", sa.String(256), nullable=True),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("via", sa.String(16), nullable=False),
        sa.Column("shared_published_at", UTCDateTime(), nullable=True),
        sa.CheckConstraint("outcome IN ('success', 'failure', 'denied')", name="audit_outcome"),
        sa.CheckConstraint(
            "via IN ('ui', 'api', 'ai_approved', 'cli', 'system')", name="audit_via"
        ),
    )
    op.create_index("ix_audit_log_ts", "audit_log", ["ts"])
    op.create_index("ix_audit_log_action", "audit_log", ["action"])

    # Append-only enforcement (FR-AUD-01, 04-DATA-MODEL section 2.5).
    op.execute(
        "CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit_log "
        "BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END"
    )
    unchanged = " AND ".join(f"NEW.{c} IS OLD.{c}" for c in AUDIT_COLUMNS)
    op.execute(
        "CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit_log "
        f"WHEN NOT (OLD.shared_published_at IS NULL AND NEW.shared_published_at IS NOT NULL AND {unchanged}) "
        "BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_no_update")
    op.execute("DROP TRIGGER IF EXISTS audit_no_delete")
    for table in ("audit_log", "settings", "sessions", "users", "instance"):
        op.drop_table(table)
