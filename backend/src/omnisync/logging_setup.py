"""Structured JSON logging with secret redaction (NFR-OBS-01, C-05).

Every line is one JSON object with ``ts``, ``level``, ``logger``, ``event`` and the
context fields ``request_id``, ``run_id`` and ``item_id`` when set. Registered secret
values are replaced by ``[REDACTED]`` before anything is written.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import threading
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
run_id_var: ContextVar[int | None] = ContextVar("run_id", default=None)
item_id_var: ContextVar[int | None] = ContextVar("item_id", default=None)

_SECRETS: set[str] = set()
_SECRETS_LOCK = threading.Lock()
_MIN_SECRET_LEN = 6
REDACTED = "[REDACTED]"


def register_secret(value: str | None) -> None:
    """Make sure ``value`` never appears in log output."""
    if value and len(value) >= _MIN_SECRET_LEN:
        with _SECRETS_LOCK:
            _SECRETS.add(value)


def redact(text: str) -> str:
    with _SECRETS_LOCK:
        secrets = sorted(_SECRETS, key=len, reverse=True)
    for secret in secrets:
        if secret in text:
            text = text.replace(secret, REDACTED)
    return text


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for key, var in (
            ("request_id", request_id_var),
            ("run_id", run_id_var),
            ("item_id", item_id_var),
        ):
            value = var.get()
            if value is not None:
                payload[key] = value
        extra = getattr(record, "data", None)
        if isinstance(extra, dict):
            payload["data"] = extra
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return redact(json.dumps(payload, default=str, ensure_ascii=False))


def configure_logging(level: str, logs_dir: Path | None) -> None:
    """Configure root logging: JSON to stderr and, if given, a rotating file (10 MB x 5)."""
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    formatter = JsonFormatter()
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)
    if logs_dir is not None:
        logs_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            logs_dir / "omnisync.jsonl", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    for noisy in ("uvicorn.access",):
        logging.getLogger(noisy).setLevel(logging.WARNING)
