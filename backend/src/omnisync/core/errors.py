"""Domain errors raised by pure core logic.

``core`` must not depend on the API layer, so it does not raise
:class:`omnisync.errors.AppError` (whose catalogue holds HTTP codes only). Instead it raises
:class:`CoreError` with a stable pipeline code from 05-API-SPEC section 3; the services/jobs
layers store that code on the item or translate it.
"""

from __future__ import annotations

__all__ = ["ARCHIVE_CORRUPT", "ARCHIVE_LIMITS_EXCEEDED", "CoreError"]

# Pipeline codes (05-API-SPEC section 3, "codes stored on items").
ARCHIVE_CORRUPT = "archive.corrupt"
ARCHIVE_LIMITS_EXCEEDED = "archive.limits_exceeded"


class CoreError(Exception):
    """A domain failure with a stable ``code`` and a human-readable ``message``."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
