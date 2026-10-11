"""In-memory WooCommerce fake behind an ``httpx2.MockTransport`` (09-TESTING-STRATEGY section 3).

``respx`` patches ``httpx`` only and does not intercept ``httpx2`` (ADR-0014), so the fake is a
plain ``MockTransport`` handler. It implements ``GET``/``PUT products/{id}`` and the paginated
``GET products`` listing with ``X-WP-TotalPages``, plus configurable faults: scripted statuses or
exceptions per method, ignored writes and ``date_modified`` rejection. Every request is recorded.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx2

Fault = int | BaseException | Callable[[httpx2.Request], httpx2.Response]


@dataclass
class Seen:
    method: str
    path: str
    params: dict[str, str]
    body: Any
    headers: httpx2.Headers
    extensions: dict[str, Any]


@dataclass
class FakeWooStore:
    products: dict[int, dict[str, Any]] = field(default_factory=dict)
    faults: dict[str, list[Fault]] = field(default_factory=dict)
    ignore_writes: bool = False
    reject_date_modified: bool = False
    total_pages_header: str | None = "auto"
    listing: list[dict[str, Any]] | None = None
    per_page: int = 100
    seen: list[Seen] = field(default_factory=list)
    on_request: Callable[[httpx2.Request], None] | None = None

    # -- helpers

    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self.handle)

    def add_fault(self, method: str, *faults: Fault) -> None:
        self.faults.setdefault(method, []).extend(faults)

    def calls(self, method: str | None = None) -> list[Seen]:
        return [s for s in self.seen if method is None or s.method == method]

    def puts(self) -> list[Seen]:
        return self.calls("PUT")

    # -- handler

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        body: Any = None
        raw = request.read()
        if raw:
            body = json.loads(raw)
        self.seen.append(
            Seen(
                request.method,
                request.url.path,
                dict(request.url.params),
                body,
                request.headers,
                dict(request.extensions),
            )
        )
        if self.on_request is not None:
            self.on_request(request)
        queue = self.faults.get(request.method)
        if queue:
            fault = queue.pop(0)
            if isinstance(fault, BaseException):
                raise fault
            if isinstance(fault, int):
                return httpx2.Response(fault, text=f"fault {fault}")
            return fault(request)
        path = request.url.path.removeprefix("/wp-json/wc/v3/")
        if path == "products" and request.method == "GET":
            return self._list(request)
        if path.startswith("products/"):
            pid = int(path.split("/", 1)[1])
            if pid not in self.products:
                return httpx2.Response(404, json={"code": "woocommerce_rest_product_invalid_id"})
            if request.method == "GET":
                return httpx2.Response(200, json=self.products[pid])
            if request.method == "PUT":
                return self._put(pid, body)
        return httpx2.Response(405)

    def _list(self, request: httpx2.Request) -> httpx2.Response:
        items = self.listing if self.listing is not None else list(self.products.values())
        page = int(request.url.params.get("page", "1"))
        start = (page - 1) * self.per_page
        batch = items[start : start + self.per_page]
        headers: dict[str, str] = {}
        if self.total_pages_header == "auto":
            headers["X-WP-TotalPages"] = str(max(1, -(-len(items) // self.per_page)))
        elif self.total_pages_header is not None:
            headers["X-WP-TotalPages"] = self.total_pages_header
        return httpx2.Response(200, json=batch, headers=headers)

    def _put(self, pid: int, body: dict[str, Any]) -> httpx2.Response:
        if self.reject_date_modified and "date_modified" in body:
            return httpx2.Response(
                400,
                json={
                    "code": "rest_invalid_param",
                    "message": "Invalid parameter(s): date_modified",
                    "data": {"status": 400, "params": {"date_modified": "Invalid date."}},
                },
            )
        if self.ignore_writes:
            return httpx2.Response(200, json=self.products[pid])
        product = copy.deepcopy(self.products[pid])
        for key in ("downloadable_files", "downloads", "meta_data"):
            if key in body:
                product[key] = body[key]
        self.products[pid] = product
        return httpx2.Response(200, json=product)


def entry(name: str, obj: str, eid: str = "abc") -> dict[str, Any]:
    """A download entry pointing at ``obj`` via the S3 shortcode (legacy tests/test_wc.py::_entry)."""
    return {"id": eid, "name": name, "file": f'[amazon_s3 object="{obj}"]'}


def product(
    pid: int = 42,
    downloads: list[dict[str, Any]] | None = None,
    metas: list[dict[str, Any]] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """A product payload (legacy tests/test_wc.py::_payload)."""
    return {
        "id": pid,
        "name": "Elementor Pro",
        "downloadable_files": downloads or [],
        "meta_data": metas or [],
        **extra,
    }
