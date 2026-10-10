"""Alembic environment: always driven programmatically with an Engine (see omnisync.db.migrate)."""

from __future__ import annotations

from alembic import context
from sqlalchemy import Engine

from omnisync.db.models import Base

engine = context.config.attributes.get("engine")
if not isinstance(engine, Engine):  # pragma: no cover - misuse guard
    raise TypeError("Run migrations through omnisync.db.migrate, which supplies the engine.")

with engine.connect() as connection:
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        render_as_batch=True,  # SQLite needs batch mode for ALTER TABLE
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()
