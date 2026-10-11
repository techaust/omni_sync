"""S3 lease: FR-COORD-01/02, NFR-REL-03, D-01, T-11 (03-ARCHITECTURE 6.2 and 6.4)."""

from __future__ import annotations

import math
import random
import threading
from dataclasses import dataclass, field
from typing import Any

import pytest
from hypothesis import HealthCheck, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from omnisync.services.errors import ServiceError
from omnisync.services.lease import (
    LEASE_LOST,
    ClockUncertainError,
    LeaseDocumentError,
    LeaseHandle,
    LeaseInfo,
    LeaseLostError,
    LeaseManager,
    LeaseTimings,
    LeaseUnavailable,
    LeaseWaitCancelled,
    S3TimeEstimator,
    format_s3_time,
    parse_s3_time,
)
from omnisync.services.object_store import JsonObjectStore, PreconditionFailed

from .fakes import (
    START,
    FakeConditionalS3,
    LocalClock,
    ManualWaiter,
    VirtualTime,
    advancing_wait,
)

KEY = "_omnisync/control/lease.json"


def make(
    store: FakeConditionalS3,
    name: str,
    *,
    origin: float = 0.0,
    timings: LeaseTimings | None = None,
    wait: Any = None,
) -> LeaseManager:
    clock = LocalClock(store.vt, origin)
    return LeaseManager(
        store,
        instance_id=f"id-{name}",
        instance_name=name,
        estimator=S3TimeEstimator(clock.monotonic),
        timings=timings,
        wait=wait,
    )


def held(result: LeaseHandle | LeaseUnavailable) -> LeaseHandle:
    assert isinstance(result, LeaseHandle), result
    return result


def busy(result: LeaseHandle | LeaseUnavailable) -> LeaseUnavailable:
    assert isinstance(result, LeaseUnavailable), result
    return result


def acquire(mgr: LeaseManager, run_id: int = 1) -> LeaseHandle | LeaseUnavailable:
    return mgr.try_acquire(run_id=run_id, operation="pipeline_live", user="owner")


@pytest.fixture
def store() -> FakeConditionalS3:
    return FakeConditionalS3(VirtualTime(), latency=0.2)


# --------------------------------------------------------------------------------------------
# Timings, time estimation, document format
# --------------------------------------------------------------------------------------------


def test_timings_default_exactly_as_documented() -> None:
    t = LeaseTimings()
    assert (t.ttl, t.renew_interval, t.write_margin, t.takeover_grace) == (300, 30, 120, 30)
    assert (t.call_deadline, t.poll_interval, t.max_rtt, t.max_clock_error) == (60, 15, 3, 2)
    assert t.safety_gap == 146  # 03-ARCHITECTURE 6.2, safety argument step 6


