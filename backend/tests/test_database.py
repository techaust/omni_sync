"""Migrations, durability pragmas and audit append-only triggers (NFR-REL-02, FR-AUD-01)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine, text
from sqlalchemy.exc import DatabaseError

from omnisync.db.engine import make_engine, make_sessionmaker, transaction
from omnisync.db.migrate import current_revision, head_revision, upgrade_to_head
from omnisync.db.models import AuditLog, Base
from omnisync.services import audit


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    engine = make_engine(tmp_path / "test.db")
    upgrade_to_head(engine)
    yield engine  # type: ignore[misc]
    engine.dispose()


def test_upgrade_reaches_head_and_is_idempotent(engine: Engine) -> None:
    assert current_revision(engine) == head_revision(engine) == "0001"
    upgrade_to_head(engine)
    assert current_revision(engine) == "0001"


def test_models_match_migrations(engine: Engine) -> None:
    with engine.connect() as conn:
        diff = compare_metadata(
            MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata
        )
    assert diff == []


def test_pragmas(engine: Engine) -> None:
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert conn.execute(text("PRAGMA synchronous")).scalar() == 2  # FULL
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1


def _insert_audit(engine: Engine) -> int:
    with transaction(make_sessionmaker(engine)) as db:
        entry = audit.record(
            db,
            now=datetime(2026, 10, 11, tzinfo=UTC),
            actor="owner",
            instance_name="pc",
            action="test.action",
        )
        db.flush()
        return entry.id


def test_audit_rows_cannot_be_deleted(engine: Engine) -> None:
    entry_id = _insert_audit(engine)
    with engine.begin() as conn, pytest.raises(DatabaseError, match="append-only"):
        conn.execute(text("DELETE FROM audit_log WHERE id = :id"), {"id": entry_id})


def test_audit_rows_cannot_be_modified(engine: Engine) -> None:
    entry_id = _insert_audit(engine)
    with engine.begin() as conn, pytest.raises(DatabaseError, match="append-only"):
        conn.execute(
            text("UPDATE audit_log SET outcome = 'failure' WHERE id = :id"), {"id": entry_id}
        )


def test_audit_publication_timestamp_may_be_set_once(engine: Engine) -> None:
    entry_id = _insert_audit(engine)
    stamp = "2026-10-11T10:00:00.000000Z"
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE audit_log SET shared_published_at = :t WHERE id = :id"),
            {"t": stamp, "id": entry_id},
        )
    with engine.begin() as conn, pytest.raises(DatabaseError, match="append-only"):
        conn.execute(
            text("UPDATE audit_log SET shared_published_at = :t WHERE id = :id"),
            {"t": "2026-10-12T10:00:00.000000Z", "id": entry_id},
        )
    with engine.begin() as conn, pytest.raises(DatabaseError, match="append-only"):
        conn.execute(
            text(
                "UPDATE audit_log SET action = 'forged', shared_published_at = NULL WHERE id = :id"
            ),
            {"id": entry_id},
        )


def test_audit_check_constraints(engine: Engine) -> None:
    with transaction(make_sessionmaker(engine)) as db:
        db.add(
            AuditLog(
                ts=datetime(2026, 10, 11, tzinfo=UTC),
                actor_user="x",
                actor_instance="pc",
                action="a",
                outcome="success",
                details={},
                via="ui",
            )
        )
    with pytest.raises(DatabaseError), transaction(make_sessionmaker(engine)) as db:
        db.add(
            AuditLog(
                ts=datetime(2026, 10, 11, tzinfo=UTC),
                actor_user="x",
                actor_instance="pc",
                action="a",
                outcome="maybe",
                details={},
                via="ui",
            )
        )


def test_naive_datetimes_are_refused(engine: Engine) -> None:
    from sqlalchemy.exc import StatementError

    with transaction(make_sessionmaker(engine)) as db:
        audit.record(db, now=datetime(2026, 10, 11), actor="x", instance_name="pc", action="a")  # noqa: DTZ001
        with pytest.raises(StatementError):
            db.flush()
        db.rollback()
