"""In-memory fakes for the coordination services (09-TESTING-STRATEGY section 3).

``FakeConditionalS3`` implements exact S3 conditional-write semantics for small JSON documents
(``If-None-Match: *``, ``If-Match``, ETag = quoted MD5 of the body, ``Date`` header with 1 s
resolution) on a shared :class:`VirtualTime`, plus fault injection:

* latency per call, split into the request leg (before the server commits) and the response
  leg (after), so RTT and server-side ordering are both realistic;
* ``timeout_before`` (request lost, nothing applied), ``timeout_after`` (applied, response lost:
  the *ambiguous* failure), ``error_500`` (nothing applied) and ``conflict_409``
  (``ConditionalRequestConflict``, raised as :class:`PreconditionFailed`);
* a server ``Date`` skew, and a ``before_commit`` hook to interleave another writer at an exact
  point.

Nothing sleeps: latency advances the virtual clock.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from omnisync.services.object_store import PreconditionFailed

FaultKind = Literal["timeout_before", "timeout_after", "error_500", "conflict_409"]
Op = Literal["get", "put", "delete", "date"]

START = 1_791_712_800.0  # 2026-10-11T10:00:00Z


class StoreTimeout(Exception):
    """The client gave up waiting (the request may or may not have been applied)."""


class StoreServerError(Exception):
    """HTTP 500 InternalError (nothing applied)."""


class VirtualTime:
    """True (S3) time shared by the fake store and every simulated instance."""

    def __init__(self, start: float = START) -> None:
        self._now = start
        self._lock = threading.Lock()

    def now(self) -> float:
        with self._lock:
            return self._now

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("time does not run backwards")
        with self._lock:
            self._now += seconds


class LocalClock:
    """One instance's monotonic clock: same rate as true time, arbitrary origin (skew)."""

    def __init__(self, vt: VirtualTime, origin: float = 0.0) -> None:
        self.vt = vt
        self.origin = origin

    def monotonic(self) -> float:
        return self.vt.now() + self.origin


@dataclass
class Fault:
    op: Op | Literal["*"]
    kind: FaultKind
    key: str | None = None
    times: int = 1

    def matches(self, op: str, key: str | None) -> bool:
        return (self.op in ("*", op)) and (self.key is None or self.key == key)


@dataclass
class CallRecord:
    op: Op
    key: str | None
    sent: float
    outcome: str
    committed: float | None = None


@dataclass
class _Object:
    body: bytes
    etag: str


def etag_of(body: bytes) -> str:
    return '"' + hashlib.md5(body, usedforsecurity=False).hexdigest() + '"'


def encode(data: dict[str, Any]) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")


LatencyFn = Callable[[str], tuple[float, float]]