@pytest.mark.parametrize(
    "kwargs",
    [
        {"ttl": 0},
        {"ttl": float("nan")},
        {"write_margin": 300},
        {"renew_interval": 180},
        {"max_clock_error": 1.0},
        {"write_margin": 30, "renew_interval": 10},  # 30 + 30 - 4 < 60
        {"call_deadline": 147},
    ],
)
def test_unsafe_timings_rejected(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        LeaseTimings(**kwargs)


def test_estimator_offset_and_error_bound() -> None:
    mono = [100.0]
    est = S3TimeEstimator(lambda: mono[0])
    with pytest.raises(ClockUncertainError) as err:
        est.now()
    assert err.value.code == "lease.clock_uncertain"
    assert err.value.retryable
    assert est.error_bound() is None
    # Request sent at mono 100, received at 101; S3 Date says 5000 (true time in [5000, 5001)).
    est.observe(100.0, 101.0, 5000.0)
    assert est.offset() == 5000.0 + 0.5 - 100.5
    assert est.error_bound() == 1.0
    mono[0] = 110.0
    assert est.now() == pytest.approx(5010.0)
    # A faster sample wins even if it is older than slower ones.
    est.observe(110.0, 110.2, 5009.0)
    est.observe(111.0, 113.0, 5011.0)
    assert est.best_sample() is not None
    assert est.best_sample().rtt == pytest.approx(0.2)  # type: ignore[union-attr]
    with pytest.raises(ValueError, match="precedes"):
        est.observe(5.0, 4.0, 1.0)


def test_estimator_uncertain_when_best_rtt_exceeds_3s_and_window_is_10() -> None:
    est = S3TimeEstimator(lambda: 0.0, window=10)
    est.observe(0.0, 1.0, 10.0)
    for _ in range(9):
        est.observe(0.0, 3.5, 10.0)
    est.now()  # the good sample is still among the last 10
    est.observe(0.0, 3.5, 10.0)  # evicts it
    with pytest.raises(ClockUncertainError, match="exceeds"):
        est.now()
    with pytest.raises(ValueError, match="window"):
        S3TimeEstimator(window=0)


@pytest.mark.parametrize("origin", [-1e6, 0.0, 12345.678, 9e8])
@pytest.mark.parametrize("legs", [(0.0, 0.0), (0.1, 1.3), (1.4, 0.05), (0.7, 0.7)])
def test_estimate_within_documented_bound_for_any_local_clock(
    origin: float, legs: tuple[float, float]
) -> None:
    vt = VirtualTime(START + 0.37)
    store = FakeConditionalS3(vt, latency=lambda _op: legs)
    mgr = make(store, "a", origin=origin)
    mgr.sample_clock()
    bound = mgr.estimator.error_bound()
    assert bound is not None
    assert abs(mgr.s3_now() - vt.now()) <= bound + 1e-9
    assert bound <= 2.0


def test_s3_time_format_roundtrip() -> None:
    assert format_s3_time(START + 0.99) == "2026-10-11T10:00:00Z"
    assert parse_s3_time("2026-10-11T10:00:00Z") == START
    assert parse_s3_time("2026-10-11T12:00:00+02:00") == START
    with pytest.raises(ValueError, match="time zone"):
        parse_s3_time("2026-10-11T10:00:00")


GOOD_DOC: dict[str, Any] = {
    "schema": 1,
    "holder_instance_id": "x",
    "holder_instance_name": "pc",
    "holder_user": "owner",
    "operation": "cleanup_execute",
    "run_id": 7,
    "acquired_at": "2026-10-11T10:00:00Z",
    "renewed_at": "2026-10-11T10:00:30Z",
    "expires_at": "2026-10-11T10:05:30Z",
    "released": False,
}


def test_lease_document_roundtrip_without_lease_id() -> None:
    info = LeaseInfo.from_document(GOOD_DOC)
    assert info.lease_id is None
    assert info.expires_at == START + 330
    assert info.to_document() == GOOD_DOC


@pytest.mark.parametrize(
    "patch",
    [
        {"schema": 2},
        {"schema": True},
        {"holder_user": None},
        {"run_id": "7"},
        {"run_id": True},
        {"released": "no"},
        {"lease_id": 5},
        {"expires_at": 5},
        {"expires_at": "soon"},
        {"expires_at": "2026-10-11T10:05:30"},
    ],
)
def test_lease_document_validation(patch: dict[str, Any]) -> None:
    with pytest.raises(LeaseDocumentError):
        LeaseInfo.from_document({**GOOD_DOC, **patch})


def test_lease_document_must_be_object() -> None:
    with pytest.raises(LeaseDocumentError):
        LeaseInfo.from_document([1])


def test_manager_requires_a_server_date_source(store: FakeConditionalS3) -> None:
    class PlainStore:
        def get_json(self, key: str) -> tuple[dict[str, Any], str] | None:
            return None

        def put_json(
            self,
            key: str,
            data: dict[str, Any],
            *,
            if_match: str | None = None,
            if_none_match: bool = False,
        ) -> str:
            return '"e"'

        def delete(self, key: str, *, if_match: str | None = None) -> None:
            return None

    plain: JsonObjectStore = PlainStore()
    with pytest.raises(TypeError, match="ServerDateSource"):
        LeaseManager(plain, instance_id="a", instance_name="a")
    with pytest.raises(ValueError, match="instance_id"):
        LeaseManager(store, instance_id="", instance_name="a")
    mgr = LeaseManager(plain, instance_id="a", instance_name="a", time_source=store)
    assert mgr.key == KEY


# --------------------------------------------------------------------------------------------
# Acquire / renew / release / takeover
# --------------------------------------------------------------------------------------------


def test_fresh_acquire_writes_documented_document(store: FakeConditionalS3) -> None:
    a = make(store, "owner-pc", origin=-5000.0)
    handle = held(a.try_acquire(run_id=418, operation="pipeline_live", user="owner"))
    doc = store.peek(KEY)
    assert doc is not None
    assert doc["schema"] == 1
    assert doc["holder_instance_id"] == "id-owner-pc"
    assert doc["holder_instance_name"] == "owner-pc"
    assert (doc["holder_user"], doc["operation"], doc["run_id"]) == ("owner", "pipeline_live", 418)
    assert doc["released"] is False
    assert doc["lease_id"] == handle.lease_id
    expires = parse_s3_time(doc["expires_at"])
    assert parse_s3_time(doc["acquired_at"]) == parse_s3_time(doc["renewed_at"])
    assert expires - parse_s3_time(doc["acquired_at"]) in (300, 301)
    assert handle.expires_at == expires
    assert handle.write_deadline == expires - 120
    assert 178 <= handle.remaining() <= 180
    assert handle.is_writable()
    assert handle.info.run_id == handle.run_id == 418
    handle.check()
    assert handle.guarded(lambda: 42) == 42
    assert a.read() == handle.info


def test_second_instance_waits_and_sees_holder(store: FakeConditionalS3) -> None:
    a, b = make(store, "a"), make(store, "b", origin=777.0)
    held(acquire(a))
    result = busy(b.try_acquire(run_id=9, operation="cleanup_execute", user="colleague"))
    assert result.reason == "held"
    assert result.retry_after == 15
    assert result.holder is not None
    assert result.holder.holder_instance_name == "a"
    assert "held by a" in result.message


def test_takeover_only_after_expiry_plus_grace(store: FakeConditionalS3) -> None:
    a, b = make(store, "a"), make(store, "b", origin=31337.0)
    ha = held(acquire(a))
    expires = ha.expires_at
    # A stops renewing (crash). Its own write deadline passes 150 s before B may take over.
    store.vt.advance(expires - 120 - store.vt.now() + 1)
    assert not ha.is_writable()
    assert ha.lost_error is not None
    assert ha.lost_error.detail == "renewal_failed"
    # Up to expires + 30 s (by B's estimate, error <= 0.5 + RTT/2) the lease is never broken.
    store.vt.advance(expires + 30 - 1 - store.vt.now())
    assert busy(acquire(b, 2)).reason == "held"
    store.vt.advance(2.5)
    hb = held(acquire(b, 2))
    assert store.peek(KEY)["holder_instance_id"] == "id-b"  # type: ignore[index]
    assert hb.info.acquired_at > expires + 28  # true-time bound from the safety argument


def test_released_lease_is_taken_over_immediately(store: FakeConditionalS3) -> None:
    a, b = make(store, "a"), make(store, "b")
    ha = held(acquire(a))
    assert ha.release() is True
    assert store.peek(KEY)["released"] is True  # type: ignore[index]
    with pytest.raises(LeaseLostError) as err:
        ha.check()
    assert err.value.detail == "released"
    assert not ha.is_writable()
    hb = held(acquire(b, 2))
    assert hb.info.holder_instance_id == "id-b"
    assert ha.release() is False  # idempotent, writes nothing
    assert store.peek(KEY)["holder_instance_id"] == "id-b"  # type: ignore[index]


def test_renew_extends_expiry_and_write_deadline(store: FakeConditionalS3) -> None:
    a = make(store, "a")
    handle = held(acquire(a))
    first = handle.expires_at
    for _ in range(20):  # 10 minutes of healthy renewals
        store.vt.advance(30)
        assert handle.renew() is True
        assert 150 <= handle.remaining() <= 180  # never reached by a healthy holder
    assert handle.expires_at >= first + 600
    doc = store.peek(KEY)
    assert doc is not None
    assert parse_s3_time(doc["renewed_at"]) == handle.info.renewed_at
    assert parse_s3_time(doc["acquired_at"]) == handle.info.acquired_at


def _boom(_e: LeaseLostError) -> None:
    raise RuntimeError("faulty listener")


def test_renew_detects_overwritten_lease_and_listener_fires_once(
    store: FakeConditionalS3,
) -> None:
    a = make(store, "a")
    ha = held(acquire(a))
    seen: list[LeaseLostError] = []
    ha.add_lost_listener(_boom)  # a faulty listener must not break detection
    ha.add_lost_listener(seen.append)
    store.raw_put(KEY, {**GOOD_DOC, "lease_id": "someone-else"})  # e.g. an admin repair
    assert ha.renew() is False
    assert ha.lost
    assert [e.detail for e in seen] == ["lease_taken"]
    puts = store.count("put")
    assert ha.renew() is False  # renew after loss: no write at all
    assert store.count("put") == puts
    with pytest.raises(LeaseLostError) as err:
        ha.check()
    assert err.value.code == LEASE_LOST
    assert err.value.detail == "lease_taken"
    late: list[LeaseLostError] = []
    ha.add_lost_listener(late.append)  # registered after the fact: called immediately
    assert len(late) == 1
    assert len(seen) == 1


def test_renew_after_takeover_is_refused(store: FakeConditionalS3) -> None:
    a, b = make(store, "a"), make(store, "b")
    ha = held(acquire(a))
    store.vt.advance(400)  # A was frozen for longer than ttl + grace
    hb = held(acquire(b, 2))
    puts = store.count("put")
    assert ha.renew() is False  # its write deadline passed long ago: no write is attempted
    assert store.count("put") == puts
    assert ha.lost_error is not None
    assert ha.lost_error.detail == "renewal_failed"
    assert store.peek(KEY)["lease_id"] == hb.lease_id  # type: ignore[index]
    hb.check()


def test_release_by_non_holder_is_refused(store: FakeConditionalS3) -> None:
    a, b = make(store, "a"), make(store, "b")
    ha = held(acquire(a))
    store.vt.advance(400)
    hb = held(acquire(b, 2))
    before = store.peek(KEY)
    assert ha.release() is False  # If-Match with A's stale ETag -> 412 -> nothing written
    assert store.peek(KEY) == before
    assert hb.is_writable()
    # And a raw conditional write with a stale ETag is refused by the store itself.
    with pytest.raises(PreconditionFailed):
        store.put_json(KEY, {"released": True}, if_match=ha._etag)


def test_hold_without_listener_and_default_wait(store: FakeConditionalS3) -> None:
    a = LeaseManager(
        store,
        instance_id="id-a",
        instance_name="a",
        estimator=S3TimeEstimator(LocalClock(store.vt).monotonic),
    )
    with a.hold(run_id=1, operation="x", user="u") as handle:
        handle.check()
    assert handle.released
    b = make(store, "b")
    held(acquire(b, 2))
    cancel = threading.Event()
    cancel.set()
    # The real Event.wait returns immediately for a set event: no sleeping.
    assert a._wait(cancel, 15.0) is True


def test_handle_requires_lease_id_and_loss_is_recorded_once(store: FakeConditionalS3) -> None:
    a = make(store, "a")
    handle = held(acquire(a))
    with pytest.raises(ValueError, match="lease_id"):
        LeaseHandle(a, LeaseInfo.from_document(GOOD_DOC), '"e"', 0.0)
    first = handle._mark_lost("lease_taken")
    assert handle._mark_lost("renewal_failed") is first  # concurrent detections: first wins
    assert first.detail == "lease_taken"


def test_context_manager_releases(store: FakeConditionalS3) -> None:
    a = make(store, "a")
    with held(acquire(a)) as handle:
        handle.check()
    assert handle.released
    assert store.peek(KEY)["released"] is True  # type: ignore[index]


# --------------------------------------------------------------------------------------------
# Chaos: timeouts, 500s, ambiguous writes, crashes
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["timeout_before", "error_500"])
def test_renew_failures_retry_until_write_deadline_then_lost(
    store: FakeConditionalS3, kind: Any
) -> None:
    a = make(store, "a")
    handle = held(acquire(a))
    seen: list[str] = []
    handle.add_lost_listener(lambda e: seen.append(e.detail))
    store.inject("put", kind, key=KEY, times=1000)
    while handle.remaining() > 5:
        store.vt.advance(5)  # renew_retry_interval
        assert handle.renew() is False
        assert not handle.lost or handle.remaining() <= 0
    store.vt.advance(5)
    assert handle.renew() is False
    assert seen == ["renewal_failed"]
    with pytest.raises(LeaseLostError):
        handle.check()


