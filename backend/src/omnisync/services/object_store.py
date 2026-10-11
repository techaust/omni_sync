"""Abstract JSON object store used by the coordination services (ADR-0007).

``services/lease.py`` and ``services/shared_config.py`` depend only on the protocols in this
module, never on boto3, so that they can be tested against an in-memory fake with exact S3
conditional-write semantics (09-TESTING-STRATEGY section 3, ``FakeConditionalS3``).

The S3 adapter (``services/s3.py``) is expected to satisfy :class:`JsonObjectStore` and raise
:class:`PreconditionFailed` (this class, the canonical one) whenever a conditional request is
rejected. That includes:

* HTTP 412 ``PreconditionFailed`` (``If-Match`` mismatch, or ``If-None-Match: *`` on an
  existing object),
* HTTP 409 ``ConditionalRequestConflict`` (a concurrent conditional write won the race),
* HTTP 404 ``NoSuchKey`` returned for a request carrying ``If-Match`` against a missing key.

All three mean the same thing to the caller: "the object is not in the state you assumed".

Every other failure (timeouts, 5xx, access denied) is raised as some other exception. The
coordination services treat such failures as **ambiguous**: the write may or may not have been
applied. They are written to stay safe in both cases.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

__all__ = ["JsonObjectStore", "PreconditionFailed", "ServerDateSource"]


class PreconditionFailed(Exception):
    """A conditional request was rejected (412, 409 conflict, or 404 under ``If-Match``)."""


class JsonObjectStore(Protocol):
    """Small JSON documents with ETag-based optimistic concurrency (S3 semantics)."""

    def get_json(self, key: str) -> tuple[dict[str, Any], str] | None:
        """Return ``(data, etag)`` or ``None`` when the object does not exist."""
        ...

    def put_json(
        self,
        key: str,
        data: dict[str, Any],
        *,
        if_match: str | None = None,
        if_none_match: bool = False,
    ) -> str:
        """Write ``data`` and return the new ETag.

        ``if_match`` sends ``If-Match: <etag>``; ``if_none_match`` sends ``If-None-Match: *``.
        Raises :class:`PreconditionFailed` when the precondition does not hold.
        """
        ...

    def delete(self, key: str, *, if_match: str | None = None) -> None:
        """Delete the object (``If-Match`` optional). Deleting a missing object is a no-op."""
        ...


@runtime_checkable
class ServerDateSource(Protocol):
    """Source of S3 server time samples for the clock-offset estimate (03-ARCHITECTURE 6.4).

    :meth:`server_date` performs one fresh, cheap request against the store (for example a
    ``HeadObject`` on the lease key, where a 404 also carries a ``Date`` header) and returns the
    response's ``Date`` header as POSIX seconds (1-second resolution, truncated, as sent by S3).

    The lease service times the call on its local monotonic clock, which pairs each ``Date``
    value with the exact request that produced it. (Reading "the last response's date" from a
    shared client would be unsafe: after a network error it would pair an old ``Date`` with a new
    send time and make S3 time appear earlier than it is.)
    """

    def server_date(self) -> float: ...
