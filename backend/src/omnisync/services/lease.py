"""Shared S3 lease between instances (FR-COORD-01/02, NFR-REL-03, ADR-0007).

Protocol: 03-ARCHITECTURE section 6.2 (lease) and 6.4 (clock skew).

Document
--------
``<control prefix>control/lease.json``::

    {"schema": 1, "holder_instance_id": "...", "holder_instance_name": "owner-pc",
     "holder_user": "owner", "operation": "pipeline_live", "run_id": 418,
     "lease_id": "<uuid4 hex, one per acquisition>",
     "acquired_at": "2026-10-11T10:00:00Z", "renewed_at": "2026-10-11T10:00:30Z",
     "expires_at": "2026-10-11T10:05:30Z", "released": false}

``lease_id`` is an addition to the documented fields. It identifies one acquisition exactly, so
that an instance can recognise its *own* write after an ambiguous failure (a PUT that timed out
but was applied by S3) without ever mistaking someone else's lease for its own. Readers ignore
it. Timestamps are written truncated to whole seconds; every party derives its decisions from
the *written* values, so truncation shortens the lease by < 1 s for holder and taker alike.

Time
----
All lease decisions use **S3 time**: ``s3_now() = monotonic() + offset``, where the offset comes
from :class:`S3TimeEstimator` (``offset = s3_date + 0.5 - (send + receive) / 2``, best RTT among
the last 10 samples, refuse when that RTT exceeds 3 s). Wall clocks are never used.

Operations (all writes are conditional)
---------------------------------------
* **Acquire** - ``PUT If-None-Match: *``. 200 -> held. 412/409 or an ambiguous failure ->
  *takeover check*.
* **Takeover check** - ``GET`` -> doc, ETag ``E``. If ``released``, or ``s3_now() > expires_at +
  takeover_grace``, or the doc is our own ambiguous write: ``PUT If-Match: E`` (200 -> held;
  412/409 -> busy). Otherwise busy (holder reported, retry after the 15 s polling interval).
* **Renew** (every 30 s) - ``PUT If-Match: <my ETag>`` with new ``renewed_at`` and ``expires_at
  = s3_now() + ttl``. 412 -> ``GET``: if the doc still carries my ``lease_id`` (an earlier
  ambiguous renew landed) adopt its ETag and retry once; otherwise the lease is **lost**
  (``lease_taken``). Network errors and clock uncertainty are retried every 5 s until the
  write deadline passes, then the lease is lost (``renewal_failed`` / ``clock_uncertain``).
* **Release** - ``PUT If-Match: <my ETag>`` with ``released: true``. 412 -> nothing to do (unless
  the doc is still provably ours, then retry once with its ETag). The object is never deleted,
  so a release can never remove another holder's lease.

The 60 s lease-safe call rule
-----------------------------
A holder may **send** a mutating call only while ``s3_now() < expires_at - 120 s``. The runner
calls :meth:`LeaseHandle.check` (or passes it as the ``before_write`` gate of
``services/s3.py``) immediately before every S3 PUT, part upload, complete-multipart, delete and
WooCommerce PUT. The write deadline is held as an instant on the *local monotonic clock*
(computed with the offset in force when ``expires_at`` was written), so the check needs no
network and keeps working when S3 time later becomes uncertain.

The total per-call deadline (``LeaseTimings.call_deadline`` = 60 s) is enforced by the transport
wrapper that sends the call; this module only guarantees, via :meth:`LeaseTimings.__post_init__`,
that the timing parameters leave room for it::

    write_margin + takeover_grace - 2 * max_clock_error >= call_deadline
            120  +             30 -  2 * 2              =  146 >= 60

i.e. in true S3 time the last call is sent before ``expires_at - 118 s`` and the earliest
takeover happens after ``expires_at + 28 s``: 146 s for a 60 s call. A healthy holder renews
every 30 s, so its deadline stays 150-180 s ahead and is never reached. The only residual
risk is a process freeze between :meth:`LeaseHandle.check` and the send (T-11, accepted).
"""

from __future__ import annotations

import logging
import math
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, fields, replace
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, Final, Literal, NamedTuple, Self, TypeVar

