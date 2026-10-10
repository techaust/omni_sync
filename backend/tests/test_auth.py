"""Login, sessions, lockout, CSRF, password and preferences (FR-AUTH-02, ADR-0012)."""

from __future__ import annotations

from collections.abc import Callable

from fastapi.testclient import TestClient

from omnisync.clock import FakeClock
from tests.conftest import ADMIN_PASSWORD, ORIGIN, STRONG_PASSWORD, Session, login

LOGIN = "/api/v1/auth/login"


def _comparable(body: dict[str, object]) -> dict[str, object]:
    return {k: v for k, v in body.items() if k not in ("request_id", "instance")}


def test_login_and_me(client: TestClient, admin: Session) -> None:
    session = login(client, "owner", ADMIN_PASSWORD)
    me = client.get("/api/v1/auth/me").json()
    assert me["username"] == "owner"
    assert me["csrf_token"] == session.csrf
    assert me["instance"]["name"] == "test-pc"
    assert me["preferences"] == {
        "color_mode": "system",
        "density": "compact",
        "browser_notifications": False,
    }


def test_username_is_case_insensitive(client: TestClient, admin: Session) -> None:
    login(client, "OWNER", ADMIN_PASSWORD)


def test_me_requires_authentication(client: TestClient) -> None:
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.json()["code"] == "auth.required"


def test_wrong_password_and_unknown_user_are_indistinguishable(
    client: TestClient, admin: Session
) -> None:
    client.cookies.clear()
    wrong = client.post(
        LOGIN, json={"username": "owner", "password": "nope-nope-nope"}, headers=ORIGIN
    )
    unknown = client.post(
        LOGIN, json={"username": "ghost", "password": "nope-nope-nope"}, headers=ORIGIN
    )
    assert wrong.status_code == unknown.status_code == 401
    assert _comparable(wrong.json()) == {**_comparable(unknown.json())}
    assert wrong.json()["code"] == "auth.invalid_credentials"


def test_lockout_after_ten_failures_looks_like_wrong_password(
    client: TestClient, admin: Session, clock: FakeClock
) -> None:
    client.cookies.clear()
    for _ in range(10):
        clock.advance(seconds=7)  # stay under the 10/min login rate limit
        assert (
            client.post(
                LOGIN, json={"username": "owner", "password": "bad-password-x"}, headers=ORIGIN
            ).status_code
            == 401
        )
    clock.advance(seconds=7)
    locked = client.post(
        LOGIN, json={"username": "owner", "password": ADMIN_PASSWORD}, headers=ORIGIN
    )
    assert locked.status_code == 401
    assert locked.json()["code"] == "auth.invalid_credentials"
    clock.advance(minutes=15)
    login(client, "owner", ADMIN_PASSWORD)


def test_login_rate_limit(client: TestClient, admin: Session) -> None:
    client.cookies.clear()
    codes = [
        client.post(
            LOGIN, json={"username": "owner", "password": "bad-password-x"}, headers=ORIGIN
        ).status_code
        for _ in range(11)
    ]
    assert codes[-1] == 429


def test_state_changing_request_requires_csrf_token(client: TestClient, admin: Session) -> None:
    response = client.post("/api/v1/auth/logout", headers=ORIGIN)
    assert response.status_code == 403
    assert response.json()["code"] == "auth.csrf_failed"
    assert admin.post("/api/v1/auth/logout").status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401


def test_cross_origin_request_with_valid_csrf_is_rejected(
    client: TestClient, admin: Session
) -> None:
    response = client.post(
        "/api/v1/auth/logout", headers={"X-CSRF-Token": admin.csrf, "Origin": "http://evil.test"}
    )
    assert response.status_code == 403


def test_session_idle_timeout(client: TestClient, admin: Session, clock: FakeClock) -> None:
    clock.advance(hours=11, minutes=59)
    assert client.get("/api/v1/auth/me").status_code == 200
    clock.advance(hours=12)
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.json()["code"] == "auth.session_expired"


def test_session_absolute_lifetime(client: TestClient, admin: Session, clock: FakeClock) -> None:
    for _ in range(15):  # stay active (never idle 12 h): 15 x 11 h = 165 h
        clock.advance(hours=11)
        assert client.get("/api/v1/auth/me").status_code == 200
    clock.advance(hours=4)  # 169 h > 7 days, but only 4 h idle: expiry is due to the absolute limit
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.json()["code"] == "auth.session_expired"


def test_password_change_revokes_other_sessions(client: TestClient, admin: Session) -> None:
    other = TestClient(client.app, base_url=str(client.base_url))
    other_session = login(other, "owner", ADMIN_PASSWORD)
    new_password = "a-much-better-passphrase-77"
    response = admin.post(
        "/api/v1/auth/password",
        json={"current_password": ADMIN_PASSWORD, "new_password": new_password},
    )
    assert response.status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 200  # current session kept
    assert other_session.client.get("/api/v1/auth/me").status_code == 401
    login(client, "owner", new_password)


def test_password_change_checks_current_and_policy(client: TestClient, admin: Session) -> None:
    wrong = admin.post(
        "/api/v1/auth/password",
        json={"current_password": "x", "new_password": "a-much-better-passphrase-77"},
    )
    assert wrong.status_code == 422
    weak = admin.post(
        "/api/v1/auth/password", json={"current_password": ADMIN_PASSWORD, "new_password": "short"}
    )
    assert weak.json()["code"] == "password.weak"


def test_preferences_update_and_validation(client: TestClient, admin: Session) -> None:
    response = admin.patch("/api/v1/auth/preferences", json={"color_mode": "dark"})
    assert response.status_code == 200
    assert response.json()["preferences"]["color_mode"] == "dark"
    assert response.json()["preferences"]["density"] == "compact"
    assert admin.patch("/api/v1/auth/preferences", json={"color_mode": "purple"}).status_code == 422
    assert admin.patch("/api/v1/auth/preferences", json={"unknown": 1}).status_code == 422


def test_disabled_user_cannot_log_in_and_is_signed_out(
    client: TestClient, admin: Session, make_user: Callable[[str, str], None]
) -> None:
    make_user("colleague", "operator")
    colleague_client = TestClient(client.app, base_url=str(client.base_url))
    login(colleague_client, "colleague", STRONG_PASSWORD)
    users = client.get("/api/v1/users").json()["items"]
    colleague_id = next(u["id"] for u in users if u["username"] == "colleague")
    assert admin.patch(f"/api/v1/users/{colleague_id}", json={"disabled": True}).status_code == 200
    assert colleague_client.get("/api/v1/auth/me").status_code == 401
    colleague_client.cookies.clear()
    response = colleague_client.post(
        LOGIN, json={"username": "colleague", "password": STRONG_PASSWORD}, headers=ORIGIN
    )
    assert response.status_code == 401
