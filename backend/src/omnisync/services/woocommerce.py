"""WooCommerce REST client: rate gate, retry, circuit breaker, product lock, verify (FR-PIPE-04).

Ported from ``legacy:omnisync/wc.py``, ``legacy:omnisync/retry.py``,
``legacy:omnisync/circuit_breaker.py`` and the fetch half of ``legacy:omnisync/mappings.py:196-307``.
Pure decisions are reused from :mod:`omnisync.core`: entry selection
(:func:`~omnisync.core.shortcodes.select_download_entry`, D-02 rule), shortcodes, paging
(:func:`~omnisync.core.catalog.next_page`) and catalog building
(:func:`~omnisync.core.catalog.build_catalog`).

Concurrency model
    The job runner is a thread pool (03-ARCHITECTURE section 5.3, ADR-0004), so this client is
    **synchronous** (``httpx2.Client``; httpx2 is the maintained httpx continuation, ADR-0014).
    One client per store may be shared by all item threads. Process-wide coordination lives in
    :class:`WooCoordination`: one rate gate per store host, one circuit breaker per store and one
    lock per ``(store, product_id)``. All clients default to the module-level
    :data:`DEFAULT_COORDINATION`, so separate client instances still share these.

Resilience (FR-PIPE-10)
    Up to 3 retries with 2/4/8 s backoff on 408/429/500/502/503/504/520/521/522 and network
    errors; 60 s per-step timeouts. A call whose retries are exhausted counts one breaker failure;
    5 consecutive failures open that store's breaker for 60 s, during which calls fail immediately
    with ``circuit_open`` (no request sent). Other stores are unaffected.

Call deadline (03-ARCHITECTURE section 5.2)
    Every mutating call (PUT) additionally has a total deadline of 60 s: before every transport
    phase (pool, connect, TLS, send, receive) the remaining budget becomes that phase's timeout,
    a phase is never started after the deadline, and the response body is streamed and abandoned
    once the deadline passes. A phase already running at the deadline can overrun by at most its
    own timeout, so a call ends well inside the 146 s lease safety window. ``before_write`` (the
    lease write-deadline check of the jobs layer) runs immediately before every PUT attempt.

Security (06-SECURITY C-05, C-23)
    Store base URLs must be ``https://``; TLS verification is always on; redirects are not
    followed. Credentials are resolved on every request from plain values, callables or
    ``SecretStr``-like objects and never appear in reprs, messages or logs; authentication failure
    messages never echo the response body.
"""

from __future__ import annotations

import contextlib
import copy
import json
import logging
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final, Protocol
from urllib.parse import urlsplit

import httpx2

from omnisync.clock import Clock, SystemClock
from omnisync.core.catalog import (
    MAX_PAGES,
    PER_PAGE,
    CatalogBuild,
    PageAction,
    PageCursor,
    build_catalog,
    next_page,
)
from omnisync.core.shortcodes import (
    EntryMatch,
    build_shortcode,
    entry_object_key,
    select_download_entry,
)
from omnisync.services.errors import ServiceError

__all__ = [
    "BACKOFF_BASE_S",
    "BREAKER_FAILURE_THRESHOLD",
    "BREAKER_RECOVERY_S",
    "CALL_DEADLINE_S",
    "CIRCUIT_OPEN",
    "DEFAULT_COORDINATION",
    "DEFAULT_REQUEST_DELAY_S",
    "DEFAULT_TIMEOUT_S",
    "MAX_RESPONSE_BYTES",
    "MAX_RETRIES",
    "NEEDS_INPUT_AMBIGUOUS_ENTRY",
    "RETRYABLE_STATUS_CODES",
    "WC_AUTH_FAILED",
    "WC_ENTRY_NOT_FOUND",
    "WC_HTTP_ERROR",
    "WC_INVALID_RESPONSE",
    "WC_PAGE_LIMIT",
    "WC_WRITE_IGNORED",
    "CatalogProgress",
    "CircuitBreaker",
    "CircuitBreakerOpen",
    "RateGate",
    "SecretSource",
    "StoreConfig",
    "WooCommerceClient",
    "WooCoordination",
    "WriteOutcome",
    "WriteResult",
    "backoff_seconds",
    "fetch_catalog_pages",
    "read_live_version",
    "refresh_catalog",
]

_log = logging.getLogger(__name__)

# --------------------------------------------------------------------------- policy constants