from omnisync.services.errors import ServiceError
from omnisync.services.object_store import JsonObjectStore, PreconditionFailed, ServerDateSource

__all__ = [
    "DEFAULT_CONTROL_PREFIX",
    "LEASE_LOST",
    "LEASE_OBJECT",
    "LEASE_SCHEMA",
    "ClockSample",
    "ClockUncertainError",
    "LeaseDocumentError",
    "LeaseHandle",
    "LeaseInfo",
    "LeaseLostError",
    "LeaseManager",
    "LeaseTimings",
    "LeaseUnavailable",
    "LeaseWaitCancelled",
    "LostDetail",
    "S3TimeEstimator",
    "UnavailableReason",
    "format_s3_time",
    "parse_s3_time",
]

log = logging.getLogger(__name__)

LEASE_SCHEMA: Final = 1
LEASE_OBJECT: Final = "control/lease.json"
DEFAULT_CONTROL_PREFIX: Final = "_omnisync/"

# Error codes (05-API-SPEC section 3).
LEASE_LOST: Final = "lease.lost"
LEASE_CLOCK_UNCERTAIN: Final = "lease.clock_uncertain"
LEASE_WAIT_CANCELLED: Final = "lease.wait_cancelled"
LEASE_DOCUMENT_INVALID: Final = "lease.document_invalid"

LostDetail = Literal["renewal_failed", "lease_taken", "clock_uncertain", "released"]
UnavailableReason = Literal[
    "held", "contended", "clock_uncertain", "store_error", "invalid_document"
]

T = TypeVar("T")
Waiter = Callable[[threading.Event, float], bool]
"""``wait(event, seconds) -> event_was_set``; injectable so tests never sleep."""


def _default_wait(event: threading.Event, seconds: float) -> bool:
    return event.wait(seconds)


# --------------------------------------------------------------------------------------------
# Timings
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LeaseTimings:
    """Lease parameters, defaults exactly as in 03-ARCHITECTURE 6.2 / 6.4 (seconds)."""

    ttl: float = 300.0
    renew_interval: float = 30.0
    write_margin: float = 120.0  # write_deadline = expires_at - write_margin
    takeover_grace: float = 30.0
    call_deadline: float = 60.0  # total deadline of one mutating call (enforced by transport)
    poll_interval: float = 15.0  # takeover polling while waiting_for_lease
    renew_retry_interval: float = 5.0  # retry cadence after a failed renewal (not in the docs)
    max_rtt: float = 3.0  # best RTT above this -> clock uncertain
    max_clock_error: float = 2.0  # = 0.5 + max_rtt / 2

    def __post_init__(self) -> None:
        for item in fields(self):
            name = item.name
            value = getattr(self, name)
            if not (isinstance(value, int | float) and math.isfinite(value) and value > 0):
                raise ValueError(f"LeaseTimings.{name} must be a positive number")
        if self.max_clock_error < 0.5 + self.max_rtt / 2:
            raise ValueError("max_clock_error must be at least 0.5 + max_rtt / 2")
        if self.write_margin >= self.ttl:
            raise ValueError("write_margin must be shorter than ttl")
        if self.renew_interval >= self.ttl - self.write_margin:
            raise ValueError("renew_interval must be shorter than ttl - write_margin")
        if self.write_margin + self.takeover_grace - 2 * self.max_clock_error < self.call_deadline:
            raise ValueError(
                "unsafe lease timings: write_margin + takeover_grace - 2 * max_clock_error "
                "must be at least call_deadline"
            )

    @property
    def safety_gap(self) -> float:
        """Worst-case true-time gap between the last send and the earliest takeover (146 s)."""
        return self.write_margin + self.takeover_grace - 2 * self.max_clock_error


# --------------------------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------------------------


class ClockUncertainError(ServiceError):
    """S3 time cannot be estimated within 2 s (no sample, or best RTT > 3 s)."""

    def __init__(self, message: str) -> None:
        super().__init__(LEASE_CLOCK_UNCERTAIN, message, retryable=True)


