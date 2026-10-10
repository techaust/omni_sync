"""Serve the built single-page app.

``index.html`` is rendered per request: the CSP nonce is injected for libraries that
insert ``<style>`` elements, and the signed-in user's colour mode is written as a class on
``<html>`` so the right theme applies before first paint without inline script (08-UX-DESIGN 5.2).
"""

from __future__ import annotations

import html
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from omnisync.api.deps import SESSION_COOKIE
from omnisync.api.state import AppState
from omnisync.errors import AppError
from omnisync.services import accounts

NONCE_PLACEHOLDER = "__CSP_NONCE__"
THEME_PLACEHOLDER = "__THEME_CLASS__"


def _theme_class(request: Request, state: AppState) -> str:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        return ""
    with state.db_factory() as db:
        resolution = accounts.resolve_session(db, raw, state.clock.now())
        if resolution.session is None:
            return ""
        mode = resolution.session.user.preferences.get("color_mode", "system")
    return mode if mode in ("light", "dark") else ""


def mount_spa(app: FastAPI, static_dir: Path) -> None:
    index = static_dir / "index.html"
    assets = static_dir / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")
    template = index.read_text(encoding="utf-8")

    root = static_dir.resolve()

    @app.get("/{path:path}", include_in_schema=False, response_model=None)
    def spa(path: str, request: Request) -> HTMLResponse | FileResponse:
        if path.startswith("api/") or path == "api":
            raise AppError("resource.not_found")
        candidate = (static_dir / path).resolve()
        if (
            path
            and candidate.is_relative_to(root)
            and candidate.is_file()
            and candidate != index.resolve()
        ):
            # Top-level files emitted by the build (for example favicon.svg).
            return FileResponse(candidate)
        state: AppState = request.app.state.omnisync
        nonce = html.escape(request.scope["state"]["csp_nonce"], quote=True)
        page = template.replace(NONCE_PLACEHOLDER, nonce).replace(
            THEME_PLACEHOLDER, _theme_class(request, state)
        )
        return HTMLResponse(page, headers={"Cache-Control": "no-store"})
