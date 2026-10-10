"""User management (FR-AUTH-04)."""

from __future__ import annotations

from collections.abc import Callable

from fastapi.testclient import TestClient

from tests.conftest import STRONG_PASSWORD, Session, login


def _user_id(client: TestClient, username: str) -> int:
    return next(
        u["id"] for u in client.get("/api/v1/users").json()["items"] if u["username"] == username
    )


def test_admin_creates_and_lists_users(
    client: TestClient, admin: Session, make_user: Callable[[str, str], None]
) -> None:
    make_user("colleague", "operator")
    items = client.get("/api/v1/users").json()["items"]
    assert [(u["username"], u["role"], u["disabled"]) for u in items] == [
        ("colleague", "operator", False),
        ("owner", "admin", False),
    ]
    login(client, "colleague", STRONG_PASSWORD)


def test_duplicate_and_invalid_usernames(client: TestClient, admin: Session) -> None:
    body = {"username": "owner", "display_name": "X", "role": "viewer", "password": STRONG_PASSWORD}
    assert admin.post("/api/v1/users", json=body).json()["code"] == "user.exists"
    bad = admin.post("/api/v1/users", json={**body, "username": "Bad Name!"})
    assert bad.status_code == 422
    assert bad.json()["errors"][0]["field"] == "username"


def test_unknown_role_rejected(client: TestClient, admin: Session) -> None:
    body = {
        "username": "someone",
        "display_name": "X",
        "role": "superuser",
        "password": STRONG_PASSWORD,
    }
    assert admin.post("/api/v1/users", json=body).status_code == 422


def test_last_admin_cannot_be_demoted_or_disabled(client: TestClient, admin: Session) -> None:
    owner_id = _user_id(client, "owner")
    for change in ({"role": "operator"}, {"disabled": True}):
        response = admin.patch(f"/api/v1/users/{owner_id}", json=change)
        assert response.status_code == 409
        assert response.json()["code"] == "user.last_admin"


def test_second_admin_allows_demotion(
    client: TestClient, admin: Session, make_user: Callable[[str, str], None]
) -> None:
    make_user("second", "admin")
    response = admin.patch(f"/api/v1/users/{_user_id(client, 'owner')}", json={"role": "viewer"})
    assert response.status_code == 200
    assert response.json()["role"] == "viewer"


def test_admin_password_reset_unlocks_and_revokes(
    client: TestClient, admin: Session, make_user: Callable[[str, str], None]
) -> None:
    make_user("colleague", "operator")
    other = TestClient(client.app, base_url=str(client.base_url))
    login(other, "colleague", STRONG_PASSWORD)
    new_password = "fresh-river-stone-2026"
    response = admin.patch(
        f"/api/v1/users/{_user_id(client, 'colleague')}", json={"new_password": new_password}
    )
    assert response.status_code == 200
    assert other.get("/api/v1/auth/me").status_code == 401
    login(other, "colleague", new_password)


def test_unknown_user_is_404(client: TestClient, admin: Session) -> None:
    response = admin.patch("/api/v1/users/9999", json={"display_name": "X"})
    assert response.status_code == 404
    assert response.json()["code"] == "resource.not_found"


def test_user_changes_are_audited(
    client: TestClient, admin: Session, make_user: Callable[[str, str], None]
) -> None:
    from sqlalchemy import select

    from omnisync.db.models import AuditLog

    make_user("colleague", "operator")
    admin.patch(f"/api/v1/users/{_user_id(client, 'colleague')}", json={"role": "viewer"})
    state = client.app.state.omnisync  # type: ignore[attr-defined]
    with state.db_factory() as db:
        actions = [
            (a.action, a.target, a.outcome)
            for a in db.scalars(select(AuditLog).order_by(AuditLog.id))
        ]
    assert ("setup.completed", "owner", "success") in actions
    assert ("user.created", "colleague", "success") in actions
    assert ("user.updated", "colleague", "success") in actions
