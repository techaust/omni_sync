"""Shared config: FR-COORD-03, FR-RULE-02, C-18, 04-DATA-MODEL section 5 (03-ARCHITECTURE 6.3)."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from omnisync.clock import FakeClock
from omnisync.services.errors import ServiceError
from omnisync.services.shared_config import (
    ABSENT,
    HISTORY_LIMIT,
    ConfigChange,
    KeyConflict,
    SharedConfigConflict,
    SharedConfigService,
    default_document,
    validate_document,
)

from .fakes import FakeConditionalS3, VirtualTime

KEY = "_omnisync/config/shared.json"
GPLFAMILY = {
    "key": "gplfamily",
    "label": "GPLFamily",
    "base_url": "https://www.gplfamily.com",
    "enabled": True,
}
PLUGINWAY = {
    "key": "pluginway",
    "label": "PluginWay",
    "base_url": "https://www.pluginway.com",
    "enabled": True,
}


@pytest.fixture
def store() -> FakeConditionalS3:
    return FakeConditionalS3(VirtualTime())


def service(store: FakeConditionalS3, name: str) -> SharedConfigService:
    return SharedConfigService(
        store, instance_name=name, clock=FakeClock(datetime(2026, 10, 11, 10, 0, tzinfo=UTC))
    )


def add(section: Any, key: str, value: Any) -> ConfigChange:
    return ConfigChange(section, key, value)


# --------------------------------------------------------------------------------------------
# Bootstrap and pull
# --------------------------------------------------------------------------------------------


def test_bootstrap_creates_once_with_if_none_match(store: FakeConditionalS3) -> None:
    a, b = service(store, "owner-pc"), service(store, "colleague-pc")
    first = a.bootstrap({"stores": [GPLFAMILY]}, actor="owner")
    assert first.status == "updated"
    assert first.snapshot is not None
    doc = store.peek(KEY)
    assert doc is not None
    assert doc["schema"] == 1
    assert doc["version"] == 0
    assert doc["updated_by"] == "owner@owner-pc"
    assert doc["updated_at"] == "2026-10-11T10:00:00Z"
    assert doc["policies"] == {"downgrade": "hold"}
    assert doc["stores"] == [GPLFAMILY]
    second = b.bootstrap({"stores": [PLUGINWAY]}, actor="colleague")  # already exists: pulled
    assert second.snapshot is not None
    assert second.snapshot.document["stores"] == [GPLFAMILY]
    assert store.peek(KEY)["stores"] == [GPLFAMILY]  # type: ignore[index]


def test_bootstrap_race_falls_back_to_pull(store: FakeConditionalS3) -> None:
    a, b = service(store, "a"), service(store, "b")

    def hook(op: str, key: str | None) -> None:
        if op == "put":
            store.before_commit = None
            b.bootstrap(None, actor="b")  # B creates between A's GET (missing) and A's PUT

    store.before_commit = hook
    result = a.bootstrap(None, actor="a")
    assert result.snapshot is not None
    assert result.snapshot.document["updated_by"] == "b@b"


def test_pull_statuses(store: FakeConditionalS3) -> None:
    a, b = service(store, "a"), service(store, "b")
    missing = a.pull()
    assert missing.status == "missing"
    assert missing.snapshot is None
    b.bootstrap(None, actor="b")
    assert a.pull().status == "updated"
    assert a.pull().status == "unchanged"
    b.apply([add("aliases", "x", "y")], actor="b")
    updated = a.pull()
    assert updated.status == "updated"
    assert updated.snapshot is not None
    assert updated.snapshot.version == 1
    assert updated.snapshot.get("aliases", "x") == "y"
    assert updated.snapshot.get("aliases", "nope") is ABSENT
    assert updated.snapshot.section("aliases") == {"x": "y"}
    assert a.cached is updated.snapshot


def test_invalid_document_is_rejected_and_last_good_copy_kept(store: FakeConditionalS3) -> None:
    a = service(store, "a")
    a.bootstrap({"aliases": {"good": "rule"}}, actor="a")
    good = a.cached
    store.raw_put(KEY, {"schema": 1, "version": "seven"})
    result = a.pull()
    assert result.status == "invalid"
    assert result.error is not None
    assert "version" in result.error
    assert result.snapshot is good
    assert a.cached is good
    with pytest.raises(ServiceError) as err:  # never overwrite a document we cannot validate
        a.apply([add("aliases", "x", "y")], actor="a")
    assert err.value.code == "config.invalid"
    assert store.peek(KEY) == {"schema": 1, "version": "seven"}


def test_version_going_backwards_is_accepted(store: FakeConditionalS3) -> None:
    a = service(store, "a")
    a.bootstrap(None, actor="a")
    a.apply([add("aliases", "x", "y")], actor="a")
    store.raw_put(KEY, default_document())  # restored from an old backup
    assert a.pull().status == "updated"
    assert a.cached is not None
    assert a.cached.version == 0


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"schema": 0}, "schema"),
        ({"schema": "1"}, "schema"),
        ({"version": -1}, "version"),
        ({"version": True}, "version"),
        ({"updated_by": None}, "updated_by"),
        ({"stores": {}}, "stores must be a list"),
        ({"stores": ["x"]}, "store must be an object"),
        ({"stores": [{**GPLFAMILY, "key": "Bad Key"}]}, "store key"),
        ({"stores": [{**GPLFAMILY, "label": ""}]}, "label"),
        ({"stores": [{**GPLFAMILY, "base_url": "http://x"}]}, "https"),
        ({"stores": [{**GPLFAMILY, "enabled": 1}]}, "enabled"),
        ({"stores": [{**GPLFAMILY, "sort_order": "1"}]}, "sort_order"),
        ({"stores": [{**GPLFAMILY, "consumer_secret": "cs_x"}]}, "secrets"),
        ({"stores": [GPLFAMILY, GPLFAMILY]}, "duplicate"),
        ({"aliases": []}, "aliases must be an object"),
        ({"aliases": {"x": ""}}, "non-empty string"),
        ({"overrides": {"x": 3}}, "non-empty string"),
        ({"policies": {"downgrade": ["hold"]}}, "scalar"),
        ({"history": {}}, "history"),
        ({"history": [1]}, "history"),
    ],
)
def test_document_validation(patch: dict[str, Any], message: str) -> None:
    with pytest.raises(ServiceError, match=message) as err:
        validate_document({**default_document(), **patch})
    assert err.value.code == "config.invalid"


def test_document_validation_defaults_sections_and_rejects_non_objects() -> None:
    doc = validate_document({"schema": 1, "version": 3, "updated_at": "", "updated_by": "x"})
    assert doc["stores"] == []
    assert doc["aliases"] == {}
    assert doc["history"] == []
    with pytest.raises(ServiceError):
        validate_document([])
    ok = validate_document({**default_document(), "stores": [{**GPLFAMILY, "sort_order": 2}]})
    assert ok["stores"][0]["sort_order"] == 2


# --------------------------------------------------------------------------------------------
# Push: optimistic concurrency, merge, conflicts
# --------------------------------------------------------------------------------------------


def test_push_increments_version_and_records_history(store: FakeConditionalS3) -> None:
    a = service(store, "owner-pc")
    a.bootstrap(None, actor="owner")
    result = a.apply(
        [
            add("aliases", "elementor-pro-x", "elementor-pro"),
            add("stores", "gplfamily", GPLFAMILY),
            add("policies", "max_parallel", 4),
        ],
        actor="owner",
        source="manual",
    )
    assert result.attempts == 1
    assert len(result.applied) == 3
    doc = store.peek(KEY)
    assert doc is not None
    assert doc["version"] == 1
    assert doc["aliases"] == {"elementor-pro-x": "elementor-pro"}
    assert doc["stores"] == [GPLFAMILY]
    assert doc["history"][0] == {
        "at": "2026-10-11T10:00:00Z",
        "actor": "owner",
        "instance": "owner-pc",
        "source": "manual",
        "section": "aliases",
        "key": "elementor-pro-x",
        "old": None,
        "new": "elementor-pro",
    }
    # Update and delete of a store keep the list keyed by "key".
    disabled = {**GPLFAMILY, "enabled": False}
    a.apply([ConfigChange("stores", "gplfamily", disabled, GPLFAMILY)], actor="owner")
    a.apply([add("stores", "pluginway", PLUGINWAY)], actor="owner")
    a.apply([ConfigChange("stores", "gplfamily", ABSENT, disabled)], actor="owner")
    assert store.peek(KEY)["stores"] == [PLUGINWAY]  # type: ignore[index]
    assert store.peek(KEY)["version"] == 4  # type: ignore[index]


def test_push_without_cache_pulls_first_and_creates_when_missing(store: FakeConditionalS3) -> None:
    a = service(store, "a")
    result = a.apply([add("aliases", "x", "y")], actor="a")  # no bootstrap: created
    assert result.snapshot.version == 1
    assert store.peek(KEY)["aliases"] == {"x": "y"}  # type: ignore[index]


def test_noop_change_writes_nothing(store: FakeConditionalS3) -> None:
    a = service(store, "a")
    a.bootstrap({"aliases": {"x": "y"}}, actor="a")
    puts = store.count("put")
    result = a.apply([ConfigChange("aliases", "x", "y", ABSENT)], actor="a")
    assert result.applied == ()
    assert len(result.unchanged) == 1
    assert store.count("put") == puts


def test_concurrent_edits_to_different_keys_merge(store: FakeConditionalS3) -> None:
    a, b = service(store, "owner-pc"), service(store, "colleague-pc")
    a.bootstrap(None, actor="owner")
    b.pull()
    a.apply([add("aliases", "a-slug", "a-key")], actor="owner")
    result = b.apply([add("aliases", "b-slug", "b-key")], actor="colleague")  # stale ETag
    assert result.attempts == 2  # 412 -> re-pull -> re-apply -> retry once
    doc = store.peek(KEY)
    assert doc is not None
    assert doc["aliases"] == {"a-slug": "a-key", "b-slug": "b-key"}  # no change is lost
    assert doc["version"] == 2
    assert [h["instance"] for h in doc["history"]] == ["owner-pc", "colleague-pc"]


def test_concurrent_edits_to_same_key_conflict(store: FakeConditionalS3) -> None:
    """FR-RULE-02: the second editor gets a conflict with both values; nothing is lost."""
    a, b = service(store, "owner-pc"), service(store, "colleague-pc")
    a.bootstrap({"aliases": {"slug": "old"}}, actor="owner")
    b.pull()
    a.apply([ConfigChange("aliases", "slug", "owner-choice", "old")], actor="owner")
    with pytest.raises(SharedConfigConflict) as err:
        b.apply(
            [
                ConfigChange("aliases", "slug", "colleague-choice", "old"),
                add("aliases", "other", "fine"),  # all-or-nothing: not written either
            ],
            actor="colleague",
        )
    assert err.value.code == "config.conflict"
    assert not err.value.retryable
    assert err.value.conflicts == (
        KeyConflict("aliases", "slug", "owner-choice", "colleague-choice", "old"),
    )
    assert err.value.conflicts[0].as_dict() == {
        "section": "aliases",
        "key": "slug",
        "current": "owner-choice",
        "yours": "colleague-choice",
        "current_exists": True,
        "yours_exists": True,
    }
    doc = store.peek(KEY)
    assert doc is not None
    assert doc["aliases"] == {"slug": "owner-choice"}
    # The user resolves by re-basing on the current value.
    b.apply([ConfigChange("aliases", "slug", "colleague-choice", "owner-choice")], actor="c")
    assert store.peek(KEY)["aliases"]["slug"] == "colleague-choice"  # type: ignore[index]


def test_delete_versus_edit_conflict(store: FakeConditionalS3) -> None:
    a, b = service(store, "a"), service(store, "b")
    a.bootstrap({"download_names": {"slug": "Old"}}, actor="a")
    b.pull()
    a.apply([ConfigChange("download_names", "slug", ABSENT, "Old")], actor="a")
    with pytest.raises(SharedConfigConflict) as err:
        b.apply([ConfigChange("download_names", "slug", "New", "Old")], actor="b")
    conflict = err.value.conflicts[0]
    assert conflict.current is ABSENT
    assert conflict.as_dict()["current"] is None
    assert conflict.as_dict()["current_exists"] is False
    assert repr(ABSENT) == "ABSENT"


def test_same_change_on_both_instances_is_a_noop(store: FakeConditionalS3) -> None:
    a, b = service(store, "a"), service(store, "b")
    a.bootstrap(None, actor="a")
    b.pull()
    a.apply([add("overrides", "file.zip", "slug")], actor="a")
    result = b.apply([add("overrides", "file.zip", "slug")], actor="b")
    assert result.applied == ()
    assert len(result.unchanged) == 1


def test_stale_cache_conflict_is_rechecked_against_fresh_copy(store: FakeConditionalS3) -> None:
    a, b = service(store, "a"), service(store, "b")
    a.bootstrap({"aliases": {"k": "v1"}}, actor="a")
    b.pull()
    a.apply([ConfigChange("aliases", "k", "v2", "v1")], actor="a")
    # B's UI already showed v2 (e.g. from an SSE refresh) but its cache is stale.
    result = b.apply([ConfigChange("aliases", "k", "v3", "v2")], actor="b")
    assert result.attempts == 2
    assert store.peek(KEY)["aliases"]["k"] == "v3"  # type: ignore[index]


def test_persistent_contention_raises_retryable_conflict(store: FakeConditionalS3) -> None:
    a = service(store, "a")
    a.bootstrap(None, actor="a")
    store.inject("put", "conflict_409", key=KEY, times=2)
    with pytest.raises(SharedConfigConflict) as err:
        a.apply([add("aliases", "x", "y")], actor="a")
    assert err.value.conflicts == ()
    assert err.value.retryable


def test_store_errors_propagate(store: FakeConditionalS3) -> None:
    a = service(store, "a")
    a.bootstrap(None, actor="a")
    store.inject("put", "error_500", key=KEY)
    with pytest.raises(Exception, match="500"):
        a.apply([add("aliases", "x", "y")], actor="a")
    store.inject("get", "timeout_before", key=KEY)
    with pytest.raises(Exception, match="timed out"):
        a.pull()


def test_before_write_gate_runs_before_every_put(store: FakeConditionalS3) -> None:
    a = service(store, "a")
    a.bootstrap(None, actor="a")
    calls: list[int] = []
    a.apply([add("aliases", "x", "y")], actor="a", before_write=lambda: calls.append(1))
    assert calls == [1]

    def deny() -> None:
        raise ServiceError("lease.lost", "the lease was released")

    puts = store.count("put")
    with pytest.raises(ServiceError, match=r"lease\.lost"):
        a.apply([add("aliases", "z", "y")], actor="a", before_write=deny)
    assert store.count("put") == puts


def test_history_is_capped_at_500(store: FakeConditionalS3) -> None:
    a = service(store, "a")
    a.bootstrap({"history": [{"n": i} for i in range(HISTORY_LIMIT + 20)]}, actor="a")
    a.apply([add("aliases", "x", "y")], actor="a")
    history = store.peek(KEY)["history"]  # type: ignore[index]
    assert len(history) == HISTORY_LIMIT
    assert history[-1]["key"] == "x"
    assert history[0] == {"n": 21}


def test_deleted_document_is_recreated_from_last_good_copy(store: FakeConditionalS3) -> None:
    a = service(store, "a")
    a.bootstrap({"aliases": {"keep": "me"}}, actor="a")
    store.objects.pop(KEY)
    a.apply([add("aliases", "x", "y")], actor="a")
    doc = store.peek(KEY)
    assert doc is not None
    assert doc["aliases"] == {"keep": "me", "x": "y"}
    assert doc["version"] == 1


def test_noop_on_missing_document_creates_it(store: FakeConditionalS3) -> None:
    a = service(store, "a")
    result = a.apply([ConfigChange("aliases", "x", ABSENT, ABSENT)], actor="a")
    assert result.applied == ()
    assert store.peek(KEY) is not None


@pytest.mark.parametrize(
    "change",
    [
        ConfigChange("rules", "x", "y"),  # type: ignore[arg-type]
        ConfigChange("aliases", "", "y"),
        ConfigChange("aliases", "x", ""),
        ConfigChange("stores", "gplfamily", PLUGINWAY),  # key mismatch
        ConfigChange("stores", "gplfamily", {**GPLFAMILY, "consumer_key": "ck_x"}),
        ConfigChange("policies", "downgrade", {"nested": True}),
    ],
)
def test_invalid_changes_are_refused_before_any_io(
    store: FakeConditionalS3, change: ConfigChange
) -> None:
    a = service(store, "a")
    with pytest.raises(ServiceError) as err:
        a.apply([change], actor="a")
    assert err.value.code == "validation.failed"
    assert store.calls == []


# --------------------------------------------------------------------------------------------
# Schema versions (10-OPERATIONS section 8)
# --------------------------------------------------------------------------------------------


def test_newer_schema_is_readable_but_push_is_refused(store: FakeConditionalS3) -> None:
    a = service(store, "a")
    store.raw_put(KEY, {**default_document(), "schema": 2, "new_section": {"x": 1}})
    result = a.pull()
    assert result.status == "updated"
    assert result.snapshot is not None
    assert result.snapshot.schema == 2
    assert not result.snapshot.writable
    before = store.peek(KEY)
    with pytest.raises(ServiceError) as err:
        a.apply([add("aliases", "x", "y")], actor="a")
    assert err.value.code == "config.schema_too_new"
    with pytest.raises(ServiceError):
        a.merge_missing([("aliases", "x", "y")], actor="a")
    assert store.peek(KEY) == before


def test_unknown_top_level_fields_are_preserved(store: FakeConditionalS3) -> None:
    a = service(store, "a")
    store.raw_put(KEY, {**default_document(), "future_hint": "keep"})
    a.apply([add("aliases", "x", "y")], actor="a")
    assert store.peek(KEY)["future_hint"] == "keep"  # type: ignore[index]


# --------------------------------------------------------------------------------------------
# Legacy import merge (04-DATA-MODEL section 5)
# --------------------------------------------------------------------------------------------


def test_merge_missing_adds_absent_reports_conflicts_and_keeps_existing(
    store: FakeConditionalS3,
) -> None:
    a = service(store, "owner-pc")
    a.bootstrap({"aliases": {"same": "v", "differs": "shared"}}, actor="owner")
    report = a.merge_missing(
        [
            ("aliases", "same", "v"),
            ("aliases", "differs", "legacy"),
            ("aliases", "new", "n"),
            ("stores", "gplfamily", GPLFAMILY),
        ],
        actor="owner",
    )
    assert [c.key for c in report.added] == ["new", "gplfamily"]
    assert [c.key for c in report.unchanged] == ["same"]
    assert report.conflicts == (KeyConflict("aliases", "differs", "shared", "legacy"),)
    doc = store.peek(KEY)
    assert doc is not None
    assert doc["aliases"] == {"same": "v", "differs": "shared", "new": "n"}  # not overwritten
    assert doc["history"][-1]["source"] == "legacy_import"


def test_merge_missing_creates_document_when_absent(store: FakeConditionalS3) -> None:
    a = service(store, "a")
    report = a.merge_missing([("download_names", "slug", "Name")], actor="a")
    assert report.snapshot is not None
    assert report.snapshot.version == 1
    assert store.peek(KEY)["download_names"] == {"slug": "Name"}  # type: ignore[index]


def test_merge_missing_nothing_to_add(store: FakeConditionalS3) -> None:
    a = service(store, "a")
    assert a.merge_missing([], actor="a").snapshot is None  # missing doc, nothing to write
    a.bootstrap({"aliases": {"x": "y"}}, actor="a")
    report = a.merge_missing([("aliases", "x", "y")], actor="a")
    assert report.added == ()
    assert report.snapshot is a.cached


def test_merge_missing_retries_after_concurrent_write(store: FakeConditionalS3) -> None:
    a, b = service(store, "a"), service(store, "b")
    a.bootstrap(None, actor="a")
    b.pull()
    a.apply([add("aliases", "x", "from-a")], actor="a")
    report = b.merge_missing([("aliases", "x", "from-legacy"), ("aliases", "y", "z")], actor="b")
    assert [c.key for c in report.conflicts] == ["x"]
    assert [c.key for c in report.added] == ["y"]
    assert store.peek(KEY)["aliases"] == {"x": "from-a", "y": "z"}  # type: ignore[index]


def test_merge_missing_persistent_contention(store: FakeConditionalS3) -> None:
    a = service(store, "a")
    a.bootstrap(None, actor="a")
    store.inject("put", "conflict_409", key=KEY, times=2)
    with pytest.raises(SharedConfigConflict):
        a.merge_missing([("aliases", "x", "y")], actor="a")


# --------------------------------------------------------------------------------------------
# Two simulated instances editing concurrently (threads) and a property
# --------------------------------------------------------------------------------------------


def test_two_instances_editing_concurrently_in_threads() -> None:
    store = FakeConditionalS3(VirtualTime(), latency=0.01)
    services = [service(store, "a"), service(store, "b")]
    services[0].bootstrap(None, actor="a")
    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def worker(i: int) -> None:
        svc = services[i]
        barrier.wait()
        for n in range(25):
            for _ in range(10):  # the UI retries a retryable conflict
                try:
                    svc.apply([add("aliases", f"i{i}-{n}", f"v{n}")], actor=f"u{i}")
                    break
                except SharedConfigConflict as exc:
                    if not exc.retryable:
                        errors.append(exc)
                        return
            else:
                errors.append(AssertionError("too much contention"))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    doc = store.peek(KEY)
    assert doc is not None
    assert len(doc["aliases"]) == 50  # every edit of both instances survived
    assert doc["version"] == 50
    assert len(doc["history"]) == 50


edits = st.lists(
    st.tuples(
        st.integers(0, 1),
        st.sampled_from(["k1", "k2", "k3"]),
        st.one_of(st.just(ABSENT), st.sampled_from(["x", "y", "z"])),
    ),
    max_size=25,
)


@settings(max_examples=150, deadline=None)
@given(edits)
def test_no_lost_update_property(ops: list[tuple[int, str, Any]]) -> None:
    """Every successful push is based on what its author saw; a conflict never writes."""
    store = FakeConditionalS3(VirtualTime())
    services = [service(store, "a"), service(store, "b")]
    services[0].bootstrap(None, actor="a")
    services[1].pull()
    expected: dict[str, Any] = {}
    for who, key, value in ops:
        svc = services[who]
        seen = svc.cached.get("aliases", key) if svc.cached else ABSENT
        before = store.peek(KEY)
        conflict: SharedConfigConflict | None = None
        try:
            svc.apply([ConfigChange("aliases", key, value, seen)], actor=str(who))
        except SharedConfigConflict as exc:
            conflict = exc
        if conflict is not None:
            assert conflict.conflicts
            assert store.peek(KEY) == before  # nothing written on conflict
            svc.pull()
            continue
        if value is ABSENT:
            expected.pop(key, None)
        else:
            expected[key] = value
        assert store.peek(KEY)["aliases"] == expected  # type: ignore[index]
