"""Permission matrix: every API route x every role (FR-AUTH-03, 06-SECURITY section 4).

Routes are discovered from the application itself, so a new endpoint without a declared
permission fails this test instead of silently becoming public.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from omnisync.api.app import API_PREFIX, API_ROUTERS
from omnisync.api.deps import PermissionDependency
from omnisync.security.permissions import ROLE_PERMISSIONS, Permission
from tests.conftest import ORIGIN, STRONG_PASSWORD, Session, login

PUBLIC = {
    ("GET", "/api/v1/health"),
    ("GET", "/api/v1/setup/status"),
    ("POST", "/api/v1/setup"),
    ("POST", "/api/v1/auth/login"),
}


def _permissions(dependant: Dependant) -> list[Permission]:
    found: list[Permission] = []
    if isinstance(dependant.call, PermissionDependency):
        found.append(dependant.call.permission)
    for sub in dependant.dependencies:
        found.extend(_permissions(sub))
    return found


def _api_routes(client: TestClient) -> list[tuple[str, str, list[Permission]]]:
    """Walk the public ``APIRouter.routes`` of every registered router."""
    routes = []
    for router in API_ROUTERS:
        for route in router.routes:
            assert isinstance(route, APIRoute)
            perms = _permissions(route.dependant)
            routes.extend(
                (method, API_PREFIX + route.path, perms)
                for method in sorted(route.methods - {"HEAD"})
            )
    return routes


def test_router_registry_matches_openapi(client: TestClient) -> None:
    """Guards against an endpoint being added outside API_ROUTERS (and so escaping this test)."""
    schema = client.get("/api/v1/openapi.json").json()
    documented = {(m.upper(), path) for path, ops in schema["paths"].items() for m in ops}
    registered = {(m, p) for m, p, _ in _api_routes(client)}
    assert documented == registered


def test_every_route_declares_exactly_one_permission_or_is_public(client: TestClient) -> None:
    routes = _api_routes(client)
    assert len(routes) >= 12, f"route discovery looks broken: {routes}"
    for method, path, perms in routes:
        if (method, path) in PUBLIC:
            assert perms == [], f"{method} {path} is public but declares {perms}"
        else:
            assert len(perms) == 1, (
                f"{method} {path} must declare exactly one permission, found {perms}"
            )


@pytest.mark.parametrize("role", ["viewer", "operator", "admin"])
def test_matrix(
    role: str, client: TestClient, admin: Session, make_user: Callable[[str, str], None]
) -> None:
    username = f"{role}-user"
    make_user(username, role)
    session = login(client, username, STRONG_PASSWORD)
    for method, path, perms in _api_routes(client):
        if (method, path) in PUBLIC:
            continue
        url = path.replace("{user_id}", "1")
        response = client.request(method, url, headers=session.headers(), json={})
        allowed = perms[0] in ROLE_PERMISSIONS[role]
        if allowed:
            assert response.status_code not in (401, 403), (
                f"{role} {method} {path}: {response.status_code}"
            )
        else:
            assert response.status_code == 403, f"{role} {method} {path}: {response.status_code}"
            assert response.json()["code"] == "auth.forbidden"
        if method == "POST" and path.endswith("/logout") and allowed:
            session = login(client, username, STRONG_PASSWORD)


def test_anonymous_requests_are_rejected(client: TestClient) -> None:
    for method, path, _ in _api_routes(client):
        if (method, path) in PUBLIC:
            continue
        response = client.request(method, path.replace("{user_id}", "1"), headers=ORIGIN, json={})
        assert response.status_code == 401, f"{method} {path}"