def test_transient_renew_failure_recovers(store: FakeConditionalS3) -> None:
    a = make(store, "a")
    handle = held(acquire(a))
    store.inject("put", "error_500", key=KEY, times=3)
    for _ in range(3):
        store.vt.advance(5)
        assert handle.renew() is False
    store.vt.advance(5)
    assert handle.renew() is True
    assert not handle.lost


def test_ambiguous_renew_that_was_applied_is_recovered(store: FakeConditionalS3) -> None:
    a = make(store, "a")
    handle = held(acquire(a))
    old_expiry = handle.expires_at
    store.vt.advance(30)
    store.inject("put", "timeout_after", key=KEY)  # S3 applied it, the client timed out
    assert handle.renew() is False
    assert handle.expires_at == old_expiry  # the earlier (safer) deadline stays in force
    store.vt.advance(5)
    assert handle.renew() is True  # 412 -> GET shows our lease_id -> adopt ETag -> renew
    assert not handle.lost
    assert handle.expires_at > old_expiry


def test_renew_412_with_unreadable_store_keeps_retrying(store: FakeConditionalS3) -> None:
    a = make(store, "a")
    handle = held(acquire(a))
    store.inject("put", "conflict_409", key=KEY)
    store.inject("get", "timeout_before", key=KEY)
    assert handle.renew() is False
    assert not handle.lost  # unknown state: not declared lost before the deadline
    assert handle.renew() is True


