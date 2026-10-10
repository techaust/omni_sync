"""ORM models (04-DATA-MODEL section 2). Alembic migrations must match these definitions;
``tests/test_migrations.py`` fails on any drift."""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import JSON, CheckConstraint, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from omnisync.db.types import UTCDateTime

ROLES = ("admin", "operator", "viewer")


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        datetime: UTCDateTime(),
        dict[str, Any]: JSON(),
    }


class Instance(Base):
    """Singleton row identifying this instance (04-DATA-MODEL section 2.7)."""

    __tablename__ = "instance"
    __table_args__ = (CheckConstraint("id = 1", name="instance_singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_id: Mapped[str] = mapped_column(String(36))
    instance_name: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime]


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('admin', 'operator', 'viewer')", name="users_role"),
        CheckConstraint("length(username) BETWEEN 3 AND 32", name="users_username_length"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(32), unique=True)
    display_name: Mapped[str] = mapped_column(String(64))
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(16))
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None]
    disabled_at: Mapped[datetime | None]
    preferences: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]

    sessions: Mapped[list[Session]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )


class Session(Base):
    """Server-side session; only the SHA-256 of the cookie token is stored (C-14)."""

    __tablename__ = "sessions"

    id_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime]
    last_seen_at: Mapped[datetime]
    expires_at: Mapped[datetime]
    user_agent: Mapped[str | None] = mapped_column(String(256))

    user: Mapped[User] = relationship(back_populates="sessions")


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON)
    updated_by: Mapped[str | None] = mapped_column(String(32))
    updated_at: Mapped[datetime]


class AuditLog(Base):
    """Append-only audit log; enforced by triggers created in the baseline migration (FR-AUD-01)."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(index=True)
    actor_user: Mapped[str] = mapped_column(String(32))
    actor_instance: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64), index=True)
    target: Mapped[str | None] = mapped_column(String(256))
    outcome: Mapped[str] = mapped_column(String(16))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    via: Mapped[str] = mapped_column(String(16))
    shared_published_at: Mapped[datetime | None]

    __table_args__ = (
        CheckConstraint("outcome IN ('success', 'failure', 'denied')", name="audit_outcome"),
        CheckConstraint("via IN ('ui', 'api', 'ai_approved', 'cli', 'system')", name="audit_via"),
    )
