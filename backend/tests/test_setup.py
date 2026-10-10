"""First-run setup (FR-AUTH-01)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from omnisync.api.app import create_app
from omnisync.clock import FakeClock
from omnisync.settings import Settings
from tests.conftest import ADMIN_PASSWORD, BASE_URL, ORIGIN, do_setup, read_setup_token


def _setup_body(token: str, password: str = ADMIN_PASSWORD) -> dict[str, str]:
    return {
        "setup_token": token,
        "username": "owner",
        "display_name": "Owner",
        "password": password,
    }


def test_status_reports_setup_required(client: TestClient) -> None:
    assert client.get("/api/v1/setup/status").json() == {"setup_required": True}


def test_token_file_has_grouped_128_bit_token(client: TestClient, settings: Settings) -> None:
    token = read_setup_token(settings)
    assert len(token.replace("-", "")) == 26
    assert token.count("-") == 5


def test_wrong_token_is_rejected(client: TestClient) -> None:
    response = client.post("/api/v1/setup", json=_setup_body("AAAAA-AAAAA"), headers=ORIGIN)
    assert response.status_code == 422
    assert response.json()["code"] == "setup.invalid_token"
    assert response.headers["content-type"] == "application/problem+json"


def test_token_is_accepted_without_dashes_and_lowercase(
    client: TestClient, settings: Settings
) -> None:
    token = read_setup_token(settings).replace("-", "").lower()
    response = client.post("/api/v1/setup", json=_setup_body(token), headers=ORIGIN)
    assert response.status_code == 201


def test_weak_password_is_rejected(client: TestClient, settings: Settings) -> None:
    response = client.post(
        "/api/v1/setup", json=_setup_body(read_setup_token(settings), "password123"), headers=ORIGIN
    )
    assert response.status_code == 422
    assert response.json()["code"] == "password.weak"


def test_cross_origin_setup_is_rejected(client: TestClient, settings: Settings) -> None:
    response = client.post(
        "/api/v1/setup",
        json=_setup_body(read_setup_token(settings)),
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "auth.csrf_failed"


def test_cross_site_fetch_metadata_is_rejected(client: TestClient, settings: Settings) -> None:
    response = client.post(
        "/api/v1/setup",
        json=_setup_body(read_setup_token(settings)),
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert response.status_code == 403


def test_successful_setup_signs_in_and_consumes_token(
    client: TestClient, settings: Settings
) -> None:
    session = do_setup(client, settings)
    assert session.me["role"] == "admin"
    assert "users.manage" in session.me["permissions"]
    assert not settings.setup_token_path.exists()
    cookie = client.cookies.get("omnisync_session")
    assert cookie
    assert client.get("/api/v1/setup/status").json() == {"setup_required": False}


def test_setup_cookie_attributes(client: TestClient, settings: Settings) -> None:
    response = client.post(
        "/api/v1/setup", json=_setup_body(read_setup_token(settings)), headers=ORIGIN
    )
    set_cookie = response.headers["set-cookie"].lower()
    assert "httponly" in set_cookie
    assert "samesite=strict" in set_cookie
    assert "path=/" in set_cookie


def test_second_setup_is_refused(client: TestClient, settings: Settings) -> None:
    token = read_setup_token(settings)
    do_setup(client, settings)
    response = client.post("/api/v1/setup", json=_setup_body(token), headers=ORIGIN)
    assert response.status_code == 409
    assert response.json()["code"] == "setup.already_done"


def test_setup_is_rate_limited(client: TestClient) -> None:
    codes = [
        client.post("/api/v1/setup", json=_setup_body("WRONG"), headers=ORIGIN).status_code
        for _ in range(6)
    ]
    assert codes[:5] == [422] * 5
    assert codes[5] == 429


def test_restart_generates_a_new_token(settings: Settings) -> None:
    first_app: FastAPI = create_app(settings, clock=FakeClock())
    with TestClient(first_app, base_url=BASE_URL):
        first = read_setup_token(settings)
    with TestClient(create_app(settings, clock=FakeClock()), base_url=BASE_URL) as second_client:
        second = read_setup_token(settings)
        assert first != second
        response = second_client.post("/api/v1/setup", json=_setup_body(first), headers=ORIGIN)
        assert response.status_code == 422


def test_no_token_file_once_setup_is_done(settings: Settings) -> None:
    with TestClient(create_app(settings, clock=FakeClock()), base_url=BASE_URL) as c:
        do_setup(c, settings)
    with TestClient(create_app(settings, clock=FakeClock()), base_url=BASE_URL):
        assert not settings.setup_token_path.exists()