def test_renew_with_corrupted_document_is_lost(store: FakeConditionalS3) -> None:
    a = make(store, "a")
    handle = held(acquire(a))
    store.raw_put(KEY, {"garbage": True})
    assert handle.renew() is False
    assert handle.lost_error is not None
    assert handle.lost_error.detail == "lease_taken"


def test_renew_with_deleted_document_is_lost(store: FakeConditionalS3) -> None:
    a = make(store, "a")
    handle = held(acquire(a))
    store.delete(KEY)
    assert handle.renew() is False
    assert handle.lost


def test_renew_while_clock_uncertain_ends_with_clock_uncertain() -> None:
    vt = VirtualTime()
    legs = [(0.1, 0.1)]
    store = FakeConditionalS3(vt, latency=lambda _op: legs[0])
    a = make(store, "a")
    handle = held(acquire(a))
    legs[0] = (2.0, 2.0)  # RTT 4 s: once the good samples leave the window, S3 time is uncertain
    for _ in range(12):
        a.sample_clock()
    with pytest.raises(ClockUncertainError):
        a.s3_now()
    results = []
    while not handle.lost:
        vt.advance(5)
        results.append(handle.renew())
    assert not any(results)
    assert handle.lost_error is not None
    assert handle.lost_error.detail == "clock_uncertain"
    assert handle.lost_error.code == "lease.lost"


def test_acquire_refused_while_clock_uncertain() -> None:
    store = FakeConditionalS3(VirtualTime(), latency=4.0)
    result = busy(acquire(make(store, "a")))
    assert result.reason == "clock_uncertain"
    assert store.peek(KEY) is None


def test_clock_sample_failure_falls_back_to_recent_samples(store: FakeConditionalS3) -> None:
    a = make(store, "a")
    assert a.sample_clock() is not None
    store.inject("date", "timeout_before")
    assert a.sample_clock() is None
    held(acquire(a))  # the next sample succeeds; but even without it the older one is valid
    fresh = make(store, "b")
    store.inject("date", "error_500")
    assert busy(acquire(fresh)).reason == "clock_uncertain"  # no sample at all


