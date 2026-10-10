"""Random tokens and the first-run setup token (FR-AUTH-01, C-14)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import sys
from pathlib import Path


def new_session_token() -> str:
    """256-bit random token for the session cookie."""
    return secrets.token_urlsafe(32)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _normalise_setup_token(value: str) -> str:
    return "".join(ch for ch in value.upper() if ch.isalnum())


def _group(raw: str, size: int = 5) -> str:
    return "-".join(raw[i : i + size] for i in range(0, len(raw), size))


class SetupToken:
    """One-time 128-bit token (26 base32 characters) required to create the first admin.

    A fresh token is generated at every start while setup is required, printed to the
    console and written to ``setup-token.txt`` so it can be read when the server was
    started without a visible console (FR-AUTH-01).
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._raw: str | None = None

    @property
    def display(self) -> str | None:
        return _group(self._raw) if self._raw else None

    def generate(self) -> str:
        self._raw = base64.b32encode(secrets.token_bytes(16)).decode("ascii").rstrip("=")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(_group(self._raw) + "\n", encoding="utf-8")
        if sys.platform != "win32":
            os.chmod(tmp, 0o600)
        # On Windows the default data directory (%LOCALAPPDATA%) is already private to the user.
        tmp.replace(self._path)
        return _group(self._raw)

    def matches(self, candidate: str) -> bool:
        if self._raw is None:
            return False
        return hmac.compare_digest(_normalise_setup_token(candidate), self._raw)

    def consume(self) -> None:
        self._raw = None
        self._path.unlink(missing_ok=True)