class LeaseLostError(ServiceError):
    """No further mutating call may be sent under this lease (code ``lease.lost``)."""

    def __init__(self, detail: LostDetail, message: str) -> None:
        super().__init__(LEASE_LOST, message, retryable=False)
        self.detail: LostDetail = detail


class LeaseWaitCancelled(ServiceError):
    """The user cancelled a run while it was ``waiting_for_lease``."""

    def __init__(self) -> None:
        super().__init__(LEASE_WAIT_CANCELLED, "cancelled while waiting for the lease")


class LeaseDocumentError(ValueError):
    """The lease document is malformed or has an unsupported schema."""


# --------------------------------------------------------------------------------------------
# S3 time
# --------------------------------------------------------------------------------------------


class ClockSample(NamedTuple):
    rtt: float
    offset: float  # s3_time - local_monotonic


class S3TimeEstimator:
    """Estimate S3 server time from ``Date`` headers (03-ARCHITECTURE 6.4).

    ``observe(send, receive, date)`` records one request timed on the local monotonic clock.
    The estimate uses the sample with the smallest RTT among the last ``window`` samples; its
    error is bounded by ``0.5 s + RTT / 2``. :meth:`now` raises :class:`ClockUncertainError`
    when there is no sample or that RTT exceeds ``max_rtt``.
    """

    def __init__(
        self,
        monotonic: Callable[[], float] = time.monotonic,
        *,
        window: int = 10,
        max_rtt: float = 3.0,
    ) -> None:
        if window < 1:
            raise ValueError("window must be at least 1")
        self._monotonic = monotonic
        self._max_rtt = max_rtt
        self._samples: deque[ClockSample] = deque(maxlen=window)
        self._lock = threading.Lock()

    def monotonic(self) -> float:
        return self._monotonic()

    def observe(self, send: float, receive: float, server_date: float) -> ClockSample:
        if receive < send:
            raise ValueError("receive time precedes send time")
        sample = ClockSample(receive - send, server_date + 0.5 - (send + receive) / 2)
        with self._lock:
            self._samples.append(sample)
        return sample

    def best_sample(self) -> ClockSample | None:
        with self._lock:
            return min(self._samples, key=lambda s: s.rtt, default=None)

    def error_bound(self) -> float | None:
        best = self.best_sample()
        return None if best is None else 0.5 + best.rtt / 2

    def offset(self) -> float:
        best = self.best_sample()
        if best is None:
            raise ClockUncertainError("no S3 time sample yet")
        if best.rtt > self._max_rtt:
            raise ClockUncertainError(
                f"S3 round trip {best.rtt:.1f} s exceeds {self._max_rtt:.1f} s; "
                "S3 time cannot be estimated within 2 s"
            )
        return best.offset

    def now(self) -> float:
        """Current S3 time (POSIX seconds)."""
        return self._monotonic() + self.offset()