DEFAULT_TIMEOUT_S: Final = 60.0
"""Per-step HTTP timeout (legacy:omnisync/wc.py:70-72)."""
CALL_DEADLINE_S: Final = 60.0
"""Total deadline of one mutating call (03-ARCHITECTURE section 5.2)."""
DEFAULT_REQUEST_DELAY_S: Final = 0.15
"""Minimum spacing between requests to one store host (legacy:omnisync/wc.py:44)."""
MAX_RETRIES: Final = 3
BACKOFF_BASE_S: Final = 2.0
RETRYABLE_STATUS_CODES: Final[frozenset[int]] = frozenset(
    {408, 429, 500, 502, 503, 504, 520, 521, 522}
)
"""408 timeout, 429 rate limit, 5xx, 520/521/522 Cloudflare origin down (legacy:omnisync/retry.py)."""
BREAKER_FAILURE_THRESHOLD: Final = 5
BREAKER_RECOVERY_S: Final = 60.0
MAX_RESPONSE_BYTES: Final = 64 * 1024 * 1024
"""Responses larger than this are refused (a catalog page of 100 products is far smaller)."""

_CATALOG_FIELDS: Final = "id,slug,name,downloadable_files,downloads,meta_data"
_ERROR_SNIPPET: Final = 200

# --------------------------------------------------------------------------- error codes

WC_AUTH_FAILED: Final = "wc.auth_failed"
"""401/403 from the store, or credentials missing. Never retried."""
WC_HTTP_ERROR: Final = "wc.http_error"
"""HTTP or network failure after retries, or a non-retryable HTTP status."""
WC_WRITE_IGNORED: Final = "wc.write_ignored"
"""PUT succeeded but its response lacks the new object key (FR-PIPE-04)."""
WC_ENTRY_NOT_FOUND: Final = "wc.entry_not_found"
"""The product has no download entry that matches the slug (FR-PIPE-05)."""
WC_INVALID_RESPONSE: Final = "wc.invalid_response"
"""Response body is not the expected JSON shape, or too large."""
WC_PAGE_LIMIT: Final = "wc.page_limit_exceeded"
"""Catalog pagination passed the 1,000-page safety cap without a usable header."""
CIRCUIT_OPEN: Final = "circuit_open"
"""The store's circuit breaker is open; no request was sent."""
NEEDS_INPUT_AMBIGUOUS_ENTRY: Final = "ambiguous_entry"
"""Needs-input kind for a multi-entry product without a slug match (FR-PIPE-05, D-02)."""


def backoff_seconds(attempt: int) -> float:
    """Delay before retry ``attempt`` (1-indexed): 2 s, 4 s, 8 s (legacy:omnisync/retry.py)."""
    return BACKOFF_BASE_S * 2.0 ** (attempt - 1)


# --------------------------------------------------------------------------- store config


class _HasSecretValue(Protocol):
    def get_secret_value(self) -> str: ...


SecretSource = str | _HasSecretValue | Callable[[], "str | _HasSecretValue"]
"""A credential: a plain value, a ``SecretStr``-like object, or a callable returning either.

Resolution of where secrets live belongs to ``services/secrets.py``; this module only reads the
value it is handed, on every request (so a rotated secret takes effect without a new client).
"""


def _resolve_secret(source: SecretSource) -> str:
    value: object = source() if callable(source) else source
    if not isinstance(value, str):
        getter = getattr(value, "get_secret_value", None)
        value = getter() if callable(getter) else ""
    return value if isinstance(value, str) else ""


@dataclass(frozen=True, slots=True)
class StoreConfig:
    """One configured store (ADR-0010). Secrets are excluded from ``repr``.

    ``base_url`` is the shop root (``https://www.example.com``); the REST base is derived as
    ``<base_url>/wp-json/wc/v3`` (04-DATA-MODEL section 2.2).
    """

    store_id: str
    base_url: str
    consumer_key: SecretSource = field(repr=False)
    consumer_secret: SecretSource = field(repr=False)
    label: str = ""
    request_delay: float = DEFAULT_REQUEST_DELAY_S
    timeout: float = DEFAULT_TIMEOUT_S

    def __post_init__(self) -> None:
        if not self.store_id:
            raise ValueError("store_id must not be empty")
        parts = urlsplit(self.base_url)
        if parts.scheme != "https" or not parts.hostname:
            raise ValueError("store base_url must be an https:// URL (06-SECURITY C-23)")
        if self.request_delay < 0 or self.timeout <= 0:
            raise ValueError("request_delay must be >= 0 and timeout > 0")

    @property
    def api_base(self) -> str:
        return self.base_url.rstrip("/") + "/wp-json/wc/v3"

    @property
    def host(self) -> str:
        """Rate-gate key: the host (and non-default port) of the store."""
        return urlsplit(self.base_url).netloc.lower()

    @property
    def display_name(self) -> str:
        return self.label or self.store_id

    def credentials(self) -> tuple[str, str]:
        """Resolve key and secret; missing values raise ``wc.auth_failed`` (not retryable)."""
        key = _resolve_secret(self.consumer_key)
        secret = _resolve_secret(self.consumer_secret)
        if not key or not secret:
            raise ServiceError(
                WC_AUTH_FAILED,
                f"WooCommerce credentials are not configured for {self.display_name}.",
                retryable=False,
            )
        return key, secret


