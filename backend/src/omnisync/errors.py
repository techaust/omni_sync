"""Stable error codes and the domain exception type (05-API-SPEC section 3).

Every failure the API reports carries a stable ``code``; HTTP responses use
RFC 9457 Problem Details. The code catalogue below is the single source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PROBLEM_BASE = "https://omnisync.local/problems/"

# code -> (HTTP status, title)
CATALOGUE: dict[str, tuple[int, str]] = {
    "request.invalid": (400, "Invalid request"),
    "auth.required": (401, "Authentication required"),
    "auth.session_expired": (401, "Session expired"),
    "auth.invalid_credentials": (401, "Invalid username or password"),
    "auth.forbidden": (403, "Not permitted"),
    "auth.csrf_failed": (403, "Request could not be verified"),
    "resource.not_found": (404, "Not found"),
    "setup.already_done": (409, "Setup already completed"),
    "user.exists": (409, "Username already exists"),
    "user.last_admin": (409, "The last active admin cannot be removed"),
    "request.bad_host": (421, "Misdirected request"),
    "validation.failed": (422, "Validation failed"),
    "password.weak": (422, "Password does not meet the policy"),
    "setup.invalid_token": (422, "Invalid setup token"),
    "rate_limited": (429, "Too many requests"),
    "internal": (500, "Internal error"),
}


@dataclass(eq=False)
class AppError(Exception):
    """Domain error with a stable code; rendered as Problem Details by the API layer."""

    code: str
    detail: str | None = None
    errors: list[dict[str, Any]] | None = None
    headers: dict[str, str] | None = None

    def __post_init__(self) -> None:
        if self.code not in CATALOGUE:
            raise ValueError(f"unknown error code {self.code!r}")
        super().__init__(self.code)

    @property
    def status(self) -> int:
        return CATALOGUE[self.code][0]

    @property
    def title(self) -> str:
        return CATALOGUE[self.code][1]


def problem(
    code: str,
    *,
    detail: str | None = None,
    instance: str | None = None,
    request_id: str | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    status, title = CATALOGUE[code]
    body: dict[str, Any] = {
        "type": PROBLEM_BASE + code,
        "title": title,
        "status": status,
        "code": code,
    }
    if detail:
        body["detail"] = detail
    if instance:
        body["instance"] = instance
    if request_id:
        body["request_id"] = request_id
    if errors:
        body["errors"] = errors
    return body
