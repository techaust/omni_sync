"""Tests for ``services/woocommerce.py`` (FR-PIPE-04, FR-PIPE-05, FR-PIPE-10, FR-CAT-01).

No real network and no real sleeping: every client talks to an ``httpx2.MockTransport``
(:mod:`tests.services.wc_fake`) and uses a :class:`FakeTime` for backoff, rate gate and breaker.
"""

from __future__ import annotations

import base64
import threading
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Any

import httpcore2
import httpx2
import pytest

from omnisync.clock import FakeClock
from omnisync.core.catalog import MAX_PAGES, PER_PAGE
from omnisync.core.shortcodes import EntryMatch
from omnisync.services import woocommerce as wc
from omnisync.services.errors import ServiceError
from omnisync.services.woocommerce import (
    CIRCUIT_OPEN,
    MAX_RETRIES,
    RETRYABLE_STATUS_CODES,
    WC_AUTH_FAILED,
    WC_ENTRY_NOT_FOUND,
    WC_HTTP_ERROR,
    WC_INVALID_RESPONSE,
    WC_PAGE_LIMIT,
    WC_WRITE_IGNORED,
    CatalogProgress,
    CircuitBreaker,
    CircuitBreakerOpen,
    RateGate,
    StoreConfig,
    WooCommerceClient,
    WooCoordination,
    WriteOutcome,
    WriteResult,
    backoff_seconds,
    read_live_version,
)
from tests.services.wc_fake import FakeWooStore, entry, product

# --------------------------------------------------------------------------- fixtures


