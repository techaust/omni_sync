"""Pure-ASGI security middleware (works with streaming/SSE responses).

Per request it: assigns a request ID, rejects unexpected Host headers with 421
(DNS-rebinding defence, C-04), generates a CSP nonce, and adds security headers (C-22).
"""

from __future__ import annotations

import json
import logging
import secrets
import time
import uuid
from collections.abc import Iterable

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from omnisync.errors import problem
from omnisync.logging_setup import request_id_var

log = logging.getLogger("omnisync.http")


def content_security_policy(nonce: str) -> str:
    return "; ".join(
        (
            "default-src 'self'",
            "script-src 'self'",
            f"style-src 'self' 'nonce-{nonce}'",
            "img-src 'self' data:",
            "font-src 'self'",
            "connect-src 'self'",
            "worker-src 'self'",
            "frame-ancestors 'none'",
            "base-uri 'none'",
            "form-action 'self'",
            "object-src 'none'",
        )
    )


def _valid_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


class SecurityMiddleware:
    def __init__(self, app: ASGIApp, allowed_hosts: Iterable[str]) -> None:
        self.app = app
        self.allowed_hosts = frozenset(h.lower() for h in allowed_hosts)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        supplied = headers.get("x-request-id", "")
        request_id = supplied if _valid_uuid(supplied) else str(uuid.uuid4())
        token = request_id_var.set(request_id)
        nonce = secrets.token_urlsafe(16)
        scope.setdefault("state", {})
        scope["state"]["csp_nonce"] = nonce
        scope["state"]["request_id"] = request_id
        path: str = scope.get("path", "")
        started = time.perf_counter()
        status_holder = {"status": 0}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                raw = list(message.get("headers", []))
                existing = {k.lower() for k, _ in raw}
                extra = {
                    b"x-request-id": request_id.encode(),
                    b"content-security-policy": content_security_policy(nonce).encode(),
                    b"x-content-type-options": b"nosniff",
                    b"referrer-policy": b"no-referrer",
                    b"x-frame-options": b"DENY",
                    b"cross-origin-opener-policy": b"same-origin",
                    b"cross-origin-resource-policy": b"same-origin",
                }
                if path.startswith("/api/"):
                    extra[b"cache-control"] = b"no-store"
                elif path.startswith("/assets/"):
                    extra[b"cache-control"] = b"public, max-age=31536000, immutable"
                for key, value in extra.items():
                    if key not in existing or key == b"cache-control":
                        raw = [(k, v) for k, v in raw if k.lower() != key]
                        raw.append((key, value))
                message["headers"] = raw
            await send(message)

        try:
            host = headers.get("host", "").lower()
            if host not in self.allowed_hosts:
                body = json.dumps(problem("request.bad_host", request_id=request_id)).encode()
                await send_wrapper(
                    {
                        "type": "http.response.start",
                        "status": 421,
                        "headers": [
                            (b"content-type", b"application/problem+json"),
                            (b"content-length", str(len(body)).encode()),
                        ],
                    }
                )
                await send_wrapper({"type": "http.response.body", "body": body})
                log.warning(
                    "rejected request with unexpected Host header",
                    extra={"data": {"host": host[:100]}},
                )
                return
            await self.app(scope, receive, send_wrapper)
        finally:
            if path.startswith("/api/"):
                log.info(
                    "%s %s -> %s",
                    scope.get("method"),
                    path,
                    status_holder["status"],
                    extra={"data": {"ms": round((time.perf_counter() - started) * 1000, 1)}},
                )
            request_id_var.reset(token)