def test_ambiguous_create_that_was_applied_is_adopted(store: FakeConditionalS3) -> None:
    a = make(store, "a")
    store.inject("put", "timeout_after", key=KEY)
    handle = held(acquire(a))  # create timed out -> GET finds our own lease_id -> If-Match
    assert store.peek(KEY)["lease_id"] == handle.lease_id  # type: ignore[index]


def test_ambiguous_create_adopted_on_next_attempt(store: FakeConditionalS3) -> None:
    a, b = make(store, "a"), make(store, "b")
    store.inject("put", "timeout_after", key=KEY)
    store.inject("get", "timeout_before", key=KEY)
    assert busy(acquire(a)).reason == "store_error"
    assert busy(acquire(b, 2)).reason == "held"  # B must not treat it as free
    handle = held(acquire(a))  # A recognises its own earlier write: no 5-minute wait
    assert handle.info.holder_instance_id == "id-a"
    # The memory is per run: a different run of A does not adopt it.
    assert busy(acquire(a, run_id=99)).reason == "held"


def test_ambiguous_takeover_write(store: FakeConditionalS3) -> None:
    a, b = make(store, "a"), make(store, "b")
    held(acquire(a))
    store.vt.advance(400)
    store.inject("put", "timeout_after", key=KEY, times=2)  # create (412 first) then takeover
    assert busy(acquire(b, 2)).reason == "store_error"
    held(acquire(b, 2))


def test_crash_without_release_then_takeover_after_ttl(store: FakeConditionalS3) -> None:
    a = make(store, "a")
    ha = held(acquire(a))
    for _ in range(3):
        store.vt.advance(30)
        assert ha.renew()
    crash_expiry = ha.expires_at
    del ha  # process killed: no release, no more renewals
    waits: list[LeaseUnavailable] = []
    b = make(store, "b", origin=-424242.0, wait=advancing_wait(store.vt))
    hb = b.acquire(run_id=5, operation="cleanup_execute", user="colleague", on_wait=waits.append)
    assert {w.reason for w in waits} == {"held"}
    taken_at = hb.info.acquired_at
    assert crash_expiry + 28 < taken_at <= crash_expiry + 30 + 15 + 2
    # "about 6 minutes" upper bound from ADR-0007.
    assert taken_at - (crash_expiry - 300) <= 6 * 60


def test_cleanup_waits_for_live_run_then_runs(store: FakeConditionalS3) -> None:
    """D-01 / FR-COORD-01: a cleanup created during a live run starts only after release."""
    owner, colleague = make(store, "owner"), make(store, "colleague")
    live = held(owner.try_acquire(run_id=1, operation="pipeline_live", user="owner"))
    state: dict[str, Any] = {"polls": 0}

    def wait(event: threading.Event, timeout: float) -> bool:
        state["polls"] += 1
        store.vt.advance(timeout)
        live.renew()
        if state["polls"] == 4:
            live.release()
        return False

    colleague._wait = wait
    cleanup = colleague.acquire(run_id=2, operation="cleanup_execute", user="colleague")
    assert state["polls"] == 4
    assert cleanup.info.operation == "cleanup_execute"
    assert live.released


def test_acquire_wait_can_be_cancelled(store: FakeConditionalS3) -> None:
    a = make(store, "a")
    held(acquire(a))
    cancel = threading.Event()

    def wait(event: threading.Event, timeout: float) -> bool:
        event.set()
        return True

    b = make(store, "b", wait=wait)
    with pytest.raises(LeaseWaitCancelled) as err:
        b.acquire(run_id=2, operation="x", user="u", cancel=cancel)
    assert err.value.code == "lease.wait_cancelled"
    with pytest.raises(LeaseWaitCancelled):
        b.acquire(run_id=2, operation="x", user="u", cancel=cancel)  # already cancelled


def test_invalid_or_newer_document_is_never_overwritten(store: FakeConditionalS3) -> None:
    a = make(store, "a")
    store.raw_put(KEY, {**GOOD_DOC, "schema": 2, "expires_at": "2000-01-01T00:00:00Z"})
    before = store.peek(KEY)
    result = busy(acquire(a))
    assert result.reason == "invalid_document"
    assert store.peek(KEY) == before
    with pytest.raises(ServiceError) as err:
        a.read()
    assert err.value.code == "lease.document_invalid"


def test_read_absent(store: FakeConditionalS3) -> None:
    assert make(store, "a").read() is None


def test_document_deleted_between_create_and_read(store: FakeConditionalS3) -> None:
    a = make(store, "a")
    store.raw_put(KEY, GOOD_DOC)

    def hook(op: str, key: str | None) -> None:
        if op == "get":
            store.objects.pop(KEY, None)

    store.before_commit = hook
    handle = held(acquire(a))
    store.before_commit = None
    assert handle.info.holder_instance_id == "id-a"