# --------------------------------------------------------------------------- coordination


class CircuitBreakerOpen(Exception):
    """Raised by :meth:`CircuitBreaker.check` while the breaker is open."""

    def __init__(self, remaining: float) -> None:
        super().__init__(
            "Circuit breaker is OPEN due to consecutive failures. "
            f"Cool-down remaining: {remaining:.1f}s"
        )
        self.remaining = remaining


class CircuitBreaker:
    """Count consecutive failures; open for ``recovery_timeout`` after ``failure_threshold``.

    Thread-safe. ``monotonic`` is injectable for tests. legacy:omnisync/circuit_breaker.py.
    """

    def __init__(
        self,
        failure_threshold: int = BREAKER_FAILURE_THRESHOLD,
        recovery_timeout: float = BREAKER_RECOVERY_S,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._monotonic = monotonic
        self._consecutive_failures = 0
        self._open_until: float | None = None
        self._lock = threading.Lock()

    @property
    def consecutive_failures(self) -> int:
        with self._lock:
            return self._consecutive_failures

    def record_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0
            self._open_until = None

    def record_failure(self) -> None:
        with self._lock:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.failure_threshold:
                self._open_until = self._monotonic() + self.recovery_timeout

    def check(self) -> None:
        """Raise :class:`CircuitBreakerOpen` while open; close (half-reset) once it expired."""
        with self._lock:
            if self._open_until is None:
                return
            remaining = self._open_until - self._monotonic()
            if remaining > 0:
                raise CircuitBreakerOpen(remaining)
            self._open_until = None
            self._consecutive_failures = 0


class RateGate:
    """Minimum spacing between requests to one host. The caller sleeps outside the lock.

    legacy:omnisync/wc.py:21-36 (legacy slept inside ``wait``; here :meth:`reserve` returns the
    delay so the injectable sleep of the client is used).
    """

    def __init__(self, *, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def reserve(self, interval: float) -> float:
        """Claim the next slot; return how long to wait before sending (0 when free)."""
        now = self._monotonic()
        with self._lock:
            if now < self._next_allowed:
                wait = self._next_allowed - now
                self._next_allowed += interval
            else:
                wait = 0.0
                self._next_allowed = now + interval
        return wait


LockFactory = Callable[[], contextlib.AbstractContextManager[Any]]


class WooCoordination:
    """Process-wide shared state: rate gates per host, breakers per store, product locks.

    Use one instance per process (:data:`DEFAULT_COORDINATION`); tests pass a fresh one with an
    injected ``monotonic`` clock (and, for lock-ordering tests, an instrumented ``lock_factory``).
    """

    def __init__(
        self,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        failure_threshold: int = BREAKER_FAILURE_THRESHOLD,
        recovery_timeout: float = BREAKER_RECOVERY_S,
        lock_factory: LockFactory = threading.Lock,
    ) -> None:
        self.monotonic = monotonic
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._lock_factory = lock_factory
        self._guard = threading.Lock()
        self._gates: dict[str, RateGate] = {}
        self._breakers: dict[str, CircuitBreaker] = {}
        self._product_locks: dict[tuple[str, int], contextlib.AbstractContextManager[Any]] = {}

    def gate(self, host: str) -> RateGate:
        with self._guard:
            gate = self._gates.get(host)
            if gate is None:
                gate = self._gates[host] = RateGate(monotonic=self.monotonic)
            return gate

    def breaker(self, store_id: str) -> CircuitBreaker:
        with self._guard:
            breaker = self._breakers.get(store_id)
            if breaker is None:
                breaker = self._breakers[store_id] = CircuitBreaker(
                    self._failure_threshold, self._recovery_timeout, monotonic=self.monotonic
                )
            return breaker

    def product_lock(
        self, store_id: str, product_id: int
    ) -> contextlib.AbstractContextManager[Any]:
        """Lock serialising read-modify-write of one product (legacy:omnisync/wc.py:53-57)."""
        key = (store_id, product_id)
        with self._guard:
            lock = self._product_locks.get(key)
            if lock is None:
                lock = self._product_locks[key] = self._lock_factory()
            return lock


DEFAULT_COORDINATION = WooCoordination()

# --------------------------------------------------------------------------- results


class WriteOutcome(StrEnum):
    WRITTEN = "written"
    SKIPPED = "skipped"
    """The entry already carries the target shortcode and version (03-ARCHITECTURE 5.4)."""
    NEEDS_INPUT = "needs_input"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class WriteResult:
    """Outcome of :meth:`WooCommerceClient.update_product_download` for one store.

    ``reason`` is the needs-input kind (``ambiguous_entry``) for ``NEEDS_INPUT``;
    ``error_code``/``error_message``/``retryable`` are set for ``FAILED``. ``old_object_key`` is
    the key the selected entry pointed at before the write (for finalisation); ``live_version``
    is the product's ``latest_version_<slug>`` before the write.
    """

    outcome: WriteOutcome
    store_id: str
    product_id: int
    match: EntryMatch | None = None
    entry_id: str | None = None
    old_object_key: str | None = None
    live_version: str | None = None
    reason: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False

    @property
    def ok(self) -> bool:
        return self.outcome in (WriteOutcome.WRITTEN, WriteOutcome.SKIPPED)


@dataclass(frozen=True, slots=True)
class CatalogProgress:
    """Progress of a catalog fetch: ``page`` done, ``total_pages`` if known (FR-CAT-01)."""

    store_id: str
    page: int
    total_pages: int | None
    products: int
    warning: str | None = None


# --------------------------------------------------------------------------- helpers


def read_live_version(
    product: Mapping[str, object], slug: str, *, is_primary: bool = False
) -> str | None:
    """Return the product's live version for ``slug`` (downgrade check, FR-PIPE-06).

    ``latest_version_<slug>`` wins; a primary link falls back to ``latest_version`` (the key
    legacy compared for downgrades, legacy:omnisync/wc.py:253-259). Blank values count as absent.
    """
    meta = product.get("meta_data")
    if not isinstance(meta, list):
        return None
    keys = [f"latest_version_{slug}"] + (["latest_version"] if is_primary else [])
    for key in keys:
        for item in meta:
            if isinstance(item, Mapping) and item.get("key") == key:
                value = item.get("value")
                if value is not None and str(value).strip():
                    return str(value).strip()
    return None


def _download_entries(product: Mapping[str, object]) -> tuple[str, list[dict[str, Any]]]:
    """Return the write field and a deep copy of its dict entries (legacy:omnisync/wc.py:239-248)."""
    if product.get("downloadable_files"):
        write_key = "downloadable_files"
        raw = product["downloadable_files"]
    else:
        write_key = "downloads"
        raw = product.get("downloads") or []
    values = list(raw.values()) if isinstance(raw, Mapping) else raw
    if not isinstance(values, list):
        values = []
    return write_key, [copy.deepcopy(e) for e in values if isinstance(e, dict)]


def _response_has_key(body: object, object_key: str) -> bool:
    """Verify-after-PUT: the echoed downloads contain ``object_key`` (legacy:omnisync/wc.py:287-297)."""
    if not isinstance(body, Mapping):
        return False
    echoed = body.get("downloadable_files") or body.get("downloads") or []
    if isinstance(echoed, Mapping):
        echoed = list(echoed.values())
    if not isinstance(echoed, list):
        return False
    return any(isinstance(e, Mapping) and entry_object_key(e) == object_key for e in echoed)


def _is_date_modified_reject(body: object) -> bool:
    """True for a 400 that rejects only ``date_modified`` (legacy:omnisync/wc.py:100-111)."""
    if not isinstance(body, Mapping):
        return False
    data = body.get("data")
    params = data.get("params") if isinstance(data, Mapping) else None
    if isinstance(params, Mapping) and "date_modified" in params:
        return True
    return "date_modified" in str(body.get("message") or "")


class _DeadlineExceeded(Exception):
    """The total call deadline passed; handled like a network timeout."""


class _BeforeWriteAbort(Exception):
    """Carries an exception raised by the caller's ``before_write`` hook through the retry loop."""


class _CallDeadline:
    """Total deadline of one call, enforced through httpx2's ``trace`` extension."""

    def __init__(self, budget: float, monotonic: Callable[[], float]) -> None:
        self._monotonic = monotonic
        self._expires = monotonic() + budget
        self.timeouts: dict[str, float | None] = dict.fromkeys(
            ("connect", "read", "write", "pool"), budget
        )

    def remaining(self) -> float:
        return self._expires - self._monotonic()

    def check(self) -> None:
        if self.remaining() <= 0:
            raise _DeadlineExceeded

    def trace(self, event: str, info: Mapping[str, object]) -> None:
        """Before each transport phase: refuse to start after the deadline, else shrink timeouts.

        httpcore reads ``request.extensions["timeout"]`` at the start of every phase, and this
        dict is that object, so each phase gets at most the remaining budget.
        """
        if not event.endswith(".started"):
            return
        remaining = self.remaining()
        if remaining <= 0:
            raise _DeadlineExceeded
        for key in self.timeouts:
            self.timeouts[key] = remaining


@dataclass(frozen=True, slots=True)
class _Reply:
    status: int
    headers: httpx2.Headers
    body: bytes

    def json(self) -> object:
        return json.loads(self.body)

    def snippet(self) -> str:
        return self.body[:_ERROR_SNIPPET].decode("utf-8", errors="replace")


# --------------------------------------------------------------------------- client


class WooCommerceClient:
    """Synchronous, thread-safe WooCommerce REST client for one store.

    ``transport`` (an ``httpx2.MockTransport`` in tests), ``sleep`` (backoff and rate-gate waits)
    and ``coordination`` (gates, breakers, locks, monotonic clock) are injectable; ``clock``
    supplies the wall time sent as ``date_modified``.
    """

    def __init__(
        self,
        store: StoreConfig,
        *,
        coordination: WooCoordination | None = None,
        transport: httpx2.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Clock | None = None,
        call_deadline: float = CALL_DEADLINE_S,
    ) -> None:
        self.store = store
        self.coordination = coordination or DEFAULT_COORDINATION
        self._sleep = sleep
        self._clock = clock or SystemClock()
        self._call_deadline = call_deadline
        self._gate = self.coordination.gate(store.host)
        self.breaker = self.coordination.breaker(store.store_id)
        self._http = httpx2.Client(
            base_url=store.api_base,
            timeout=httpx2.Timeout(store.timeout),
            follow_redirects=False,
            verify=True,
            transport=transport,
            headers={"Accept": "application/json", "User-Agent": "OmniSync/2"},
        )

    # -- lifecycle

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> WooCommerceClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- transport

    def _send_once(
        self,
        method: str,
        path: str,
        params: Mapping[str, str | int] | None,
        payload: Mapping[str, object] | None,
        deadline: _CallDeadline | None,
    ) -> _Reply:
        extensions: dict[str, Any] | None = None
        if deadline is not None:
            extensions = {"timeout": deadline.timeouts, "trace": deadline.trace}
        request = self._http.build_request(
            method, path, params=params, json=payload, extensions=extensions
        )
        response = self._http.send(request, auth=self.store.credentials(), stream=True)
        try:
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_bytes():
                if deadline is not None:
                    deadline.check()
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise ServiceError(
                        WC_INVALID_RESPONSE,
                        f"{self.store.display_name}: response exceeds {MAX_RESPONSE_BYTES} bytes",
                        retryable=False,
                    )
                chunks.append(chunk)
        finally:
            response.close()
        return _Reply(response.status_code, response.headers, b"".join(chunks))

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str | int] | None = None,
        payload: Mapping[str, object] | None = None,
        before_send: Callable[[], None] | None = None,
    ) -> _Reply:
        """Send with breaker, rate gate and retries (legacy:omnisync/wc.py:59-98).

        Returns any response that is not retryable and not 5xx (the caller interprets 4xx).
        Raises ``circuit_open``, or ``wc.http_error`` after retries / on other 5xx.
        """
        name = self.store.display_name
        attempt = 0
        while True:
            try:
                self.breaker.check()
            except CircuitBreakerOpen as exc:
                raise ServiceError(CIRCUIT_OPEN, f"{name}: {exc}", retryable=True) from None
            attempt += 1
            if before_send is not None:
                try:
                    before_send()
                except Exception as exc:
                    raise _BeforeWriteAbort from exc
            wait = self._gate.reserve(self.store.request_delay)
            if wait > 0:
                self._sleep(wait)
            deadline = (
                _CallDeadline(self._call_deadline, self.coordination.monotonic)
                if method != "GET"
                else None
            )
            try:
                reply = self._send_once(method, path, params, payload, deadline)
            except (httpx2.TransportError, _DeadlineExceeded) as exc:
                what = (
                    "call deadline exceeded"
                    if isinstance(exc, _DeadlineExceeded)
                    else type(exc).__name__
                )
                if attempt > MAX_RETRIES:
                    self.breaker.record_failure()
                    raise ServiceError(
                        WC_HTTP_ERROR,
                        f"{name}: network error after {MAX_RETRIES} retries ({what})",
                        retryable=True,
                    ) from None
                delay = backoff_seconds(attempt)
                _log.warning(
                    "%s: transient network error (%s); retry %d/%d in %.1fs",
                    name,
                    what,
                    attempt,
                    MAX_RETRIES,
                    delay,
                )
                self._sleep(delay)
                continue
            if reply.status in RETRYABLE_STATUS_CODES:
                if attempt > MAX_RETRIES:
                    self.breaker.record_failure()
                    raise ServiceError(
                        WC_HTTP_ERROR,
                        f"{name}: HTTP {reply.status} after {MAX_RETRIES} retries",
                        retryable=True,
                    )
                delay = backoff_seconds(attempt)
                _log.warning(
                    "%s: transient error (HTTP %d); retry %d/%d in %.1fs",
                    name,
                    reply.status,
                    attempt,
                    MAX_RETRIES,
                    delay,
                )
                self._sleep(delay)
                continue
            if reply.status >= 500:
                self.breaker.record_failure()
                raise ServiceError(
                    WC_HTTP_ERROR, f"{name}: HTTP {reply.status}: server error", retryable=False
                )
            self.breaker.record_success()
            return reply

    def _raise_for_status(self, reply: _Reply, what: str) -> None:
        name = self.store.display_name
        if reply.status in (401, 403):
            raise ServiceError(
                WC_AUTH_FAILED,
                f"{name}: HTTP {reply.status}: WooCommerce authentication failed",
                retryable=False,
            )
        if 300 <= reply.status < 400:
            raise ServiceError(
                WC_HTTP_ERROR,
                f"{name}: HTTP {reply.status}: unexpected redirect on {what} (not followed)",
                retryable=False,
            )
        if reply.status >= 400:
            raise ServiceError(
                WC_HTTP_ERROR,
                f"{name}: HTTP {reply.status} on {what}: {reply.snippet()}",
                retryable=False,
            )

    def _json(self, reply: _Reply, what: str) -> object:
        try:
            return reply.json()
        except ValueError:
            raise ServiceError(
                WC_INVALID_RESPONSE,
                f"{self.store.display_name}: invalid JSON from WooCommerce on {what}",
                retryable=False,
            ) from None

    # -- products

    def get_product(self, product_id: int) -> dict[str, Any]:
        """GET ``products/{id}``. Raises :class:`ServiceError` on any failure."""
        what = f"product {product_id}"
        reply = self._request("GET", f"products/{product_id}")
        self._raise_for_status(reply, what)
        body = self._json(reply, what)
        if not isinstance(body, dict):
            raise ServiceError(
                WC_INVALID_RESPONSE,
                f"{self.store.display_name}: unexpected response shape for {what}",
                retryable=False,
            )
        return body

    read_live_version = staticmethod(read_live_version)

    def _put(
        self,
        path: str,
        payload: dict[str, Any],
        what: str,
        before_write: Callable[[], None] | None,
    ) -> _Reply:
        """PUT with the ``date_modified`` 400 fallback (legacy:omnisync/wc.py:113-134)."""
        reply = self._request("PUT", path, payload=payload, before_send=before_write)
        if reply.status == 400 and "date_modified" in payload:
            try:
                body = reply.json()
            except ValueError:
                body = None
            if _is_date_modified_reject(body):
                trimmed = {k: v for k, v in payload.items() if k != "date_modified"}
                reply = self._request("PUT", path, payload=trimmed, before_send=before_write)
        self._raise_for_status(reply, what)
        return reply

    def update_product_download(
        self,
        product_id: int,
        *,
        slug: str,
        object_key: str,
        download_name: str,
        is_primary: bool,
        version: str,
        entry_id_hint: str | None = None,
        before_write: Callable[[], None] | None = None,
    ) -> WriteResult:
        """GET -> select entry -> modify -> PUT -> verify, under the product lock (FR-PIPE-04).

        Entry selection is :func:`select_download_entry` (FR-PIPE-05, D-02): several entries
        without a slug match give ``NEEDS_INPUT`` with reason ``ambiguous_entry`` and no PUT.
        The selected entry gets shortcode ``[amazon_s3 object="<object_key>"]`` and name
        ``download_name``; meta ``latest_version_<slug>`` (plus ``latest_version`` when primary)
        is set to ``version``. A PUT response lacking ``object_key`` fails with
        ``wc.write_ignored``. Service failures are returned as ``FAILED``; an exception raised by
        ``before_write`` (lease write deadline) propagates unchanged and nothing is sent.
        """
        with self.coordination.product_lock(self.store.store_id, product_id):
            try:
                return self._update_locked(
                    product_id,
                    slug=slug,
                    object_key=object_key,
                    download_name=download_name,
                    is_primary=is_primary,
                    version=version,
                    entry_id_hint=entry_id_hint,
                    before_write=before_write,
                )
            except _BeforeWriteAbort as abort:
                raise (abort.__cause__ or abort) from None

    def _failed(self, product_id: int, exc: ServiceError, **kw: Any) -> WriteResult:
        return WriteResult(
            WriteOutcome.FAILED,
            self.store.store_id,
            product_id,
            error_code=exc.code,
            error_message=exc.message,
            retryable=exc.retryable,
            **kw,
        )

    def _update_locked(
        self,
        product_id: int,
        *,
        slug: str,
        object_key: str,
        download_name: str,
        is_primary: bool,
        version: str,
        entry_id_hint: str | None,
        before_write: Callable[[], None] | None,
    ) -> WriteResult:
        store_id = self.store.store_id
        name = self.store.display_name
        try:
            product = self.get_product(product_id)
        except ServiceError as exc:
            return self._failed(product_id, exc)
        write_key, entries = _download_entries(product)
        live = read_live_version(product, slug)
        selection = select_download_entry(entries, slug, download_name, entry_id_hint)
        if selection.index is None:
            if selection.reason is EntryMatch.AMBIGUOUS:
                return WriteResult(
                    WriteOutcome.NEEDS_INPUT,
                    store_id,
                    product_id,
                    match=selection.reason,
                    live_version=live,
                    reason=NEEDS_INPUT_AMBIGUOUS_ENTRY,
                )
            return self._failed(
                product_id,
                ServiceError(
                    WC_ENTRY_NOT_FOUND,
                    f"{name}: no uniquely matching download entry found for '{slug}' "
                    f"on product {product_id}",
                ),
                match=selection.reason,
                live_version=live,
            )
        entry = entries[selection.index]
        raw_id = entry.get("id") or entry.get("download_id")
        entry_id = str(raw_id) if raw_id else None
        old_key = entry_object_key(entry)
        common: dict[str, Any] = {
            "match": selection.reason,
            "entry_id": entry_id,
            "old_object_key": old_key,
            "live_version": live,
        }
        if old_key == object_key and live == version:
            return WriteResult(WriteOutcome.SKIPPED, store_id, product_id, **common)
        entry["file"] = build_shortcode(object_key)
        entry["name"] = download_name
        meta_raw = product.get("meta_data")
        meta = meta_raw if isinstance(meta_raw, list) else []
        drop = {f"latest_version_{slug}"} | ({"latest_version"} if is_primary else set())
        new_meta: list[object] = [
            m for m in meta if not (isinstance(m, Mapping) and m.get("key") in drop)
        ]
        new_meta.append({"key": f"latest_version_{slug}", "value": version})
        if is_primary:
            new_meta.append({"key": "latest_version", "value": version})
        payload: dict[str, Any] = {
            write_key: entries,
            "meta_data": new_meta,
            "date_modified": self._clock.now().strftime("%Y-%m-%dT%H:%M:%S"),
        }
        what = f"product {product_id}"
        try:
            reply = self._put(f"products/{product_id}", payload, what, before_write)
        except ServiceError as exc:
            return self._failed(product_id, exc, **common)
        try:
            body = reply.json()
        except ValueError:
            body = None
        if not _response_has_key(body, object_key):
            return self._failed(
                product_id,
                ServiceError(
                    WC_WRITE_IGNORED,
                    f"{name}: API returned success but product {product_id} still lacks a "
                    f"download entry for '{object_key}' (write field ignored?)",
                ),
                **common,
            )
        return WriteResult(WriteOutcome.WRITTEN, store_id, product_id, **common)

    # -- catalog

    def iter_catalog_pages(
        self, progress_cb: Callable[[CatalogProgress], None] | None = None
    ) -> Iterator[list[dict[str, Any]]]:
        """Yield the downloadable products page by page, driven by core :func:`next_page`.

        100 per page; ``X-WP-TotalPages`` (read on the first non-empty page) with short-page
        fallback; passing the 1,000-page cap without a usable header raises
        ``wc.page_limit_exceeded``. legacy:omnisync/mappings.py:196-307.
        """
        cursor = PageCursor()
        fetched = 0
        while True:
            params: dict[str, str | int] = {
                "downloadable": "true",
                "per_page": PER_PAGE,
                "page": cursor.page,
                "_fields": _CATALOG_FIELDS,
            }
            what = f"catalog page {cursor.page}"
            reply = self._request("GET", "products", params=params)
            self._raise_for_status(reply, what)
            batch = self._json(reply, what)
            if not isinstance(batch, list):
                raise ServiceError(
                    WC_INVALID_RESPONSE,
                    f"{self.store.display_name}: unexpected response shape on {what}",
                    retryable=False,
                )
            step = next_page(cursor, len(batch), reply.headers.get("X-WP-TotalPages"))
            if step.warning:
                _log.warning("%s: %s", self.store.display_name, step.warning)
            products = [p for p in batch if isinstance(p, dict)]
            fetched += len(products)
            if batch:
                if progress_cb is not None:
                    progress_cb(
                        CatalogProgress(
                            self.store.store_id,
                            cursor.page,
                            step.cursor.total_pages,
                            fetched,
                            step.warning,
                        )
                    )
                yield products
            if step.action is PageAction.ABORT:
                raise ServiceError(
                    WC_PAGE_LIMIT,
                    f"{self.store.display_name}: pagination exceeded safety limit "
                    f"({MAX_PAGES} pages); aborting to avoid an infinite loop.",
                    retryable=False,
                )
            if step.action is PageAction.STOP:
                return
            cursor = step.cursor

    def fetch_catalog_pages(
        self, progress_cb: Callable[[CatalogProgress], None] | None = None
    ) -> list[dict[str, Any]]:
        """All downloadable products of the store (see :meth:`iter_catalog_pages`)."""
        products: list[dict[str, Any]] = []
        for page in self.iter_catalog_pages(progress_cb):
            products.extend(page)
        return products

    def refresh_catalog(
        self,
        bucket: str | None,
        progress_cb: Callable[[CatalogProgress], None] | None = None,
    ) -> CatalogBuild:
        """Fetch every page and build the store's catalog with core :func:`build_catalog`."""
        self.store.credentials()  # fail fast before any request (legacy:omnisync/mappings.py:403-407)
        return build_catalog(self.fetch_catalog_pages(progress_cb), bucket)


# --------------------------------------------------------------------------- module-level API


def fetch_catalog_pages(
    store: StoreConfig,
    progress_cb: Callable[[CatalogProgress], None] | None = None,
    *,
    coordination: WooCoordination | None = None,
    transport: httpx2.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> list[dict[str, Any]]:
    """Open a client for ``store``, fetch all downloadable products and close it."""
    with WooCommerceClient(
        store, coordination=coordination, transport=transport, sleep=sleep
    ) as client:
        return client.fetch_catalog_pages(progress_cb)


def refresh_catalog(
    store: StoreConfig,
    bucket: str | None,
    progress_cb: Callable[[CatalogProgress], None] | None = None,
    *,
    coordination: WooCoordination | None = None,
    transport: httpx2.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> CatalogBuild:
    """Fetch and build one store's catalog (FR-CAT-01); see :meth:`WooCommerceClient.refresh_catalog`."""
    with WooCommerceClient(
        store, coordination=coordination, transport=transport, sleep=sleep
    ) as client:
        return client.refresh_catalog(bucket, progress_cb)
