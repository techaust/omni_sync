"""The request transaction commits before the response is sent.

Regression test for a bug found by the WebKit end-to-end run: FastAPI runs the cleanup of a
``yield`` dependency after the response by default (``scope="request"``), so the client could
receive ``201 Created`` before the commit happened. A fast follow-up read then missed the new row,
and a failing commit would have been reported to the client as success.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from omnisync.db.models import User
from tests.conftest import STRONG_PASSWORD, Session

NEW_USER = {
    "username": "colleague",
    "display_name": "Colleague",
    "role": "operator",
    "password": STRONG_PASSWORD,
}


def test_failed_commit_is_reported_to_the_client(
    client: TestClient, admin: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_commit(self: DbSession, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError("disk full")

    # Not used as a context manager: the app is already started, and start-up must not re-run.
    other = TestClient(client.app, base_url=str(client.base_url), raise_server_exceptions=False)
    other.cookies.update(client.cookies)
    monkeypatch.setattr(DbSession, "commit", failing_commit)
    response = other.post("/api/v1/users", json=NEW_USER, headers=admin.headers())
    monkeypatch.undo()

    assert response.status_code == 500, "a commit failure must never be reported as success"
    state = client.app.state.omnisync  # type: ignore[attr-defined]
    with state.db_factory() as db:
        assert db.scalar(select(User).where(User.username == "colleague")) is None


def test_write_is_visible_to_an_immediate_read(client: TestClient, admin: Session) -> None:
    assert admin.post("/api/v1/users", json=NEW_USER).status_code == 201
    usernames = [u["username"] for u in client.get("/api/v1/users").json()["items"]]
    assert "colleague" in usernames