def format_s3_time(seconds: float) -> str:
    """POSIX seconds -> ``YYYY-MM-DDTHH:MM:SSZ`` (truncated to whole seconds)."""
    return datetime.fromtimestamp(math.floor(seconds), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_s3_time(text: str) -> float:
    """ISO 8601 with an explicit offset (``Z`` or ``+00:00``) -> POSIX seconds."""
    value = datetime.fromisoformat(text)
    if value.tzinfo is None:
        raise ValueError(f"timestamp without time zone: {text!r}")
    return value.timestamp()


# --------------------------------------------------------------------------------------------
# Lease document
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LeaseInfo:
    """Parsed lease document. Times are S3 time in POSIX seconds."""

    holder_instance_id: str
    holder_instance_name: str
    holder_user: str
    operation: str
    run_id: int
    acquired_at: float
    renewed_at: float
    expires_at: float
    released: bool
    lease_id: str | None = None
    schema: int = LEASE_SCHEMA

    @classmethod
    def from_document(cls, data: Any) -> LeaseInfo:
        if not isinstance(data, dict):
            raise LeaseDocumentError("lease document is not a JSON object")
        schema = data.get("schema")
        if type(schema) is not int or schema != LEASE_SCHEMA:
            raise LeaseDocumentError(f"unsupported lease schema {schema!r}")
        strings: dict[str, str] = {}
        for name in ("holder_instance_id", "holder_instance_name", "holder_user", "operation"):
            value = data.get(name)
            if not isinstance(value, str):
                raise LeaseDocumentError(f"lease field {name!r} must be a string")
            strings[name] = value
        run_id = data.get("run_id")
        if type(run_id) is not int:
            raise LeaseDocumentError("lease field 'run_id' must be an integer")
        released = data.get("released")
        if type(released) is not bool:
            raise LeaseDocumentError("lease field 'released' must be a boolean")
        lease_id = data.get("lease_id")
        if lease_id is not None and not isinstance(lease_id, str):
            raise LeaseDocumentError("lease field 'lease_id' must be a string")
        times: dict[str, float] = {}
        for name in ("acquired_at", "renewed_at", "expires_at"):
            value = data.get(name)
            bad = LeaseDocumentError(f"lease field {name!r} must be a timestamp")
            if not isinstance(value, str):
                raise bad
            try:
                times[name] = parse_s3_time(value)
            except ValueError:
                raise bad from None
        return cls(
            holder_instance_id=strings["holder_instance_id"],
            holder_instance_name=strings["holder_instance_name"],
            holder_user=strings["holder_user"],
            operation=strings["operation"],
            run_id=run_id,
            acquired_at=times["acquired_at"],
            renewed_at=times["renewed_at"],
            expires_at=times["expires_at"],
            released=released,
            lease_id=lease_id,
            schema=schema,
        )

    def to_document(self) -> dict[str, Any]:
        doc: dict[str, Any] = {
            "schema": self.schema,
            "holder_instance_id": self.holder_instance_id,
            "holder_instance_name": self.holder_instance_name,
            "holder_user": self.holder_user,
            "operation": self.operation,
            "run_id": self.run_id,
            "acquired_at": format_s3_time(self.acquired_at),
            "renewed_at": format_s3_time(self.renewed_at),
            "expires_at": format_s3_time(self.expires_at),
            "released": self.released,
        }
        if self.lease_id is not None:
            doc["lease_id"] = self.lease_id
        return doc

    def is_lease(self, instance_id: str, run_id: int, lease_id: str) -> bool:
        """True when this document is exactly the given acquisition."""
        return (
            self.lease_id == lease_id
            and self.holder_instance_id == instance_id
            and self.run_id == run_id
        )


@dataclass(frozen=True, slots=True)
class LeaseUnavailable:
    """The lease could not be acquired now; the run stays ``waiting_for_lease``."""

    reason: UnavailableReason
    message: str
    holder: LeaseInfo | None = None
    retry_after: float = 15.0


# --------------------------------------------------------------------------------------------
# Manager
# --------------------------------------------------------------------------------------------


class LeaseManager:
    """Acquires the shared lease for one instance.

    ``store`` must satisfy :class:`JsonObjectStore`. ``time_source`` provides S3 ``Date``
    samples; it defaults to ``store`` when the store also satisfies :class:`ServerDateSource`.
    ``estimator`` carries the injectable monotonic clock. ``wait`` replaces
    ``threading.Event.wait`` for polling and renewal cadence (tests).
    """

    def __init__(
        self,
        store: JsonObjectStore,
        *,
        instance_id: str,
        instance_name: str,
        control_prefix: str = DEFAULT_CONTROL_PREFIX,
        timings: LeaseTimings | None = None,
        estimator: S3TimeEstimator | None = None,
        time_source: ServerDateSource | None = None,
        wait: Waiter | None = None,
    ) -> None:
        if not instance_id:
            raise ValueError("instance_id is required")
        if time_source is None:
            if not isinstance(store, ServerDateSource):
                raise TypeError("a ServerDateSource is required for S3 time estimation")
            time_source = store
        self.store = store
        self.instance_id = instance_id
        self.instance_name = instance_name
        self.key = control_prefix + LEASE_OBJECT
        self.timings = timings or LeaseTimings()
        self.estimator = estimator or S3TimeEstimator(max_rtt=self.timings.max_rtt)
        self._time_source = time_source
        self._wait: Waiter = wait or _default_wait
        self._ambiguous: dict[int, deque[str]] = {}
        self._lock = threading.Lock()

    # -- time ---------------------------------------------------------------------------------

    def sample_clock(self) -> ClockSample | None:
        """Take one S3 ``Date`` sample. Failures are logged; older samples stay usable."""
        est = self.estimator
        send = est.monotonic()
        try:
            date = self._time_source.server_date()
        except Exception:  # any transport failure only means "no sample"
            log.warning("lease: S3 time sample failed", exc_info=True)
            return None
        return est.observe(send, est.monotonic(), date)

    def s3_now(self) -> float:
        """Current S3 time; raises :class:`ClockUncertainError`."""
        return self.estimator.now()

    def _now_and_offset(self) -> tuple[float, float]:
        offset = self.estimator.offset()
        return self.estimator.monotonic() + offset, offset

    # -- reading ------------------------------------------------------------------------------

    def read(self) -> LeaseInfo | None:
        """Current lease document for display (FR-COORD-02). ``None`` when absent."""
        got = self.store.get_json(self.key)
        if got is None:
            return None
        try:
            return LeaseInfo.from_document(got[0])
        except LeaseDocumentError as exc:
            raise ServiceError(LEASE_DOCUMENT_INVALID, str(exc)) from None

    # -- acquisition --------------------------------------------------------------------------

    def try_acquire(
        self, *, run_id: int, operation: str, user: str
    ) -> LeaseHandle | LeaseUnavailable:
        """One acquisition attempt (no waiting). Never raises for store or clock failures."""
        self.sample_clock()
        try:
            now, offset = self._now_and_offset()
        except ClockUncertainError as exc:
            return self._unavailable("clock_uncertain", exc.message)
        info = self._new_info(now, run_id=run_id, operation=operation, user=user)
        try:
            etag = self.store.put_json(self.key, info.to_document(), if_none_match=True)
        except PreconditionFailed:
            pass
        except Exception:  # ambiguous: the create may have been applied
            log.warning("lease: create failed (ambiguous)", exc_info=True)
            self._remember_ambiguous(run_id, info)
        else:
            return self._held(info, etag, offset)
        return self._takeover_check(now, offset, run_id=run_id, operation=operation, user=user)

    def _takeover_check(
        self, now: float, offset: float, *, run_id: int, operation: str, user: str
    ) -> LeaseHandle | LeaseUnavailable:
        try:
            got = self.store.get_json(self.key)
        except Exception as exc:  # reported as a waiting reason
            log.warning("lease: reading the lease failed", exc_info=True)
            return self._unavailable("store_error", f"reading the lease failed: {exc}")
        info = self._new_info(now, run_id=run_id, operation=operation, user=user)
        if got is None:  # deleted between our create attempt and the read: create again
            return self._write_new(info, offset, if_match=None)
        data, etag = got
        try:
            current = LeaseInfo.from_document(data)
        except LeaseDocumentError as exc:
            # Never overwrite a document we cannot interpret: it may be a newer instance's
            # live lease. An admin must repair or upgrade (10-OPERATIONS section 8).
            return self._unavailable("invalid_document", f"lease document rejected: {exc}")
        ours = self._is_ambiguous_ours(run_id, current)
        expired = now > current.expires_at + self.timings.takeover_grace
        if not (current.released or expired or ours):
            return self._unavailable(
                "held",
                f"lease held by {current.holder_instance_name} ({current.operation}, "
                f"run {current.run_id}) until {format_s3_time(current.expires_at)}",
                holder=current,
            )
        return self._write_new(info, offset, if_match=etag)

    def _write_new(
        self, info: LeaseInfo, offset: float, *, if_match: str | None
    ) -> LeaseHandle | LeaseUnavailable:
        doc = info.to_document()
        try:
            if if_match is None:
                etag = self.store.put_json(self.key, doc, if_none_match=True)
            else:
                etag = self.store.put_json(self.key, doc, if_match=if_match)
        except PreconditionFailed:
            return self._unavailable("contended", "another instance acquired the lease first")
        except Exception as exc:  # ambiguous; resolved on the next attempt
            log.warning("lease: writing the lease failed (ambiguous)", exc_info=True)
            self._remember_ambiguous(info.run_id, info)
            return self._unavailable("store_error", f"writing the lease failed: {exc}")
        return self._held(info, etag, offset)

    def acquire(
        self,
        *,
        run_id: int,
        operation: str,
        user: str,
        cancel: threading.Event | None = None,
        on_wait: Callable[[LeaseUnavailable], None] | None = None,
    ) -> LeaseHandle:
        """Block until the lease is held, polling every ``poll_interval`` (FR-COORD-01).

        ``on_wait`` is called after every unsuccessful attempt (the runner sets the run to
        ``waiting_for_lease`` and emits ``lease.waiting``). Setting ``cancel`` raises
        :class:`LeaseWaitCancelled`.
        """
        cancel = cancel or threading.Event()
        while True:
            if cancel.is_set():
                raise LeaseWaitCancelled
            result = self.try_acquire(run_id=run_id, operation=operation, user=user)
            if isinstance(result, LeaseHandle):
                return result
            if on_wait is not None:
                on_wait(result)
            if self._wait(cancel, result.retry_after):
                raise LeaseWaitCancelled

    @contextmanager
    def hold(
        self,
        *,
        run_id: int,
        operation: str,
        user: str,
        cancel: threading.Event | None = None,
        on_wait: Callable[[LeaseUnavailable], None] | None = None,
        on_lost: Callable[[LeaseLostError], None] | None = None,
    ) -> Iterator[LeaseHandle]:
        """Acquire (blocking), renew in the background, release on exit."""
        handle = self.acquire(
            run_id=run_id, operation=operation, user=user, cancel=cancel, on_wait=on_wait
        )
        if on_lost is not None:
            handle.add_lost_listener(on_lost)
        handle.start_auto_renew()
        try:
            yield handle
        finally:
            handle.release()

    # -- helpers ------------------------------------------------------------------------------

    def _new_info(self, now: float, *, run_id: int, operation: str, user: str) -> LeaseInfo:
        start = math.floor(now)
        return LeaseInfo(
            holder_instance_id=self.instance_id,
            holder_instance_name=self.instance_name,
            holder_user=user,
            operation=operation,
            run_id=run_id,
            acquired_at=start,
            renewed_at=start,
            expires_at=math.floor(now + self.timings.ttl),
            released=False,
            lease_id=uuid.uuid4().hex,
        )

    def _held(self, info: LeaseInfo, etag: str, offset: float) -> LeaseHandle:
        with self._lock:
            self._ambiguous.pop(info.run_id, None)
        log.info(
            "lease acquired",
            extra={"data": {"run_id": info.run_id, "expires_at": format_s3_time(info.expires_at)}},
        )
        return LeaseHandle(self, info, etag, offset)

    def _unavailable(
        self, reason: UnavailableReason, message: str, holder: LeaseInfo | None = None
    ) -> LeaseUnavailable:
        return LeaseUnavailable(reason, message, holder, self.timings.poll_interval)

    def _remember_ambiguous(self, run_id: int, info: LeaseInfo) -> None:
        if info.lease_id is None:  # pragma: no cover - _new_info always sets it
            return
        with self._lock:
            self._ambiguous.setdefault(run_id, deque(maxlen=16)).append(info.lease_id)

    def _is_ambiguous_ours(self, run_id: int, current: LeaseInfo) -> bool:
        with self._lock:
            candidates = tuple(self._ambiguous.get(run_id, ()))
        return any(current.is_lease(self.instance_id, run_id, lid) for lid in candidates)


# --------------------------------------------------------------------------------------------
# Handle
# --------------------------------------------------------------------------------------------


class LeaseHandle:
    """A held lease. Check :meth:`check` before every mutating external call."""

    def __init__(self, manager: LeaseManager, info: LeaseInfo, etag: str, offset: float) -> None:
        if info.lease_id is None:
            raise ValueError("a held lease needs a lease_id")
        self._lease_id: str = info.lease_id
        self._mgr = manager
        self._info = info
        self._etag = etag
        self._deadline_mono = self._deadline_for(info, offset)
        self._lost: LeaseLostError | None = None
        self._released = False
        self._last_failure: LostDetail = "renewal_failed"
        self._listeners: list[Callable[[LeaseLostError], None]] = []
        self._state = threading.Lock()  # short critical sections only
        self._io = threading.RLock()  # serialises renew/release network calls
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- state --------------------------------------------------------------------------------

    @property
    def info(self) -> LeaseInfo:
        return self._info

    @property
    def lease_id(self) -> str:
        return self._lease_id

    @property
    def run_id(self) -> int:
        return self._info.run_id

    @property
    def expires_at(self) -> float:
        return self._info.expires_at

    @property
    def write_deadline(self) -> float:
        """S3 time after which no mutating call is sent (``expires_at - 120 s``)."""
        return self._info.expires_at - self._mgr.timings.write_margin

    @property
    def lost(self) -> bool:
        return self._lost is not None

    @property
    def lost_error(self) -> LeaseLostError | None:
        return self._lost

    @property
    def released(self) -> bool:
        return self._released

    def remaining(self) -> float:
        """Seconds until the write deadline (local monotonic clock); may be negative."""
        return self._deadline_mono - self._mgr.estimator.monotonic()

    def is_writable(self) -> bool:
        try:
            self.check()
        except LeaseLostError:
            return False
        return True

    def check(self) -> None:
        """Raise :class:`LeaseLostError` unless a mutating call may be sent now.

        Call immediately before every mutating external call (NFR-REL-03 a).
        """
        if self._released:
            raise LeaseLostError("released", "the lease was released")
        if self._lost is not None:
            raise self._lost
        if self.remaining() <= 0:
            raise self._mark_lost(self._last_failure)

    def guarded(self, call: Callable[[], T]) -> T:
        """Run ``call`` after :meth:`check`. ``call`` must enforce the 60 s total deadline."""
        self.check()
        return call()

    def add_lost_listener(self, listener: Callable[[LeaseLostError], None]) -> None:
        """Called once, from the detecting thread, when the lease is lost."""
        with self._state:
            self._listeners.append(listener)
            lost = self._lost
        if lost is not None:
            self._notify([listener], lost)

    # -- renew --------------------------------------------------------------------------------

    def renew(self) -> bool:
        """One renewal attempt. Returns True on success. Never raises."""
        with self._io:
            if self._released or self._lost is not None:
                return False
            if self.remaining() <= 0:
                self._mark_lost(self._last_failure)
                return False
            self._mgr.sample_clock()
            try:
                now, offset = self._mgr._now_and_offset()
            except ClockUncertainError:
                return self._failed("clock_uncertain")
            new = replace(
                self._info,
                renewed_at=math.floor(now),
                expires_at=math.floor(now + self._mgr.timings.ttl),
            )
            return self._put_renewal(new, offset, retry_on_ours=True)

    def _put_renewal(self, new: LeaseInfo, offset: float, *, retry_on_ours: bool) -> bool:
        mgr = self._mgr
        try:
            etag = mgr.store.put_json(mgr.key, new.to_document(), if_match=self._etag)
        except PreconditionFailed:
            current = self._read_current()
            if current is None:  # could not read: unknown, keep retrying until the deadline
                return self._failed("renewal_failed")
            data_info, etag_now = current
            if retry_on_ours and data_info is not None and self._is_mine(data_info):
                # An earlier ambiguous renewal was applied. Nobody else has written since.
                self._etag = etag_now
                return self._put_renewal(new, offset, retry_on_ours=False)
            self._mark_lost("lease_taken")
            return False
        except Exception:  # ambiguous; the old (earlier, safer) deadline stays in force
            log.warning("lease: renewal failed", exc_info=True)
            return self._failed("renewal_failed")
        with self._state:
            self._etag = etag
            self._info = new
            self._deadline_mono = self._deadline_for(new, offset)
            self._last_failure = "renewal_failed"
        return True

    def _failed(self, detail: LostDetail) -> bool:
        self._last_failure = detail
        if self.remaining() <= 0:
            self._mark_lost(detail)
        return False

    # -- release ------------------------------------------------------------------------------

    def release(self) -> bool:
        """Mark the lease released (``If-Match``). Returns True when S3 confirmed it.

        Idempotent. After this call :meth:`check` always fails. A 412 means the lease was
        already lost, so nothing is written: another holder's lease is never touched.
        """
        self.stop_auto_renew()
        with self._io:
            if self._released:
                return False
            self._released = True
            doc = replace(self._info, released=True).to_document()
            mgr = self._mgr
            try:
                mgr.store.put_json(mgr.key, doc, if_match=self._etag)
            except PreconditionFailed:
                current = self._read_current()
                if current is None or current[0] is None or not self._is_mine(current[0]):
                    log.info("lease release skipped: no longer the holder")
                    return False
                try:  # an earlier ambiguous renewal landed; the document is still ours
                    mgr.store.put_json(mgr.key, doc, if_match=current[1])
                except Exception:  # the lease simply expires
                    log.warning("lease: release retry failed", exc_info=True)
                    return False
            except Exception:  # the lease simply expires after ttl + grace
                log.warning("lease: release failed", exc_info=True)
                return False
            log.info("lease released", extra={"data": {"run_id": self.run_id}})
            return True

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()

    # -- background renewal -------------------------------------------------------------------

    def start_auto_renew(self) -> None:
        """Renew every ``renew_interval`` in a daemon thread until released or lost."""
        with self._state:
            if self._thread is not None:
                return
            self._thread = threading.Thread(
                target=self._renew_loop, name=f"lease-renew-run-{self.run_id}", daemon=True
            )
        self._thread.start()

    def stop_auto_renew(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2 * self._mgr.timings.call_deadline)

    def _renew_loop(self) -> None:
        timings = self._mgr.timings
        delay = timings.renew_interval
        while not self._mgr._wait(self._stop, delay):
            ok = self.renew()
            if self._lost is not None or self._released:
                return
            if ok:
                delay = timings.renew_interval
            else:
                delay = max(0.0, min(timings.renew_retry_interval, self.remaining()))

    # -- internals ----------------------------------------------------------------------------

    def _deadline_for(self, info: LeaseInfo, offset: float) -> float:
        """Write deadline as a local monotonic instant (``expires_at - 120 s - offset``)."""
        return info.expires_at - self._mgr.timings.write_margin - offset

    def _is_mine(self, info: LeaseInfo) -> bool:
        return not info.released and info.is_lease(
            self._mgr.instance_id, self.run_id, self.lease_id
        )

    def _read_current(self) -> tuple[LeaseInfo | None, str] | None:
        """``(parsed doc or None if absent/invalid, etag)``; ``None`` when the read failed."""
        try:
            got = self._mgr.store.get_json(self._mgr.key)
        except Exception:  # unknown state: the caller keeps retrying until the deadline
            log.warning("lease: reading the lease failed", exc_info=True)
            return None
        if got is None:
            return (None, "")
        try:
            return (LeaseInfo.from_document(got[0]), got[1])
        except LeaseDocumentError:
            return (None, got[1])

    def _mark_lost(self, detail: LostDetail) -> LeaseLostError:
        messages = {
            "lease_taken": "another instance took over the lease",
            "renewal_failed": "the lease could not be renewed before its write deadline",
            "clock_uncertain": "S3 time was uncertain until the lease write deadline",
            "released": "the lease was released",
        }
        with self._state:
            if self._lost is not None:
                return self._lost
            self._lost = LeaseLostError(detail, messages[detail])
            listeners = list(self._listeners)
            lost = self._lost
        self._stop.set()
        log.warning("lease lost", extra={"data": {"run_id": self.run_id, "detail": detail}})
        self._notify(listeners, lost)
        return lost

    @staticmethod
    def _notify(listeners: list[Callable[[LeaseLostError], None]], lost: LeaseLostError) -> None:
        for listener in listeners:
            try:
                listener(lost)
            except Exception:  # a faulty listener must not break loss detection
                log.exception("lease lost listener failed")