class FakeTime:
    """Monotonic clock advanced only by ``sleep``/``advance``; records every sleep."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []
        self._lock = threading.Lock()

    def monotonic(self) -> float:
        with self._lock:
            return self.now

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self.sleeps.append(seconds)
            self.now += seconds

    def advance(self, seconds: float) -> None:
        with self._lock:
            self.now += seconds


def store_config(
    store_id: str = "gplfamily",
    base_url: str = "https://example.com",
    *,
    key: Any = "ck",
    secret: Any = "cs",  # noqa: S107 - fake credential for the mock transport
    delay: float = 0.0,
) -> StoreConfig:
    return StoreConfig(store_id, base_url, key, secret, label=store_id.title(), request_delay=delay)


@pytest.fixture
def ftime() -> FakeTime:
    return FakeTime()


@pytest.fixture
def coord(ftime: FakeTime) -> WooCoordination:
    return WooCoordination(monotonic=ftime.monotonic)


@pytest.fixture
def make_client(
    ftime: FakeTime, coord: WooCoordination
) -> Iterator[Callable[..., WooCommerceClient]]:
    made: list[WooCommerceClient] = []

    def _make(fake: FakeWooStore, store: StoreConfig | None = None, **kw: Any) -> WooCommerceClient:
        kw.setdefault("coordination", coord)
        kw.setdefault("sleep", ftime.sleep)
        kw.setdefault("clock", FakeClock())
        client = WooCommerceClient(store or store_config(), transport=fake.transport(), **kw)
        made.append(client)
        return client

    yield _make
    for c in made:
        c.close()


def update(client: WooCommerceClient, pid: int = 42, **kw: Any) -> WriteResult:
    args: dict[str, Any] = {
        "slug": "flatsome",
        "object_key": "flatsome-3.20.9.zip",
        "download_name": "Download",
        "is_primary": True,
        "version": "3.20.9",
    }
    args.update(kw)
    return client.update_product_download(pid, **args)


def flatsome_store() -> FakeWooStore:
    return FakeWooStore(
        products={42: product(downloads=[entry("Download", "flatsome.zip", "id1")])}
    )


# --------------------------------------------------------------------------- legacy: circuit breaker


class TestCircuitBreaker:
    def test_stays_closed_on_successes(self) -> None:
        """Ported from legacy tests/test_wc.py::TestCircuitBreaker::test_stays_closed_on_successes."""
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=60)
        cb.record_success()
        cb.record_success()
        cb.check()

    def test_trips_after_threshold(self) -> None:
        """Ported from legacy tests/test_wc.py::TestCircuitBreaker::test_trips_after_threshold."""
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=60)
        for _ in range(3):
            cb.record_failure()
        with pytest.raises(CircuitBreakerOpen, match="OPEN") as exc:
            cb.check()
        assert exc.value.remaining == pytest.approx(60, abs=1)

    def test_recovers_after_timeout(self) -> None:
        """Ported from legacy tests/test_wc.py::TestCircuitBreaker::test_recovers_after_timeout."""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0)
        cb.record_failure()
        cb.record_failure()
        cb.check()
        assert cb.consecutive_failures == 0

    def test_reset_on_success(self) -> None:
        """Ported from legacy tests/test_wc.py::TestCircuitBreaker::test_reset_on_success."""
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=60)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        cb.record_success()
        cb.check()

    def test_wc_client_records_failure_and_blocks(
        self, ftime: FakeTime, make_client: Callable[..., WooCommerceClient]
    ) -> None:
        """Ported from legacy tests/test_wc.py::TestCircuitBreaker::test_wc_client_records_failure_and_blocks."""
        fake = flatsome_store()
        coordination = WooCoordination(monotonic=ftime.monotonic, failure_threshold=1)
        client = make_client(fake, coordination=coordination)
        client.breaker.record_failure()
        with pytest.raises(ServiceError) as exc:
            client.get_product(42)
        assert exc.value.code == CIRCUIT_OPEN
        assert exc.value.retryable is True
        assert "OPEN" in exc.value.message
        assert fake.seen == []

    def test_wc_client_resets_on_success(
        self, make_client: Callable[..., WooCommerceClient]
    ) -> None:
        """Ported from legacy tests/test_wc.py::TestCircuitBreaker::test_wc_client_resets_on_success."""
        client = make_client(flatsome_store())
        client.breaker.record_failure()
        client.get_product(42)
        assert client.breaker.consecutive_failures == 0

    def test_clock_injection_controls_cool_down(self, ftime: FakeTime) -> None:
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=60, monotonic=ftime.monotonic)
        cb.record_failure()
        ftime.advance(59.9)
        with pytest.raises(CircuitBreakerOpen):
            cb.check()
        ftime.advance(0.1)
        cb.check()


# --------------------------------------------------------------------------- legacy: update flow


class TestUpdateProductDownload:
    def test_rewrites_only_matching_entry(
        self, make_client: Callable[..., WooCommerceClient]
    ) -> None:
        """Ported from legacy tests/test_wc.py::TestUpdateProductDownload::test_rewrites_only_matching_entry."""
        fake = FakeWooStore(
            products={
                42: product(
                    downloads=[
                        entry("Flatsome", "flatsome.zip", "id1"),
                        entry("Flatsome Child", "flatsome-child.zip", "id2"),
                    ]
                )
            }
        )
        res = update(make_client(fake))
        assert res.outcome is WriteOutcome.WRITTEN
        assert res.ok
        assert res.error_code is None
        assert res.old_object_key == "flatsome.zip"
        assert res.entry_id == "id1"
        assert res.match is EntryMatch.EXACT_BASE
        payload = fake.puts()[0].body
        files = payload["downloadable_files"]
        assert files[0]["file"] == '[amazon_s3 object="flatsome-3.20.9.zip"]'
        assert files[0]["name"] == "Download"
        assert files[1] == entry("Flatsome Child", "flatsome-child.zip", "id2")
        assert {"key": "latest_version", "value": "3.20.9"} in payload["meta_data"]
        assert {"key": "latest_version_flatsome", "value": "3.20.9"} in payload["meta_data"]

    def test_secondary_sets_per_slug_meta_only(
        self, make_client: Callable[..., WooCommerceClient]
    ) -> None:
        """Ported from legacy tests/test_wc.py::TestUpdateProductDownload::test_secondary_sets_per_slug_meta_only."""
        fake = FakeWooStore(
            products={
                42: product(
                    downloads=[
                        entry("Download", "flatsome-3.20.9.zip", "id1"),
                        entry("X", "flatsome-child.zip", "id2"),
                    ],
                    metas=[{"key": "latest_version", "value": "3.20.9"}],
                )
            }
        )
        res = update(
            make_client(fake),
            slug="flatsome-child",
            object_key="flatsome-child-1.0.0.zip",
            download_name="Child",
            is_primary=False,
            version="1.0.0",
        )
        assert res.ok
        metas = fake.puts()[0].body["meta_data"]
        assert {"key": "latest_version", "value": "3.20.9"} in metas
        assert {"key": "latest_version_flatsome-child", "value": "1.0.0"} in metas
        assert len([m for m in metas if m["key"] == "latest_version"]) == 1

    def test_missing_entry_fails_safely(
        self, make_client: Callable[..., WooCommerceClient]
    ) -> None:
        """Ported from legacy tests/test_wc.py::TestUpdateProductDownload::test_missing_entry_fails_safely."""
        fake = FakeWooStore(products={42: product(downloads=[entry("Other", "other.zip", "id9")])})
        res = update(make_client(fake))
        assert res.outcome is WriteOutcome.FAILED
        assert res.error_code == WC_ENTRY_NOT_FOUND
        assert "no uniquely matching download entry" in (res.error_message or "")
        assert res.old_object_key is None
        assert fake.puts() == []

    def test_writes_back_to_downloads_field_when_api_uses_downloads(
        self, make_client: Callable[..., WooCommerceClient]
    ) -> None:
        """Ported from legacy tests/test_wc.py::TestUpdateProductDownload::test_writes_back_to_downloads_field_when_api_uses_downloads."""
        fake = FakeWooStore(
            products={
                42: {
                    "id": 42,
                    "name": "PixelYourSite Pro",
                    "downloads": [entry("Download", "pixelyoursite-pro.zip", "id1")],
                    "meta_data": [],
                }
            }
        )
        res = update(
            make_client(fake),
            slug="pixelyoursite-pro",
            object_key="pixelyoursite-pro-12.6.3.1.zip",
            version="12.6.3.1",
        )
        assert res.ok
        assert res.error_code is None
        p = fake.puts()[0].body
        assert "downloads" in p
        assert "downloadable_files" not in p
        assert p["downloads"][0]["file"] == '[amazon_s3 object="pixelyoursite-pro-12.6.3.1.zip"]'
        assert res.old_object_key == "pixelyoursite-pro.zip"

    def test_fails_when_api_silently_ignores_file_write(
        self, make_client: Callable[..., WooCommerceClient]
    ) -> None:
        """Ported from legacy tests/test_wc.py::TestUpdateProductDownload::test_fails_when_api_silently_ignores_file_write.

        FR-PIPE-04 acceptance: the failure code is ``wc.write_ignored``.
        """
        fake = flatsome_store()
        fake.ignore_writes = True
        res = update(make_client(fake))
        assert res.outcome is WriteOutcome.FAILED
        assert res.error_code == WC_WRITE_IGNORED
        assert "download entry" in (res.error_message or "")
        assert len(fake.puts()) == 1


# --------------------------------------------------------------------------- legacy: serialisation


@dataclass
class InstrumentedLock:
    """A real lock that signals when a thread *tries* to acquire it (no timing sleeps needed)."""

    inner: Any = field(default_factory=threading.Lock)
    attempts: list[str] = field(default_factory=list)
    attempted: threading.Event = field(default_factory=threading.Event)

    def __enter__(self) -> bool:
        self.attempts.append(threading.current_thread().name)
        self.attempted.set()
        return bool(self.inner.acquire())

    def __exit__(self, *_exc: object) -> None:
        self.inner.release()


class TestProductSerialization:
    def test_same_product_cycles_serialize(self, ftime: FakeTime) -> None:
        """Ported from legacy tests/test_wc.py::TestProductSerialization::test_same_product_cycles_serialize.

        Legacy slept 0.3 s to let worker b reach the lock; here an instrumented lock signals the
        acquire attempt, so the test is deterministic.
        """
        locks: list[InstrumentedLock] = []

        def factory() -> AbstractContextManager[Any]:
            lock = InstrumentedLock()
            locks.append(lock)
            return lock

        coordination = WooCoordination(monotonic=ftime.monotonic, lock_factory=factory)
        timeline: list[str] = []
        got = threading.Event()
        release = threading.Event()
        fake = flatsome_store()

        def on_request(request: httpx2.Request) -> None:
            tag = "a" if threading.current_thread().name == "worker-a" else "b"
            timeline.append(f"{tag}-{request.method}")
            if request.method == "GET" and tag == "a":
                got.set()
                assert release.wait(timeout=5)

        fake.on_request = on_request
        client = WooCommerceClient(
            store_config(), coordination=coordination, transport=fake.transport(), sleep=ftime.sleep
        )
        results: dict[str, WriteResult] = {}

        def work(name: str) -> None:
            results[name] = update(client, object_key="flatsome-1.0.0.zip", version="1.0.0")

        ta = threading.Thread(target=work, args=("worker-a",), name="worker-a", daemon=True)
        tb = threading.Thread(target=work, args=("worker-b",), name="worker-b", daemon=True)
        try:
            ta.start()
            assert got.wait(timeout=5)
            locks[0].attempted.clear()
            tb.start()
            assert locks[0].attempted.wait(timeout=5)
            assert "b-GET" not in timeline
            release.set()
            ta.join(timeout=5)
            tb.join(timeout=5)
        finally:
            release.set()
            client.close()
        assert results["worker-a"].outcome is WriteOutcome.WRITTEN
        assert results["worker-b"].outcome is WriteOutcome.SKIPPED  # a's write already applied
        assert timeline.index("b-GET") > timeline.index("a-PUT"), timeline
        assert len(locks) == 1

    def test_different_products_do_not_serialize(
        self, make_client: Callable[..., WooCommerceClient]
    ) -> None:
        """Ported from legacy tests/test_wc.py::TestProductSerialization::test_different_products_do_not_serialize."""
        fake = FakeWooStore(
            products={
                pid: product(pid, downloads=[entry("Download", "flatsome.zip", "id1")])
                for pid in (100, 200)
            }
        )
        started100 = threading.Event()
        started200 = threading.Event()

        def on_request(request: httpx2.Request) -> None:
            if request.method != "GET":
                return
            if request.url.path.endswith("products/100"):
                started100.set()
                started200.wait(timeout=5)
            else:
                started200.set()

        fake.on_request = on_request
        client = make_client(fake)
        t100 = threading.Thread(target=update, args=(client, 100), daemon=True)
        t200 = threading.Thread(target=update, args=(client, 200), daemon=True)
        t100.start()
        assert started100.wait(timeout=5)
        t200.start()
        got = started200.wait(timeout=5)
        t100.join(timeout=5)
        t200.join(timeout=5)
        assert got

    def test_same_product_id_on_other_store_is_independent(self, coord: WooCoordination) -> None:
        assert coord.product_lock("a", 42) is coord.product_lock("a", 42)
        assert coord.product_lock("a", 42) is not coord.product_lock("b", 42)
        assert coord.product_lock("a", 42) is not coord.product_lock("a", 43)


# --------------------------------------------------------------------------- legacy: PUT retry budget


def _date_modified_400() -> httpx2.Response:
    return httpx2.Response(
        400,
        json={
            "code": "rest_invalid_param",
            "message": "Invalid parameter(s): date_modified",
            "data": {"status": 400, "params": {"date_modified": "Invalid date."}},
        },
    )


class TestPutRetryBudget:
    def test_ok(self, make_client: Callable[..., WooCommerceClient]) -> None:
        """Ported from legacy tests/test_wc.py::TestPutRetryBudget::test_ok."""
        fake = flatsome_store()
        res = update(make_client(fake))
        assert res.ok
        assert res.error_code is None
        assert len(fake.puts()) == 1

    def test_persistent_rate_limit(
        self, ftime: FakeTime, make_client: Callable[..., WooCommerceClient]
    ) -> None:
        """Ported from legacy tests/test_wc.py::TestPutRetryBudget::test_persistent_rate_limit."""
        fake = flatsome_store()
        fake.add_fault("PUT", *[429] * 5)
        res = update(make_client(fake))
        assert res.outcome is WriteOutcome.FAILED
        assert res.error_code == WC_HTTP_ERROR
        assert "after 3 retries" in (res.error_message or "")
        assert len(fake.puts()) == MAX_RETRIES + 1
        assert ftime.sleeps == [2.0, 4.0, 8.0]

    def test_auth_not_retried(self, make_client: Callable[..., WooCommerceClient]) -> None:
        """Ported from legacy tests/test_wc.py::TestPutRetryBudget::test_auth_not_retried."""
        fake = flatsome_store()
        fake.add_fault("PUT", lambda _r: httpx2.Response(401, text="secret detail"))
        res = update(make_client(fake))
        assert res.error_code == WC_AUTH_FAILED
        assert res.retryable is False
        assert "authentication failed" in (res.error_message or "")
        assert "secret detail" not in (res.error_message or "")
        assert len(fake.puts()) == 1

    def test_date_modified_fallback(self, make_client: Callable[..., WooCommerceClient]) -> None:
        """Ported from legacy tests/test_wc.py::TestPutRetryBudget::test_date_modified_fallback."""
        fake = flatsome_store()
        fake.reject_date_modified = True
        res = update(make_client(fake))
        assert res.ok
        assert res.error_code is None
        first, second = fake.puts()
        assert first.body["date_modified"] == "2026-01-01T00:00:00"
        assert "date_modified" not in second.body
        assert second.body["meta_data"] == first.body["meta_data"]

    def test_unrelated_400_does_not_trim(
        self, make_client: Callable[..., WooCommerceClient]
    ) -> None:
        """Ported from legacy tests/test_wc.py::TestPutRetryBudget::test_unrelated_400_does_not_trim."""
        fake = flatsome_store()
        fake.add_fault(
            "PUT",
            lambda _r: httpx2.Response(
                400,
                json={
                    "code": "rest_invalid_param",
                    "message": "Invalid parameter(s): meta_data",
                    "data": {"status": 400, "params": {"meta_data": "bad"}},
                },
            ),
        )
        res = update(make_client(fake))
        assert res.outcome is WriteOutcome.FAILED
        assert res.error_code == WC_HTTP_ERROR
        assert "HTTP 400" in (res.error_message or "")
        assert len(fake.puts()) == 1

    def test_non_json_400_does_not_trim(
        self, make_client: Callable[..., WooCommerceClient]
    ) -> None:
        fake = flatsome_store()
        fake.add_fault("PUT", 400)
        res = update(make_client(fake))
        assert res.error_code == WC_HTTP_ERROR
        assert len(fake.puts()) == 1


# --------------------------------------------------------------------------- legacy: catalog fetch


class TestFetchDownloadableProducts:
    def test_transient_5xx_retried_then_succeeds(
        self, ftime: FakeTime, make_client: Callable[..., WooCommerceClient]
    ) -> None:
        """Ported from legacy tests/test_mappings.py::TestFetchDownloadableProducts::test_transient_5xx_retried_then_succeeds.

        The retry policy is now the shared 2/4/8 s budget (FR-PIPE-10) instead of legacy's
        5 s steps for up to 60 s.
        """
        fake = FakeWooStore(listing=[{"id": 1}], total_pages_header="1")
        fake.add_fault("GET", 520)
        prods = make_client(fake).fetch_catalog_pages()
        assert len(fake.calls("GET")) == 2
        assert prods == [{"id": 1}]
        assert ftime.sleeps == [2.0]

    def test_auth_failure_aborts(self, make_client: Callable[..., WooCommerceClient]) -> None:
        """Ported from legacy tests/test_mappings.py::TestFetchDownloadableProducts::test_auth_failure_aborts."""
        fake = FakeWooStore()
        fake.add_fault("GET", lambda _r: httpx2.Response(403, text="forbidden"))
        with pytest.raises(ServiceError) as exc:
            make_client(fake).fetch_catalog_pages()
        assert exc.value.code == WC_AUTH_FAILED
        assert exc.value.retryable is False
        assert "authentication failed" in exc.value.message
        assert len(fake.seen) == 1

    def test_retryable_set_includes_cloudflare(self) -> None:
        """Ported from legacy tests/test_mappings.py::TestFetchDownloadableProducts::test_retryable_set_includes_cloudflare."""
        assert {429, 500, 502, 503, 504, 520, 521, 522} <= RETRYABLE_STATUS_CODES
        assert 408 in RETRYABLE_STATUS_CODES

    def test_missing_total_pages_header_fetches_all_pages(
        self, make_client: Callable[..., WooCommerceClient]
    ) -> None:
        """Ported from legacy tests/test_mappings.py::TestFetchDownloadableProducts::test_missing_total_pages_header_fetches_all_pages."""
        listing = [{"id": i} for i in range(PER_PAGE * 2)] + [{"id": 999}]
        fake = FakeWooStore(listing=listing, total_pages_header=None)
        prods = make_client(fake).fetch_catalog_pages()
        assert len(fake.calls("GET")) == 3
        assert len(prods) == PER_PAGE * 2 + 1

    def test_present_header_stops_at_declared_pages(
        self, make_client: Callable[..., WooCommerceClient]
    ) -> None:
        """Ported from legacy tests/test_mappings.py::TestFetchDownloadableProducts::test_present_header_stops_at_declared_pages."""
        full = [{"id": i} for i in range(PER_PAGE)]
        fake = FakeWooStore(total_pages_header="2")
        headers = {"X-WP-TotalPages": "2"}
        fake.add_fault("GET", *[lambda _r: httpx2.Response(200, json=full, headers=headers)] * 5)
        prods = make_client(fake).fetch_catalog_pages()
        assert len(fake.calls("GET")) == 2
        assert len(prods) == PER_PAGE * 2


class TestRefreshMappingPurity:
    def test_incomplete_config_raises_mapping_fetch_error(self, coord: WooCoordination) -> None:
        """Ported from legacy tests/test_mappings.py::TestRefreshMappingPurity::test_incomplete_config_raises_mapping_fetch_error.

        Legacy raised ``MappingFetchError`` code 2 (auth/config); the service raises
        ``wc.auth_failed`` before sending any request.
        """
        fake = FakeWooStore()
        with pytest.raises(ServiceError) as exc:
            wc.refresh_catalog(
                store_config(key="", secret=""),
                "productrepo",
                coordination=coord,
                transport=fake.transport(),
            )
        assert exc.value.code == WC_AUTH_FAILED
        assert exc.value.retryable is False
        assert fake.seen == []


# --------------------------------------------------------------------------- FR-PIPE-10 acceptance


class TestResilienceAcceptance:
    @pytest.mark.parametrize("method", ["GET", "PUT"])
    def test_503_twice_then_200_succeeds_after_two_retries(
        self, method: str, ftime: FakeTime, make_client: Callable[..., WooCommerceClient]
    ) -> None:
        """FR-PIPE-10: a store returning 503 twice then 200 -> the write succeeds after 2 retries."""
        fake = flatsome_store()
        fake.add_fault(method, 503, 503)
        res = update(make_client(fake))
        assert res.outcome is WriteOutcome.WRITTEN
        assert len(fake.calls(method)) == 3
        assert ftime.sleeps == [2.0, 4.0]

    def test_503_four_times_fails_with_http_error(
        self, ftime: FakeTime, make_client: Callable[..., WooCommerceClient]
    ) -> None:
        """FR-PIPE-10: 503 four times -> store result ``failed`` with ``wc.http_error``."""
        fake = flatsome_store()
        fake.add_fault("PUT", 503, 503, 503, 503)
        res = update(make_client(fake))
        assert res.outcome is WriteOutcome.FAILED
        assert res.error_code == WC_HTTP_ERROR
        assert res.retryable is True
        assert len(fake.puts()) == 4
        assert ftime.sleeps == [2.0, 4.0, 8.0]

    def test_five_failures_open_circuit_for_60s_other_store_unaffected(
        self, ftime: FakeTime, make_client: Callable[..., WooCommerceClient]
    ) -> None:
        """FR-PIPE-10: 5 consecutive failures -> ``circuit_open`` for 60 s; other stores continue."""
        down = flatsome_store()
        down.add_fault("GET", *[503] * 4 * 5)
        healthy = flatsome_store()
        a = make_client(down, store_config("gplfamily", "https://a.example.com"))
        b = make_client(healthy, store_config("pluginway", "https://b.example.com"))
        for _ in range(5):
            assert update(a).error_code == WC_HTTP_ERROR
        sent = len(down.seen)
        assert sent == 20

        blocked = update(a)
        assert blocked.outcome is WriteOutcome.FAILED
        assert blocked.error_code == CIRCUIT_OPEN
        assert blocked.retryable is True
        assert len(down.seen) == sent  # failed immediately: no request

        assert update(b).outcome is WriteOutcome.WRITTEN  # other store continues

        ftime.advance(59.0)
        assert update(a).error_code == CIRCUIT_OPEN
        assert len(down.seen) == sent
        ftime.advance(1.0)
        assert update(a).outcome is WriteOutcome.WRITTEN
        assert a.breaker.consecutive_failures == 0

    def test_separate_clients_of_one_store_share_the_breaker(
        self, make_client: Callable[..., WooCommerceClient]
    ) -> None:
        fake = flatsome_store()
        first = make_client(fake)
        second = make_client(fake)
        for _ in range(5):
            first.breaker.record_failure()
        assert update(second).error_code == CIRCUIT_OPEN


# --------------------------------------------------------------------------- D-02 regression


@pytest.mark.behaviour_change("D-02")
def test_two_entries_without_slug_match_need_input_and_write_nothing(
    make_client: Callable[..., WooCommerceClient],
) -> None:
    """D-02 / FR-PIPE-05: two entries, no slug match -> ``ambiguous_entry``, zero PUTs.

    Legacy fell back to the first entry named like the button and overwrote it.
    """
    fake = FakeWooStore(
        products={
            42: product(
                downloads=[
                    entry("Download", "other-a.zip", "e1"),
                    entry("Download", "other-b.zip", "e2"),
                ]
            )
        }
    )
    res = update(make_client(fake))
    assert res.outcome is WriteOutcome.NEEDS_INPUT
    assert res.reason == "ambiguous_entry"
    assert res.match is EntryMatch.AMBIGUOUS
    assert fake.puts() == []
    assert len(fake.calls("GET")) == 1


# --------------------------------------------------------------------------- update flow details


class TestUpdateDetails:
    def test_entry_id_hint_wins_and_is_reported(
        self, make_client: Callable[..., WooCommerceClient]
    ) -> None:
        fake = FakeWooStore(
            products={
                42: product(
                    downloads=[entry("A", "x.zip", "e1"), entry("B", "y.zip", "e2")],
                )
            }
        )
        res = update(make_client(fake), entry_id_hint="e2")
        assert res.ok
        assert res.entry_id == "e2"
        assert res.match is EntryMatch.ENTRY_ID
        assert (
            fake.puts()[0].body["downloadable_files"][1]["file"].endswith('"flatsome-3.20.9.zip"]')
        )

    def test_primary_replaces_existing_meta_and_keeps_others(
        self, make_client: Callable[..., WooCommerceClient]
    ) -> None:
        metas = [
            {"id": 1, "key": "latest_version", "value": "3.0"},
            {"id": 2, "key": "latest_version_flatsome", "value": "3.0"},
            {"id": 3, "key": "_other", "value": "keep"},
            "junk",
        ]
        fake = FakeWooStore(
            products={42: product(downloads=[entry("D", "flatsome.zip")], metas=metas)}
        )
        res = update(make_client(fake))
        assert res.live_version == "3.0"
        sent = fake.puts()[0].body["meta_data"]
        assert sent == [
            {"id": 3, "key": "_other", "value": "keep"},
            "junk",
            {"key": "latest_version_flatsome", "value": "3.20.9"},
            {"key": "latest_version", "value": "3.20.9"},
        ]

    def test_already_current_entry_is_skipped_without_put(
        self, make_client: Callable[..., WooCommerceClient]
    ) -> None:
        fake = FakeWooStore(
            products={
                42: product(
                    downloads=[entry("Download", "flatsome-3.20.9.zip", "id1")],
                    metas=[{"key": "latest_version_flatsome", "value": "3.20.9"}],
                )
            }
        )
        res = update(make_client(fake))
        assert res.outcome is WriteOutcome.SKIPPED
        assert res.ok
        assert res.old_object_key == "flatsome-3.20.9.zip"
        assert fake.puts() == []

    def test_dict_shaped_downloads_and_bad_meta(
        self, make_client: Callable[..., WooCommerceClient]
    ) -> None:
        fake = FakeWooStore(
            products={
                42: {
                    "id": 42,
                    "downloads": {"k": entry("Download", "flatsome.zip", "id1"), "s": "junk"},
                    "meta_data": "not-a-list",
                }
            }
        )
        res = update(make_client(fake))
        assert res.ok
        body = fake.puts()[0].body
        assert body["downloads"][0]["id"] == "id1"
        assert len(body["downloads"]) == 1

    def test_non_list_downloads_means_no_entries(
        self, make_client: Callable[..., WooCommerceClient]
    ) -> None:
        fake = FakeWooStore(products={42: {"id": 42, "downloadable_files": "weird"}})
        res = update(make_client(fake))
        assert res.error_code == WC_ENTRY_NOT_FOUND
        assert res.match is EntryMatch.NOT_FOUND

    def test_get_failure_is_returned_as_failed(
        self, make_client: Callable[..., WooCommerceClient]
    ) -> None:
        fake = FakeWooStore()
        res = update(make_client(fake), pid=7)
        assert res.outcome is WriteOutcome.FAILED
        assert res.error_code == WC_HTTP_ERROR
        assert "HTTP 404" in (res.error_message or "")
        assert res.retryable is False

    def test_put_response_not_json_is_write_ignored(
        self, make_client: Callable[..., WooCommerceClient]
    ) -> None:
        fake = flatsome_store()
        fake.add_fault("PUT", lambda _r: httpx2.Response(200, text="<html>ok</html>"))
        res = update(make_client(fake))
        assert res.error_code == WC_WRITE_IGNORED

    @pytest.mark.parametrize(
        "echo",
        [
            {"downloads": {"k": entry("Download", "flatsome-3.20.9.zip")}},
            {"downloadable_files": "weird"},
            ["not", "a", "dict"],
        ],
    )
    def test_verify_handles_echo_shapes(
        self, echo: Any, make_client: Callable[..., WooCommerceClient]
    ) -> None:
        fake = flatsome_store()
        fake.add_fault("PUT", lambda _r: httpx2.Response(200, json=echo))
        res = update(make_client(fake))
        expected = isinstance(echo, dict) and "downloads" in echo
        assert res.ok is expected
        if not expected:
            assert res.error_code == WC_WRITE_IGNORED

    def test_before_write_runs_before_every_put_attempt(
        self, make_client: Callable[..., WooCommerceClient]
    ) -> None:
        fake = flatsome_store()
        fake.add_fault("PUT", 503)
        calls: list[int] = []

        def before_write() -> None:
            calls.append(len(fake.puts()))

        res = update(make_client(fake), before_write=before_write)
        assert res.ok
        assert calls == [0, 1]  # checked before the first PUT and before the retry

    def test_before_write_exception_propagates_and_nothing_is_sent(
        self, make_client: Callable[..., WooCommerceClient]
    ) -> None:
        class LeaseLost(Exception):
            pass

        def before_write() -> None:
            raise LeaseLost("write deadline passed")

        fake = flatsome_store()
        with pytest.raises(LeaseLost, match="write deadline"):
            update(make_client(fake), before_write=before_write)
        assert fake.puts() == []

    def test_before_write_service_error_is_not_swallowed(
        self, make_client: Callable[..., WooCommerceClient]
    ) -> None:
        def before_write() -> None:
            raise ServiceError("lease.lost", "renewal_failed")

        fake = flatsome_store()
        with pytest.raises(ServiceError) as exc:
            update(make_client(fake), before_write=before_write)
        assert exc.value.code == "lease.lost"
        assert fake.puts() == []

    def test_get_product_returns_dict_and_live_version(
        self, make_client: Callable[..., WooCommerceClient]
    ) -> None:
        fake = FakeWooStore(
            products={42: product(metas=[{"key": "latest_version_flatsome", "value": "1.2"}])}
        )
        client = make_client(fake)
        prod = client.get_product(42)
        assert prod["id"] == 42
        assert client.read_live_version(prod, "flatsome") == "1.2"


class TestReadLiveVersion:
    def test_per_slug_meta(self) -> None:
        prod = product(metas=[{"key": "latest_version_x", "value": " 2.0 "}])
        assert read_live_version(prod, "x") == "2.0"

    def test_primary_falls_back_to_latest_version(self) -> None:
        prod = product(metas=[{"key": "latest_version", "value": 3}])
        assert read_live_version(prod, "x") is None
        assert read_live_version(prod, "x", is_primary=True) == "3"

    def test_blank_and_missing(self) -> None:
        prod = product(metas=[{"key": "latest_version_x", "value": ""}, "junk"])
        assert read_live_version(prod, "x") is None
        assert read_live_version({"meta_data": None}, "x") is None


# --------------------------------------------------------------------------- transport errors


class TestTransportErrors:
    def test_network_error_is_retried_then_succeeds(
        self, ftime: FakeTime, make_client: Callable[..., WooCommerceClient]
    ) -> None:
        fake = flatsome_store()
        fake.add_fault("GET", httpx2.ConnectError("boom"), httpx2.ReadTimeout("slow"))
        res = update(make_client(fake))
        assert res.ok
        assert ftime.sleeps == [2.0, 4.0]

    def test_network_errors_exhausted_record_one_breaker_failure(
        self, make_client: Callable[..., WooCommerceClient]
    ) -> None:
        fake = flatsome_store()
        fake.add_fault("GET", *[httpx2.ConnectError("down")] * 4)
        client = make_client(fake)
        with pytest.raises(ServiceError) as exc:
            client.get_product(42)
        assert exc.value.code == WC_HTTP_ERROR
        assert exc.value.retryable is True
        assert "network error after 3 retries" in exc.value.message
        assert client.breaker.consecutive_failures == 1

    def test_non_retryable_5xx_fails_immediately(
        self, ftime: FakeTime, make_client: Callable[..., WooCommerceClient]
    ) -> None:
        fake = flatsome_store()
        fake.add_fault("GET", 501)
        client = make_client(fake)
        with pytest.raises(ServiceError) as exc:
            client.get_product(42)
        assert exc.value.code == WC_HTTP_ERROR
        assert exc.value.retryable is False
        assert ftime.sleeps == []
        assert client.breaker.consecutive_failures == 1

    def test_redirect_is_not_followed(self, make_client: Callable[..., WooCommerceClient]) -> None:
        fake = flatsome_store()
        fake.add_fault(
            "GET", lambda _r: httpx2.Response(301, headers={"Location": "https://evil.test/"})
        )
        with pytest.raises(ServiceError) as exc:
            make_client(fake).get_product(42)
        assert exc.value.code == WC_HTTP_ERROR
        assert "redirect" in exc.value.message
        assert len(fake.seen) == 1

    @pytest.mark.parametrize(
        "response",
        [httpx2.Response(200, text="not json"), httpx2.Response(200, json=[1, 2])],
    )
    def test_invalid_product_body(
        self, response: httpx2.Response, make_client: Callable[..., WooCommerceClient]
    ) -> None:
        fake = flatsome_store()
        fake.add_fault("GET", lambda _r: response)
        with pytest.raises(ServiceError) as exc:
            make_client(fake).get_product(42)
        assert exc.value.code == WC_INVALID_RESPONSE

    def test_oversized_response_is_refused(
        self, monkeypatch: pytest.MonkeyPatch, make_client: Callable[..., WooCommerceClient]
    ) -> None:
        monkeypatch.setattr(wc, "MAX_RESPONSE_BYTES", 10)
        fake = flatsome_store()
        with pytest.raises(ServiceError) as exc:
            make_client(fake).get_product(42)
        assert exc.value.code == WC_INVALID_RESPONSE
        assert len(fake.seen) == 1


# --------------------------------------------------------------------------- call deadline


class TestCallDeadline:
    def test_put_carries_deadline_extensions_and_get_does_not(
        self, make_client: Callable[..., WooCommerceClient]
    ) -> None:
        fake = flatsome_store()
        update(make_client(fake))
        get, put = fake.calls("GET")[0], fake.puts()[0]
        assert "trace" not in get.extensions
        assert callable(put.extensions["trace"])
        assert put.extensions["timeout"] == dict.fromkeys(
            ("connect", "read", "write", "pool"), 60.0
        )

    def test_trace_shrinks_timeouts_and_refuses_late_phases(self, ftime: FakeTime) -> None:
        deadline = wc._CallDeadline(60.0, ftime.monotonic)
        ftime.advance(45.0)
        deadline.trace("connection.connect_tcp.started", {})
        assert deadline.timeouts["read"] == pytest.approx(15.0)
        deadline.trace("connection.connect_tcp.complete", {})  # ignored
        ftime.advance(15.0)
        with pytest.raises(wc._DeadlineExceeded):
            deadline.trace("http11.send_request_headers.started", {})

    @staticmethod
    def _real_transport(ftime: FakeTime, step: float, log: list[tuple[str, float | None]]) -> Any:
        """The real httpx2/httpcore2 stack over an in-memory socket (no network).

        Each socket write advances the fake clock by ``step`` seconds and every phase records
        the timeout httpcore passed down, proving the trace hook bounds each phase.
        """
        reply = [
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}"
        ]

        class Stream(httpcore2.MockStream):
            def write(self, buffer: bytes, timeout: float | None = None) -> None:
                log.append(("write", timeout))
                ftime.advance(step)

            def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
                log.append(("read", timeout))
                return super().read(max_bytes, timeout)

        class Backend(httpcore2.MockBackend):
            def connect_tcp(
                self, host: str, port: int, timeout: float | None = None, **_: Any
            ) -> Any:
                log.append(("connect", timeout))
                ftime.advance(5.0)
                return Stream(list(reply))

        transport = httpx2.HTTPTransport()
        # Swap only the socket layer; httpx2 has no public hook for a network backend.
        transport._pool = httpcore2.ConnectionPool(network_backend=Backend(reply))
        return transport

    def test_real_stack_phases_get_the_remaining_budget(
        self, ftime: FakeTime, coord: WooCoordination
    ) -> None:
        log: list[tuple[str, float | None]] = []
        client = WooCommerceClient(
            store_config(),
            coordination=coord,
            transport=self._real_transport(ftime, 10.0, log),
            sleep=ftime.sleep,
        )
        with client:
            reply = client._request("PUT", "products/42", payload={"a": 1})
        assert reply.status == 200
        timeouts = [(kind, round(t or 0, 3)) for kind, t in log]
        # connect at t=0; request headers at t=5; body at t=15; body writes end at t=35.
        assert timeouts[:3] == [("connect", 60.0), ("write", 55.0), ("write", 45.0)]
        assert ("read", 25.0) in timeouts
        assert ftime.sleeps == []

    def test_real_stack_refuses_phase_after_deadline(
        self, ftime: FakeTime, coord: WooCoordination
    ) -> None:
        log: list[tuple[str, float | None]] = []
        client = WooCommerceClient(
            store_config(),
            coordination=coord,
            transport=self._real_transport(ftime, 40.0, log),
            sleep=ftime.sleep,
        )
        with client, pytest.raises(ServiceError) as exc:
            client._request("PUT", "products/42", payload={"a": 1})
        assert exc.value.code == WC_HTTP_ERROR
        assert "call deadline exceeded" in exc.value.message
        assert not any(kind == "read" for kind, _ in log)  # response phase never started
        assert ftime.sleeps == [2.0, 4.0, 8.0]

    def test_slow_body_past_deadline_is_abandoned_and_retried(
        self, ftime: FakeTime, make_client: Callable[..., WooCommerceClient]
    ) -> None:
        fake = flatsome_store()

        def slow(request: httpx2.Request) -> httpx2.Response:
            def body() -> Iterator[bytes]:
                ftime.advance(61.0)
                yield b"{}"

            return httpx2.Response(200, content=body())

        fake.add_fault("PUT", slow)
        res = update(make_client(fake))
        assert res.outcome is WriteOutcome.WRITTEN
        assert len(fake.puts()) == 2
        assert ftime.sleeps == [2.0]

    def test_deadline_exhausted_on_every_attempt_fails(
        self, ftime: FakeTime, make_client: Callable[..., WooCommerceClient]
    ) -> None:
        fake = flatsome_store()

        def slow(request: httpx2.Request) -> httpx2.Response:
            def body() -> Iterator[bytes]:
                ftime.advance(61.0)
                yield b"{}"

            return httpx2.Response(200, content=body())

        fake.add_fault("PUT", *[slow] * 4)
        res = update(make_client(fake))
        assert res.error_code == WC_HTTP_ERROR
        assert "call deadline exceeded" in (res.error_message or "")


# --------------------------------------------------------------------------- rate gate


class TestRateGate:
    def test_reserve_spaces_requests(self, ftime: FakeTime) -> None:
        gate = RateGate(monotonic=ftime.monotonic)
        assert gate.reserve(0.15) == 0.0
        assert gate.reserve(0.15) == pytest.approx(0.15)
        assert gate.reserve(0.15) == pytest.approx(0.30)
        ftime.advance(1.0)
        assert gate.reserve(0.15) == 0.0

    def test_clients_on_one_host_share_the_gate(
        self, ftime: FakeTime, make_client: Callable[..., WooCommerceClient]
    ) -> None:
        fake = flatsome_store()
        one = make_client(fake, store_config("s1", "https://shop.example.com", delay=0.15))
        two = make_client(fake, store_config("s2", "https://shop.example.com/", delay=0.15))
        other = make_client(fake, store_config("s3", "https://other.example.com", delay=0.15))
        one.get_product(42)
        two.get_product(42)
        other.get_product(42)
        assert ftime.sleeps == [pytest.approx(0.15)]

    def test_backoff_schedule(self) -> None:
        assert [backoff_seconds(n) for n in (1, 2, 3)] == [2.0, 4.0, 8.0]


# --------------------------------------------------------------------------- store config and auth


class _Secret:
    def __init__(self, value: str) -> None:
        self._value = value

    def get_secret_value(self) -> str:
        return self._value


class TestStoreConfig:
    def test_derived_values_and_masked_repr(self) -> None:
        cfg = StoreConfig("gplfamily", "https://www.Example.com/", "ck_live", "cs_live")
        assert cfg.api_base == "https://www.Example.com/wp-json/wc/v3"
        assert cfg.host == "www.example.com"
        assert cfg.display_name == "gplfamily"
        assert "ck_live" not in repr(cfg)
        assert "cs_live" not in repr(cfg)

    @pytest.mark.parametrize(
        ("store_id", "url", "delay"),
        [
            ("s", "http://example.com", 0.15),
            ("s", "https://", 0.15),
            ("", "https://example.com", 0.15),
            ("s", "https://example.com", -1.0),
        ],
    )
    def test_invalid_config_is_rejected(self, store_id: str, url: str, delay: float) -> None:
        with pytest.raises(ValueError):
            StoreConfig(store_id, url, "ck", "cs", request_delay=delay)

    def test_secret_sources(self) -> None:
        cfg = StoreConfig("s", "https://example.com", lambda: "ck", _Secret("cs"))
        assert cfg.credentials() == ("ck", "cs")
        cfg = StoreConfig("s", "https://example.com", lambda: _Secret("k"), 123)  # type: ignore[arg-type]
        with pytest.raises(ServiceError) as exc:
            cfg.credentials()
        assert exc.value.code == WC_AUTH_FAILED

    def test_basic_auth_sent_and_resolved_per_request(
        self, make_client: Callable[..., WooCommerceClient]
    ) -> None:
        secret = {"value": "cs1"}
        fake = flatsome_store()
        client = make_client(fake, store_config(secret=lambda: secret["value"]))
        client.get_product(42)
        secret["value"] = "cs2"
        client.get_product(42)
        sent = [s.headers["Authorization"] for s in fake.seen]
        assert sent == [
            "Basic " + base64.b64encode(b"ck:cs1").decode(),
            "Basic " + base64.b64encode(b"ck:cs2").decode(),
        ]

    def test_missing_credentials_fail_without_request_or_breaker_hit(
        self, make_client: Callable[..., WooCommerceClient]
    ) -> None:
        fake = flatsome_store()
        client = make_client(fake, store_config(key=""))
        res = update(client)
        assert res.error_code == WC_AUTH_FAILED
        assert fake.seen == []
        assert client.breaker.consecutive_failures == 0


# --------------------------------------------------------------------------- catalog


class TestCatalog:
    def test_progress_reports_page_and_total(
        self, make_client: Callable[..., WooCommerceClient]
    ) -> None:
        listing = [{"id": i} for i in range(PER_PAGE * 2 + 5)]
        fake = FakeWooStore(listing=listing)
        seen: list[CatalogProgress] = []
        prods = make_client(fake).fetch_catalog_pages(seen.append)
        assert len(prods) == len(listing)
        assert [(p.page, p.total_pages, p.products) for p in seen] == [
            (1, 3, 100),
            (2, 3, 200),
            (3, 3, 205),
        ]
        params = fake.calls("GET")[0].params
        assert params == {
            "downloadable": "true",
            "per_page": "100",
            "page": "1",
            "_fields": "id,slug,name,downloadable_files,downloads,meta_data",
        }

    def test_empty_store(self, make_client: Callable[..., WooCommerceClient]) -> None:
        seen: list[CatalogProgress] = []
        assert make_client(FakeWooStore(listing=[])).fetch_catalog_pages(seen.append) == []
        assert seen == []

    def test_exact_multiple_without_header_stops_on_empty_page(
        self, make_client: Callable[..., WooCommerceClient]
    ) -> None:
        fake = FakeWooStore(listing=[{"id": i} for i in range(PER_PAGE)], total_pages_header="x")
        seen: list[CatalogProgress] = []
        assert len(make_client(fake).fetch_catalog_pages(seen.append)) == PER_PAGE
        assert len(fake.calls("GET")) == 2
        assert seen[0].total_pages is None
        assert seen[0].warning is not None
        assert "non-numeric" in seen[0].warning

    def test_page_cap_aborts(self, make_client: Callable[..., WooCommerceClient]) -> None:
        full = [{"id": i} for i in range(PER_PAGE)]
        fake = FakeWooStore(total_pages_header=None)
        fake.add_fault("GET", *[lambda _r: httpx2.Response(200, json=full)] * (MAX_PAGES + 5))
        with pytest.raises(ServiceError) as exc:
            make_client(fake).fetch_catalog_pages()
        assert exc.value.code == WC_PAGE_LIMIT
        assert len(fake.calls("GET")) == MAX_PAGES

    @pytest.mark.parametrize(
        "response",
        [httpx2.Response(200, json={"a": 1}), httpx2.Response(500), httpx2.Response(404)],
    )
    def test_bad_pages(
        self, response: httpx2.Response, make_client: Callable[..., WooCommerceClient]
    ) -> None:
        fake = FakeWooStore()
        fake.add_fault("GET", lambda _r: response, *[lambda _r: response] * 4)
        with pytest.raises(ServiceError) as exc:
            make_client(fake).fetch_catalog_pages()
        assert exc.value.code in (WC_INVALID_RESPONSE, WC_HTTP_ERROR)

    def test_non_dict_items_are_dropped(
        self, make_client: Callable[..., WooCommerceClient]
    ) -> None:
        fake = FakeWooStore(listing=[{"id": 1}, "junk", 3])  # type: ignore[list-item]
        assert make_client(fake).fetch_catalog_pages() == [{"id": 1}]

    def test_refresh_catalog_builds_entries(self, coord: WooCoordination, ftime: FakeTime) -> None:
        fake = FakeWooStore(
            listing=[
                {
                    "id": 7,
                    "slug": "flatsome",
                    "name": "Flatsome",
                    "downloadable_files": [entry("Download", "flatsome-3.20.9.zip")],
                    "meta_data": [],
                }
            ]
        )
        build = wc.refresh_catalog(
            store_config(),
            "productrepo",
            coordination=coord,
            transport=fake.transport(),
            sleep=ftime.sleep,
        )
        assert build.product_count == 1
        assert build.entries["flatsome"].product_id == 7
        assert build.entries["flatsome"].is_primary

    def test_module_level_fetch(self, coord: WooCoordination) -> None:
        fake = FakeWooStore(listing=[{"id": 1}])
        got = wc.fetch_catalog_pages(store_config(), coordination=coord, transport=fake.transport())
        assert got == [{"id": 1}]