def test_takeover_race_lost_reports_contended(store: FakeConditionalS3) -> None:
    a, b, c = make(store, "a"), make(store, "b"), make(store, "c")
    held(acquire(a))
    store.vt.advance(400)
    puts = []

    def hook(op: str, key: str | None) -> None:
        if op == "put":
            puts.append(op)
            if len(puts) == 2:  # B's takeover PUT (the first was its 412 create)
                store.before_commit = None
                held(acquire(c, 3))  # C takes over between B's GET and B's PUT

    store.before_commit = hook
    result = busy(acquire(b, 2))
    assert result.reason == "contended"
    assert store.peek(KEY)["holder_instance_id"] == "id-c"  # type: ignore[index]


def test_store_error_while_reading_is_a_wait_reason(store: FakeConditionalS3) -> None:
    a, b = make(store, "a"), make(store, "b")
    held(acquire(a))
    store.inject("get", "error_500", key=KEY)
    assert busy(acquire(b, 2)).reason == "store_error"


def test_release_after_ambiguous_renew_still_releases(store: FakeConditionalS3) -> None:
    a, b = make(store, "a"), make(store, "b")
    handle = held(acquire(a))
    store.inject("put", "timeout_after", key=KEY)
    assert handle.renew() is False
    assert handle.release() is True  # 412 -> doc still ours -> released with its ETag
    held(acquire(b, 2))


@pytest.mark.parametrize("fault", ["timeout_before", "error_500"])
def test_release_failures_are_not_fatal(store: FakeConditionalS3, fault: Any) -> None:
    a = make(store, "a")
    handle = held(acquire(a))
    store.inject("put", fault, key=KEY)
    assert handle.release() is False
    assert not handle.is_writable()


def test_release_retry_failure(store: FakeConditionalS3) -> None:
    a = make(store, "a")
    handle = held(acquire(a))
    store.inject("put", "timeout_after", key=KEY)
    handle.renew()
    store.inject("put", "error_500", key=KEY, times=2)
    store.inject("put", "conflict_409", key=KEY)
    store.faults.reverse()  # 409 first, then the retry fails with 500
    assert handle.release() is False


def test_lost_holder_may_still_release_its_own_stale_lease(store: FakeConditionalS3) -> None:
    """A holder past its deadline (renewals failing) releases so the peer need not wait."""
    a, b = make(store, "a"), make(store, "b")
    handle = held(acquire(a))
    store.vt.advance(181)
    assert not handle.is_writable()
    assert handle.release() is True
    held(acquire(b, 2))


# --------------------------------------------------------------------------------------------
# Threads: two instances racing; background renewal
# --------------------------------------------------------------------------------------------


def test_two_instances_racing_in_threads() -> None:
    vt = VirtualTime()
    store = FakeConditionalS3(vt, latency=0.01)
    managers = [make(store, "a", origin=1e5), make(store, "b", origin=-3e4)]
    for round_no in range(40):
        barrier = threading.Barrier(2)
        results: list[Any] = [None, None]

        def worker(
            i: int,
            rn: int = round_no,
            bar: threading.Barrier = barrier,
            out: list[Any] = results,
        ) -> None:
            bar.wait()
            out[i] = managers[i].try_acquire(run_id=rn, operation="x", user="u")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        winners = [r for r in results if isinstance(r, LeaseHandle)]
        losers = [r for r in results if isinstance(r, LeaseUnavailable)]
        assert len(winners) == 1, results
        assert losers[0].reason in ("held", "contended")
        assert store.peek(KEY)["lease_id"] == winners[0].lease_id  # type: ignore[index]
        assert winners[0].release()


def test_background_renewal_and_loss_detection() -> None:
    vt = VirtualTime()
    store = FakeConditionalS3(vt, latency=0.1)
    waiter = ManualWaiter(vt)
    a = make(store, "a", wait=waiter)
    lost = threading.Event()
    details: list[str] = []

    def on_lost(e: LeaseLostError) -> None:
        details.append(e.detail)
        lost.set()

    with a.hold(run_id=1, operation="pipeline_live", user="owner", on_lost=on_lost) as handle:
        for n in range(1, 4):
            waiter.wait_until_waiting(n)
            assert waiter.requested[-1] == 30
            waiter.tick()
        waiter.wait_until_waiting(4)
        assert store.count("put", "ok") == 4  # acquire + 3 renewals
        assert handle.is_writable()
        # Make renewals fail: the loop switches to the 5 s retry cadence.
        store.inject("put", "error_500", key=KEY, times=1000)
        waiter.tick()
        waiter.wait_until_waiting(5)
        assert waiter.requested[-1] == 5
        # Someone overwrites the lease (simulated takeover): the next renewal detects it.
        store.faults.clear()
        store.raw_put(KEY, {**GOOD_DOC, "lease_id": "other"})
        waiter.tick()
        assert lost.wait(5)
        assert details == ["lease_taken"]
        with pytest.raises(LeaseLostError):
            handle.check()
    assert handle.released
    assert store.peek(KEY)["lease_id"] == "other"  # type: ignore[index]  # untouched


def test_auto_renew_start_is_idempotent_and_stops_on_release() -> None:
    vt = VirtualTime()
    store = FakeConditionalS3(vt)
    waiter = ManualWaiter(vt)
    a = make(store, "a", wait=waiter)
    handle = held(acquire(a))
    handle.start_auto_renew()
    handle.start_auto_renew()
    waiter.wait_until_waiting(1)
    assert handle.release()
    assert handle._thread is not None
    assert not handle._thread.is_alive()


