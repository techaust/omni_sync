"""SPA serving: nonce injection, pre-paint theme class, caching, traversal (03-ARCHITECTURE section 8)."""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from omnisync.api.app import create_app
from omnisync.clock import FakeClock
from omnisync.settings import Settings
from tests.conftest import BASE_URL, do_setup

INDEX = (
    '<!doctype html><html lang="en" class="__THEME_CLASS__"><head>'
    '<meta property="csp-nonce" content="__CSP_NONCE__"></head><body><div id="root"></div></body></html>'
)


@pytest.fixture
def spa_client(tmp_path: Path) -> Iterator[tuple[TestClient, Settings]]:
    static = tmp_path / "dist"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text(INDEX, encoding="utf-8")
    (static / "assets" / "app-abc123.js").write_text("console.log(1)", encoding="utf-8")
    (static / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("do not serve", encoding="utf-8")
    settings = Settings(
        data_dir=tmp_path / "data", port=8765, instance_name="pc", static_dir=static
    )
    with TestClient(create_app(settings, clock=FakeClock()), base_url=BASE_URL) as client:
        yield client, settings


def test_index_gets_request_nonce(spa_client: tuple[TestClient, Settings]) -> None:
    client, _ = spa_client
    response = client.get("/runs/42")
    assert response.status_code == 200
    nonce = re.search(r'content="([^"]+)"', response.text)
    assert nonce is not None
    assert f"'nonce-{nonce.group(1)}'" in response.headers["content-security-policy"]
    assert "__CSP_NONCE__" not in response.text
    assert 'class=""' in response.text  # anonymous: system mode, resolved in CSS
    assert response.headers["cache-control"] == "no-store"


def test_theme_class_follows_user_preference(spa_client: tuple[TestClient, Settings]) -> None:
    client, settings = spa_client
    session = do_setup(client, settings)
    session.patch("/api/v1/auth/preferences", json={"color_mode": "dark"})
    assert 'class="dark"' in client.get("/").text
    session.patch("/api/v1/auth/preferences", json={"color_mode": "system"})
    assert 'class=""' in client.get("/").text


def test_assets_are_immutable(spa_client: tuple[TestClient, Settings]) -> None:
    client, _ = spa_client
    response = client.get("/assets/app-abc123.js")
    assert response.status_code == 200
    assert "immutable" in response.headers["cache-control"]


def test_top_level_files_served_with_their_type(spa_client: tuple[TestClient, Settings]) -> None:
    client, _ = spa_client
    response = client.get("/favicon.svg")
    assert response.headers["content-type"].startswith("image/svg+xml")


def test_traversal_outside_dist_is_not_served(spa_client: tuple[TestClient, Settings]) -> None:
    client, _ = spa_client
    for path in ("/..%2Fsecret.txt", "/%2E%2E/secret.txt"):
        assert "do not serve" not in client.get(path).text


def test_unknown_api_paths_stay_json(spa_client: tuple[TestClient, Settings]) -> None:
    client, _ = spa_client
    response = client.get("/api/v1/nope")
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
