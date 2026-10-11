"""Host allow-list, security headers, request IDs and Problem Details (C-04, C-22, NFR-SEC-04)."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient


@pytest.mark.parametrize("host", ["evil.example", "127.0.0.1:9999", "localhost", "[::1]:8765", ""])
def test_unexpected_host_is_rejected_with_421(client: TestClient, host: str) -> None:
    response = client.get("/api/v1/health", headers={"Host": host})
    assert response.status_code == 421
    assert response.json()["code"] == "request.bad_host"
    assert response.headers["content-type"] == "application/problem+json"


@pytest.mark.parametrize("host", ["127.0.0.1:8765", "localhost:8765", "LOCALHOST:8765"])
def test_expected_hosts_are_accepted(client: TestClient, host: str) -> None:
    assert client.get("/api/v1/health", headers={"Host": host}).status_code == 200


def test_security_headers(client: TestClient) -> None:
    headers = client.get("/api/v1/health").headers
    csp = headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    directives = {d.split()[0]: d for d in (part.strip() for part in csp.split(";"))}
    # Scripts and style elements never allow inline code; only style attributes do (ADR-0015).
    assert "unsafe-inline" not in directives["script-src"]
    assert "unsafe-inline" not in directives["style-src"]
    assert directives["style-src-attr"] == "style-src-attr 'unsafe-inline'"
    assert "'nonce-" in directives["style-src"]
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "no-referrer"
    assert headers["x-frame-options"] == "DENY"
    assert headers["cache-control"] == "no-store"
    assert "access-control-allow-origin" not in headers


def test_nonce_differs_per_request(client: TestClient) -> None:
    first = client.get("/api/v1/health").headers["content-security-policy"]
    second = client.get("/api/v1/health").headers["content-security-policy"]
    assert first != second


def test_request_id_generated_and_valid_ids_echoed(client: TestClient) -> None:
    generated = client.get("/api/v1/health").headers["x-request-id"]
    uuid.UUID(generated)
    supplied = str(uuid.uuid4())
    assert (
        client.get("/api/v1/health", headers={"X-Request-ID": supplied}).headers["x-request-id"]
        == supplied
    )
    assert client.get("/api/v1/health", headers={"X-Request-ID": "not-a-uuid\nforged"}).headers[
        "x-request-id"
    ] != ("not-a-uuid\nforged")


def test_unknown_api_route_is_problem_json(client: TestClient) -> None:
    response = client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "resource.not_found"
    assert body["type"].endswith("resource.not_found")
    assert body["request_id"] == response.headers["x-request-id"]


def test_validation_error_shape(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"username": ""}, headers={"Origin": "http://127.0.0.1:8765"}
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation.failed"
    assert {e["field"] for e in body["errors"]} >= {"username", "password"}


def test_unhandled_errors_are_generic(client: TestClient) -> None:
    app = client.app

    @app.get("/api/v1/_boom")  # type: ignore[attr-defined, misc]
    def boom() -> None:
        raise RuntimeError("secret internal detail")

    with TestClient(app, base_url=str(client.base_url), raise_server_exceptions=False) as c:
        response = c.get("/api/v1/_boom")
    assert response.status_code == 500
    assert response.json()["code"] == "internal"
    assert "secret internal detail" not in response.text