def test_listener_releasing_from_renewal_thread_does_not_deadlock() -> None:
    vt = VirtualTime()
    store = FakeConditionalS3(vt)
    waiter = ManualWaiter(vt)
    a = make(store, "a", wait=waiter)
    handle = held(acquire(a))
    done = threading.Event()

    def on_lost(_e: LeaseLostError) -> None:
        handle.release()
        done.set()

    handle.add_lost_listener(on_lost)
    handle.start_auto_renew()
    waiter.wait_until_waiting(1)
    store.raw_put(KEY, {**GOOD_DOC, "lease_id": "other"})
    waiter.tick()
    assert done.wait(5)
    handle.stop_auto_renew()
    assert handle.released


def test_renewal_loop_exits_when_deadline_passes() -> None:
    vt = VirtualTime()
    store = FakeConditionalS3(vt)
    waiter = ManualWaiter(vt)
    a = make(store, "a", wait=waiter)
    handle = held(acquire(a))
    store.inject("put", "timeout_before", key=KEY, times=10_000)
    handle.start_auto_renew()
    n = 0
    while not handle.lost:
        n += 1
        assert n < 100
        waiter.wait_until_waiting(n)
        waiter.tick()
        # Either the loop reaches wait() again or it detected the loss and exits.
        waiter.wait_for(lambda k=n: len(waiter.requested) > k or handle.lost)
    handle.stop_auto_renew()
    assert handle.lost_error is not None
    assert handle.lost_error.detail == "renewal_failed"


# --------------------------------------------------------------------------------------------
# T-11: the single residual stale call after a freeze
# --------------------------------------------------------------------------------------------


def test_freeze_after_check_allows_at_most_one_stale_call(store: FakeConditionalS3) -> None:
    a, b = make(store, "a"), make(store, "b")
    ha = held(acquire(a))
    sent: list[tuple[str, float]] = []

    def mutating_call(who: str) -> None:
        sent.append((who, store.vt.now()))

    ha.check()  # passes ...
    store.vt.advance(10 * 60)  # ... then the laptop is suspended for 10 minutes
    hb = held(acquire(b, 2))
    mutating_call("a")  # the stale in-flight call (T-11, accepted)
    for _ in range(5):  # every later call re-checks the deadline
        with pytest.raises(LeaseLostError):
            ha.guarded(lambda: mutating_call("a"))
    hb.guarded(lambda: mutating_call("b"))
    stale = [t for who, t in sent if who == "a" and t > hb.info.acquired_at]
    assert len(stale) == 1


# --------------------------------------------------------------------------------------------
# NFR-REL-03 property: randomised schedules (no freezes between check and send)
# --------------------------------------------------------------------------------------------


@dataclass
class _LeaseLog:
    instance: int
    acquired: float  # true time just before the acquiring call (a lower bound)
    released: float | None = None
    checks: list[float] = field(default_factory=list)


class Simulation:
    """Two or three instances with skewed clocks, random latency and injected faults."""

    def __init__(self, rng: random.Random, instances: int = 2) -> None:
        self.rng = rng
        self.vt = VirtualTime(START + rng.random() * 1000)
        self.store = FakeConditionalS3(self.vt, latency=self._latency)
        self.timings = LeaseTimings()
        self.managers = [
            make(self.store, f"i{i}", origin=rng.uniform(-1e6, 1e6)) for i in range(instances)
        ]
        self.handles: list[LeaseHandle | None] = [None] * instances
        self.logs: dict[str, _LeaseLog] = {}
        self.slow = False

    def _latency(self, _op: str) -> tuple[float, float]:
        top = 2.5 if self.slow else 1.2
        return (self.rng.uniform(0, top), self.rng.uniform(0, top))

    # actions
    def advance(self, seconds: float) -> None:
        self.vt.advance(seconds)

    def advance_to_boundary(self, choice: float, jitter: float) -> None:
        """Jump close to a critical instant of the newest lease (deadline, expiry, takeover)."""
        live = [h for h in self.handles if h is not None]
        if not live:
            return
        newest = max(live, key=lambda h: self.logs[h.lease_id].acquired)
        anchors = (
            newest.expires_at - self.timings.write_margin,
            newest.expires_at - self.timings.call_deadline,
            newest.expires_at,
            newest.expires_at + self.timings.takeover_grace,
        )
        target = anchors[int(choice * len(anchors)) % len(anchors)] + jitter
        if target > self.vt.now():
            self.vt.advance(target - self.vt.now())

    def acquire(self, i: int) -> None:
        current = self.handles[i]
        if current is not None and not (current.lost or current.released):
            return
        start = self.vt.now()
        result = self.managers[i].try_acquire(run_id=i, operation="op", user="u")
        if isinstance(result, LeaseHandle):
            self.handles[i] = result
            self.logs[result.lease_id] = _LeaseLog(i, start)

    def renew(self, i: int) -> None:
        handle = self.handles[i]
        if handle is not None:
            handle.renew()

    def release(self, i: int) -> None:
        handle = self.handles[i]
        if handle is not None and not handle.released:
            self.logs[handle.lease_id].released = self.vt.now()
            handle.release()

    def crash(self, i: int) -> None:
        self.handles[i] = None

    def fault(self, op: Any, kind: Any) -> None:
        self.store.inject(op, kind)

    def mutate(self, i: int) -> None:
        handle = self.handles[i]
        if handle is not None and handle.is_writable():
            self.logs[handle.lease_id].checks.append(self.vt.now())

    def run(self, steps: int) -> None:
        for _ in range(steps):
            self.step()
            for i in range(len(self.managers)):  # a mutating call is attempted at every step
                self.mutate(i)
        self.check_invariants()

    def step(self) -> None:
        r = self.rng
        i = r.randrange(len(self.managers))
        action = r.random()
        if action < 0.2:
            self.advance(r.choice([r.uniform(0, 8), r.uniform(0, 40), r.uniform(0, 400)]))
        elif action < 0.32:
            self.advance_to_boundary(r.random(), r.uniform(-6, 6))
        elif action < 0.45:
            self.acquire(i)
        elif action < 0.6:
            self.renew(i)
        elif action < 0.65:
            self.release(i)
        elif action < 0.68:
            self.crash(i)
        elif action < 0.76:
            self.fault(
                r.choice(["get", "put", "date"]),
                r.choice(["timeout_before", "timeout_after", "error_500", "conflict_409"]),
            )
        elif action < 0.78:
            self.slow = not self.slow
        else:
            self.mutate(i)

    def check_invariants(self) -> None:
        """NFR-REL-03 (a)+(b): a permitted send under lease L1 completes (60 s) before any
        later lease L2 is acquired, unless L1 was released first (its calls had finished)."""
        logs = sorted(self.logs.values(), key=lambda entry: entry.acquired)
        deadline = self.timings.call_deadline
        for n, first in enumerate(logs):
            for later in logs[n + 1 :]:
                for t in first.checks:
                    if first.released is not None and first.released <= later.acquired:
                        assert t <= first.released
                    else:
                        assert t + deadline < later.acquired, (first, later)


