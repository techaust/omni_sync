from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omnisync.api.app import create_app
from omnisync.clock import FakeClock
from omnisync.settings import Settings

BASE_URL = "http://127.0.0.1:8765"
ORIGIN = {"Origin": BASE_URL}
ADMIN_PASSWORD = "correct horse battery staple"
STRONG_PASSWORD = "violet-anchor-meadow-42"


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def settings(data_dir: Path) -> Settings:
    return Settings(data_dir=data_dir, port=8765, instance_name="test-pc", static_dir=None)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def app(settings: Settings, clock: FakeClock) -> FastAPI:
    return create_app(settings, clock=clock)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, base_url=BASE_URL) as c:
        yield c


def read_setup_token(settings: Settings) -> str:
    return settings.setup_token_path.read_text(encoding="utf-8").strip()


@dataclass
class Session:
    client: TestClient
    csrf: str
    me: dict[str, Any]

    def headers(self) -> dict[str, str]:
        return {"X-CSRF-Token": self.csrf, **ORIGIN}

    def post(self, url: str, **kw: Any) -> Any:
        return self.client.post(url, headers=self.headers(), **kw)

    def patch(self, url: str, **kw: Any) -> Any:
        return self.client.patch(url, headers=self.headers(), **kw)


def do_setup(client: TestClient, settings: Settings, username: str = "owner") -> Session:
    response = client.post(
        "/api/v1/setup",
        json={
            "setup_token": read_setup_token(settings),
            "username": username,
            "display_name": "Owner",
            "password": ADMIN_PASSWORD,
        },
        headers=ORIGIN,
    )
    assert response.status_code == 201, response.text
    me = response.json()
    return Session(client, me["csrf_token"], me)


def login(client: TestClient, username: str, password: str) -> Session:
    client.cookies.clear()
    response = client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}, headers=ORIGIN
    )
    assert response.status_code == 200, response.text
    me = response.json()
    return Session(client, me["csrf_token"], me)


@pytest.fixture
def admin(client: TestClient, settings: Settings) -> Session:
    return do_setup(client, settings)


@pytest.fixture
def make_user(admin: Session) -> Callable[[str, str], None]:
    def _make(username: str, role: str) -> None:
        response = admin.post(
            "/api/v1/users",
            json={
                "username": username,
                "display_name": username.title(),
                "role": role,
                "password": STRONG_PASSWORD,
            },
        )
        assert response.status_code == 201, response.text

    return _make