@dataclass
class FakeConditionalS3:
    """Implements ``JsonObjectStore`` and ``ServerDateSource`` (see module docstring)."""

    vt: VirtualTime = field(default_factory=VirtualTime)
    latency: float | LatencyFn = 0.0
    date_skew: float = 0.0
    before_commit: Callable[[str, str | None], None] | None = None
    objects: dict[str, _Object] = field(default_factory=dict)
    calls: list[CallRecord] = field(default_factory=list)
    faults: list[Fault] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._lock = threading.RLock()

    # -- fault injection ----------------------------------------------------------------------

    def inject(
        self, op: Op | Literal["*"], kind: FaultKind, *, key: str | None = None, times: int = 1
    ) -> None:
        with self._lock:
            self.faults.append(Fault(op, kind, key, times))

    def _take_fault(self, op: str, key: str | None) -> Fault | None:
        with self._lock:
            for fault in self.faults:
                if fault.matches(op, key):
                    fault.times -= 1
                    if fault.times <= 0:
                        self.faults.remove(fault)
                    return fault
        return None

    def _legs(self, op: str) -> tuple[float, float]:
        if callable(self.latency):
            return self.latency(op)
        return (self.latency / 2, self.latency / 2)

    def _call(self, op: Op, key: str | None, commit: Callable[[], Any]) -> Any:
        record = CallRecord(op, key, self.vt.now(), "pending")
        with self._lock:
            self.calls.append(record)
        before, after = self._legs(op)
        fault = self._take_fault(op, key)
        self.vt.advance(before)
        if fault is not None and fault.kind == "timeout_before":
            self.vt.advance(after)
            record.outcome = "timeout_before"
            raise StoreTimeout(f"{op} {key}: timed out (not applied)")
        if fault is not None and fault.kind == "error_500":
            self.vt.advance(after)
            record.outcome = "error_500"
            raise StoreServerError(f"{op} {key}: 500 InternalError")
        if fault is not None and fault.kind == "conflict_409":
            self.vt.advance(after)
            record.outcome = "conflict_409"
            raise PreconditionFailed(f"{op} {key}: 409 ConditionalRequestConflict")
        if self.before_commit is not None:
            self.before_commit(op, key)
        with self._lock:
            record.committed = self.vt.now()
            try:
                result = commit()
            except PreconditionFailed:
                record.outcome = "412"
                failed = True
            else:
                record.outcome = "ok"
                failed = False
        self.vt.advance(after)
        if failed:
            raise PreconditionFailed(f"{op} {key}: 412 PreconditionFailed")
        if fault is not None and fault.kind == "timeout_after":
            record.outcome = "timeout_after"
            raise StoreTimeout(f"{op} {key}: timed out (applied)")
        return result

    # -- JsonObjectStore ----------------------------------------------------------------------

    def get_json(self, key: str) -> tuple[dict[str, Any], str] | None:
        def commit() -> tuple[dict[str, Any], str] | None:
            obj = self.objects.get(key)
            if obj is None:
                return None
            return json.loads(obj.body), obj.etag

        result: tuple[dict[str, Any], str] | None = self._call("get", key, commit)
        return result

    def put_json(
        self,
        key: str,
        data: dict[str, Any],
        *,
        if_match: str | None = None,
        if_none_match: bool = False,
    ) -> str:
        if if_match is not None and if_none_match:
            raise ValueError("if_match and if_none_match are mutually exclusive")
        body = encode(data)

        def commit() -> str:
            current = self.objects.get(key)
            if if_none_match and current is not None:
                raise PreconditionFailed
            if if_match is not None and (current is None or current.etag != if_match):
                raise PreconditionFailed  # S3: 412, or 404 NoSuchKey for a missing key
            obj = _Object(body, etag_of(body))
            self.objects[key] = obj
            return obj.etag

        etag: str = self._call("put", key, commit)
        return etag

    def delete(self, key: str, *, if_match: str | None = None) -> None:
        def commit() -> None:
            current = self.objects.get(key)
            if if_match is not None and (current is None or current.etag != if_match):
                raise PreconditionFailed
            self.objects.pop(key, None)

        self._call("delete", key, commit)

    # -- ServerDateSource ---------------------------------------------------------------------

    def server_date(self) -> float:
        def commit() -> float:
            return float(math.floor(self.vt.now() + self.date_skew))

        date: float = self._call("date", None, commit)
        return date

    # -- test helpers -------------------------------------------------------------------------

    def raw_put(self, key: str, data: Any) -> str:
        """Write directly, bypassing preconditions and faults (corruption, legacy tools)."""
        body = json.dumps(data).encode("utf-8")
        with self._lock:
            self.objects[key] = _Object(body, etag_of(body))
            return self.objects[key].etag

    def peek(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            obj = self.objects.get(key)
            return None if obj is None else copy.deepcopy(json.loads(obj.body))

    def count(self, op: str, outcome: str | None = None) -> int:
        with self._lock:
            return sum(
                1 for c in self.calls if c.op == op and (outcome is None or c.outcome == outcome)
            )


class ManualWaiter:
    """Deterministic replacement for ``Event.wait`` in renewal/polling loops.

    Each ``wait`` call blocks until the test calls :meth:`tick` (which advances virtual time by
    the requested timeout) or the event is set. No real sleeping beyond thread handoff.
    """

    def __init__(self, vt: VirtualTime) -> None:
        self.vt = vt
        self.requested: list[float] = []
        self._cond = threading.Condition()
        self._tickets = 0
        self._waiting = 0

    def __call__(self, event: threading.Event, timeout: float) -> bool:
        with self._cond:
            self.requested.append(timeout)
            self._waiting += 1
            self._cond.notify_all()
            while self._tickets == 0 and not event.is_set():
                self._cond.wait(0.01)
            self._waiting -= 1
            if event.is_set():
                return True
            self._tickets -= 1
            self.vt.advance(timeout)
            return False

    def wait_until_waiting(self, count: int = 1, timeout: float = 5.0) -> None:
        """Block until ``count`` loop iterations have reached ``wait``."""
        with self._cond:
            if not self._cond.wait_for(lambda: len(self.requested) >= count, timeout):
                raise AssertionError("renewal loop did not reach wait()")

    def wait_for(self, predicate: Callable[[], bool], timeout: float = 5.0) -> None:
        """Poll ``predicate`` (1 ms steps) until true; fail after ``timeout`` seconds."""
        give_up = time.monotonic() + timeout
        with self._cond:
            while not predicate():
                if time.monotonic() > give_up:
                    raise AssertionError("condition not reached")
                self._cond.wait(0.001)

    def tick(self) -> None:
        with self._cond:
            self._tickets += 1
            self._cond.notify_all()


def advancing_wait(vt: VirtualTime, limit: int = 1000) -> Callable[[threading.Event, float], bool]:
    """A ``wait`` that advances virtual time immediately (single-threaded polling tests)."""
    calls = {"n": 0}

    def wait(event: threading.Event, timeout: float) -> bool:
        calls["n"] += 1
        if calls["n"] > limit:
            raise AssertionError("wait() called too often")
        if event.is_set():
            return True
        vt.advance(timeout)
        return event.is_set()

    return wait