@pytest.mark.parametrize("instances", [2, 3])
def test_mutual_exclusion_over_1000_randomised_trials(instances: int) -> None:
    trials = 500  # x2 parametrisations = 1,000 trials (NFR-REL-03)
    for seed in range(trials):
        Simulation(random.Random(seed * 7919 + instances), instances).run(60)  # noqa: S311


class LeaseMachine(RuleBasedStateMachine):
    """Hypothesis-driven schedules for the same invariant."""

    def __init__(self) -> None:
        super().__init__()
        self.sim = Simulation(random.Random(0))  # noqa: S311 - deterministic test schedule

    @rule(seconds=st.floats(min_value=0, max_value=400))
    def advance(self, seconds: float) -> None:
        self.sim.advance(seconds)

    @rule(choice=st.floats(0, 1), jitter=st.floats(-6, 6))
    def advance_to_boundary(self, choice: float, jitter: float) -> None:
        self.sim.advance_to_boundary(choice, jitter)

    @rule(i=st.integers(0, 1))
    def acquire(self, i: int) -> None:
        self.sim.acquire(i)

    @rule(i=st.integers(0, 1))
    def renew(self, i: int) -> None:
        self.sim.renew(i)

    @rule(i=st.integers(0, 1))
    def release(self, i: int) -> None:
        self.sim.release(i)

    @rule(i=st.integers(0, 1))
    def crash(self, i: int) -> None:
        self.sim.crash(i)

    @rule(
        op=st.sampled_from(["get", "put", "date"]),
        kind=st.sampled_from(["timeout_before", "timeout_after", "error_500", "conflict_409"]),
    )
    def fault(self, op: str, kind: str) -> None:
        self.sim.fault(op, kind)

    @rule()
    def toggle_slow_network(self) -> None:
        self.sim.slow = not self.sim.slow

    @rule(i=st.integers(0, 1))
    def mutate(self, i: int) -> None:
        self.sim.mutate(i)

    @invariant()
    def mutual_exclusion(self) -> None:
        for i in range(2):
            self.sim.mutate(i)
        self.sim.check_invariants()

    @invariant()
    def at_most_one_writable_handle(self) -> None:
        writable = [h for h in self.sim.handles if h is not None and h.is_writable()]
        assert len(writable) <= 1


LeaseMachine.TestCase.settings = settings(
    max_examples=60,
    stateful_step_count=40,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
test_lease_state_machine = LeaseMachine.TestCase


def test_safety_margin_matches_architecture() -> None:
    """Safety argument steps 5-6 with worst-case estimation errors of 2 s each."""
    t = LeaseTimings()
    expires = 1000.0
    last_send_true = expires - t.write_margin + t.max_clock_error  # holder's clock 2 s slow
    earliest_takeover_true = expires + t.takeover_grace - t.max_clock_error  # taker 2 s fast
    assert earliest_takeover_true - last_send_true == 146
    assert earliest_takeover_true - (last_send_true + t.call_deadline) == 86
    assert math.isclose(t.safety_gap, 146)
