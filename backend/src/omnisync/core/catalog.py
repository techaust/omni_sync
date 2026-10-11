"""Per-store catalog: build, normalise and heal mapping keys from WooCommerce products (FR-CAT-01).

Pure port of ``legacy:omnisync/mappings.py`` (the working tree including the uncommitted
"mapping-healing" change). HTTP fetching stays in the services layer (Milestone 4b): a service
feeds already-fetched product dicts into :func:`build_catalog` and drives paging with
:func:`next_page`. Conflict reporting (FR-CAT-03) is :func:`detect_conflicts`.

A *mapping key* is the version-free slug of a download object's base name (``flatsome-3.20.9.zip``
-> ``flatsome``). Field names follow ``catalog_entries`` in 04-DATA-MODEL section 2.2.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping, Sequence, Set
from dataclasses import dataclass, field
from enum import StrEnum

from omnisync.core.shortcodes import file_source, parse_download_source, strip_zip
from omnisync.core.slugs import slugify

__all__ = [
    "MAX_PAGES",
    "PER_PAGE",
    "CatalogBuild",
    "CatalogConflicts",
    "CatalogEntry",
    "DuplicateKey",
    "MissingKey",
    "ObjectReference",
    "PageAction",
    "PageCursor",
    "PageStep",
    "TotalPages",
    "build_catalog",
    "detect_conflicts",
    "find_missing_keys",
    "find_object_reference",
    "heal_mapping_key",
    "key_matches_product",
    "mapping_key_from_base",
    "next_page",
    "normalize_catalog",
    "parse_total_pages",
    "product_download_entries",
    "product_known_versions",
    "slug_to_key",
    "strip_known_version_suffix",
    "title_to_key",
]

PER_PAGE = 100
"""Products per WooCommerce listing page (legacy:omnisync/mappings.py:31)."""
MAX_PAGES = 1000
"""Pagination safety cap (legacy:omnisync/mappings.py:34)."""

_MAPPING_VERSION_RE = re.compile(r"(\d+\.\d+(?:\.\d+){0,8})")
_TITLE_SPLIT_RE = re.compile("\\s*[-\u2013\u2014|,\u00b7]\\s*")


# --------------------------------------------------------------------------- keys and healing


def mapping_key_from_base(base: str) -> str:
    """Return the mapping key of an object base: drop ``.zip``, dotted versions, then slugify.

    Bare-integer versions survive (``konstruktion-37`` stays), see
    :func:`strip_known_version_suffix`. legacy:omnisync/mappings.py:47-50.
    """
    name = _MAPPING_VERSION_RE.sub("-", strip_zip(base))
    return slugify(name) or ""


def slug_to_key(slug: str) -> str | None:
    """Mapping key derived from a product slug. legacy:omnisync/mappings.py:389-390."""
    return slugify(slug) or None


def title_to_key(title: str) -> str | None:
    """Mapping key from the part of a product title before the first separator.

    Separators are ``-``, en/em dash, ``|``, ``,`` and middle dot.
    legacy:omnisync/mappings.py:393-397.
    """
    if not title:
        return None
    first = _TITLE_SPLIT_RE.split(title.strip(), maxsplit=1)[0]
    return slugify(first) or None


def product_known_versions(product: Mapping[str, object]) -> frozenset[str]:
    """Collect version strings from the product's ``latest_version*`` meta.

    The pipeline writes ``latest_version`` plus per-link ``latest_version_<slug>`` on every update,
    so these values are the product's authoritative version history. Keys starting with ``_`` are
    ignored. legacy:omnisync/mappings.py:53-77.
    """
    versions: set[str] = set()
    raw = product.get("meta_data")
    if not isinstance(raw, list):
        return frozenset()
    for md in raw:
        if not isinstance(md, Mapping):
            continue
        k = md.get("key")
        v = md.get("value")
        if not isinstance(k, str) or k.startswith("_"):
            continue
        if k != "latest_version" and not k.startswith("latest_version_"):
            continue
        if isinstance(v, int | float):
            versions.add(str(v))
        elif isinstance(v, str) and v.strip():
            versions.add(v.strip())
    return frozenset(versions)


def strip_known_version_suffix(key: str, versions: Set[str]) -> str:
    """Strip trailing ``-<version>`` segments that exactly match a known version.

    A ``v`` prefix is allowed (``-v39``). Anything else stops the stripping, so legitimate numeric
    slugs such as ``realestate-7`` survive unless ``7`` is a known version (the caller's guard
    :func:`key_matches_product` then rejects unrelated results). legacy:omnisync/mappings.py:80-100.
    """
    lowered = {v.lower() for v in versions}
    while "-" in key:
        head, _, tail = key.rpartition("-")
        if not head:
            break
        t = tail.strip().lower()
        if t in lowered or (t.startswith("v") and t[1:] in lowered):
            key = head
        else:
            break
    return key


def key_matches_product(key: str, slug: str, title: str) -> bool:
    """Healing guard: ``key`` must equal the product slug/title key or dash-extend it either way.

    ``konstruktion`` vs ``konstruktion-construction-...`` and ``flatsome-child`` vs ``flatsome``
    pass; a strip that lands on an unrelated stem fails and the unhealed key is kept.
    legacy:omnisync/mappings.py:103-116.
    """
    for ref in (slug_to_key(slug), title_to_key(title)):
        if not ref:
            continue
        if key == ref or ref.startswith(key + "-") or key.startswith(ref + "-"):
            return True
    return False


def heal_mapping_key(key: str, versions: Set[str], slug: str, title: str) -> str:
    """Return ``key`` without fossil version suffixes, if the guard accepts the result.

    legacy:omnisync/mappings.py:327-330.
    """
    if not key or not versions:
        return key
    healed = strip_known_version_suffix(key, versions)
    if healed and healed != key and key_matches_product(healed, slug, title):
        return healed
    return key


# --------------------------------------------------------------------------- catalog building


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    """One catalog row: a mapping key of one store pointing at a product download.

    ``base`` is the object base without ``.zip``. ``product_slug``/``product_title`` are ``None``
    for entries normalised from a legacy ``mapping.json`` (it does not carry them).
    """

    mapping_key: str
    product_id: int
    base: str
    download_name: str
    is_primary: bool
    product_slug: str | None = None
    product_title: str | None = None


@dataclass(frozen=True, slots=True)
class DuplicateKey:
    """A mapping key claimed by several products of one store (FR-CAT-03).

    ``product_ids`` lists every claimant in input order; the first one is kept (legacy rule).
    """

    mapping_key: str
    product_ids: tuple[int, ...]

    @property
    def kept_product_id(self) -> int:
        return self.product_ids[0]


@dataclass(frozen=True, slots=True)
class CatalogBuild:
    """Result of :func:`build_catalog`. ``entries`` preserves insertion order."""

    entries: dict[str, CatalogEntry]
    duplicates: tuple[DuplicateKey, ...] = ()
    product_count: int = 0


def _product_id(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def product_download_entries(product: Mapping[str, object]) -> Iterator[object]:
    """Yield the raw download entries of a product (``downloadable_files`` then ``downloads``).

    Lists are yielded as-is; dict-shaped fields yield their dict/str values.
    legacy:omnisync/mappings.py:171-179.
    """
    for key in ("downloadable_files", "downloads"):
        raw = product.get(key) or []
        if isinstance(raw, list):
            yield from raw
        elif isinstance(raw, Mapping):
            for v in raw.values():
                if isinstance(v, Mapping | str):
                    yield v


def build_catalog(products: Iterable[Mapping[str, object]], bucket: str | None) -> CatalogBuild:
    """Build one store's catalog from its downloadable products.

    For every download entry the mapping key is derived from the object's file name and healed
    against the product's known versions. A product without parsable downloads falls back to its
    slug and title keys (base = raw slug/title). The primary key of a product is the one equal to
    ``slugify(product slug)``, else its first key. A key already claimed by another product keeps
    the first product and is reported in ``duplicates`` (legacy logged a warning).
    legacy:omnisync/mappings.py:310-356 (``bucket`` was the global ``AWS["bucket"]``).

    Products whose ``id`` is missing or not an integer are skipped.
    """
    entries: dict[str, CatalogEntry] = {}
    primary_keys: dict[int, str] = {}
    claimants: dict[str, list[int]] = {}
    count = 0
    for prod in products:
        pid = _product_id(prod.get("id"))
        if pid is None:
            continue
        count += 1
        slug = prod.get("slug") or ""
        title = prod.get("name") or ""
        slug = slug if isinstance(slug, str) else str(slug)
        title = title if isinstance(title, str) else str(title)
        known_versions = product_known_versions(prod)
        cands: list[tuple[str, str, str]] = []
        for entry in product_download_entries(prod):
            parsed = parse_download_source(file_source(entry), bucket)
            if not parsed:
                continue
            base = strip_zip(parsed.filename)
            key = heal_mapping_key(mapping_key_from_base(base), known_versions, slug, title)
            name = entry.get("name", "") if isinstance(entry, Mapping) else ""
            if key:
                cands.append((key, base, name if isinstance(name, str) else ""))
        if not cands:
            for alt, base in ((slug_to_key(slug), slug), (title_to_key(title), title)):
                if alt:
                    cands.append((alt, base, ""))
        pref = slugify(slug)
        pkey = next((k for k, _, _ in cands if k == pref), cands[0][0] if cands else None)
        if pkey and pid not in primary_keys:
            primary_keys[pid] = pkey
        for key, base, name in cands:
            existing = entries.get(key)
            if existing and existing.product_id != pid:
                ids = claimants.setdefault(key, [existing.product_id])
                if pid not in ids:
                    ids.append(pid)
                continue
            entries[key] = CatalogEntry(
                mapping_key=key,
                product_id=pid,
                base=base,
                download_name=name,
                is_primary=key == primary_keys.get(pid),
                product_slug=slug or None,
                product_title=title or None,
            )
    duplicates = tuple(DuplicateKey(k, tuple(ids)) for k, ids in claimants.items())
    return CatalogBuild(entries=entries, duplicates=duplicates, product_count=count)


def normalize_catalog(raw: Mapping[str, object]) -> dict[str, CatalogEntry]:
    """Normalise a legacy ``mapping.json`` document into catalog entries (legacy import).

    Accepts the current shape (``{key: {product_id, base, name, primary}}``), the older
    ``{key: {product_id, s3_key}}`` and the int shorthand ``{"x.zip": 5}``. Keys are re-derived
    with :func:`mapping_key_from_base`; the first raw key wins a collision. ``is_primary`` is
    recomputed as "first key of its product" (the stored ``primary`` is ignored, as in legacy).
    legacy:omnisync/mappings.py:359-382.

    Entries whose ``product_id`` is missing or not an integer are skipped (legacy kept them with
    whatever value was stored).
    """
    staged: dict[str, tuple[int, str, str]] = {}
    for key, val in raw.items():
        base_raw: object
        name_raw: object
        if isinstance(val, Mapping):
            pid_raw: object = val.get("product_id")
            base_raw = val.get("base") or val.get("s3_key") or key
            name_raw = val.get("name") or ""
        else:
            pid_raw = val
            base_raw = key
            name_raw = ""
        new_key = strip_zip(key)
        new_key = mapping_key_from_base(new_key) or new_key.lower()
        if not new_key or new_key in staged:
            continue
        pid = _product_id(pid_raw)
        if pid is None:
            continue
        base = strip_zip(str(base_raw))
        staged[new_key] = (pid, base, name_raw if isinstance(name_raw, str) else "")
    first: dict[int, str] = {}
    for k, (pid, _, _) in staged.items():
        first.setdefault(pid, k)
    return {
        k: CatalogEntry(
            mapping_key=k,
            product_id=pid,
            base=base,
            download_name=name,
            is_primary=k == first[pid],
        )
        for k, (pid, base, name) in staged.items()
    }


# --------------------------------------------------------------------------- conflicts


@dataclass(frozen=True, slots=True)
class MissingKey:
    """A mapping key present on some enabled stores but missing on others (FR-CAT-03)."""

    mapping_key: str
    present_on: tuple[str, ...]
    missing_on: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CatalogConflicts:
    """All catalog conflicts: duplicates per store and keys missing on some enabled stores."""

    duplicates: dict[str, tuple[DuplicateKey, ...]] = field(default_factory=dict)
    missing: tuple[MissingKey, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.missing and not any(self.duplicates.values())


def find_missing_keys(
    catalogs: Mapping[str, Iterable[str]], enabled_stores: Sequence[str]
) -> tuple[MissingKey, ...]:
    """Keys found on at least one enabled store but not on all of them, sorted by key.

    ``catalogs`` maps a store id to its mapping keys (a catalog dict works). An enabled store
    without a catalog counts as empty. Store order in the result follows ``enabled_stores``.
    New in OmniSync 2.0 (legacy only had the implicit tier-2 dual-store proof,
    legacy:omnisync/engine.py:190-195).
    """
    keysets = {s: frozenset(catalogs.get(s, ())) for s in enabled_stores}
    union: set[str] = set().union(*keysets.values()) if keysets else set()
    result: list[MissingKey] = []
    for key in sorted(union):
        present = tuple(s for s in enabled_stores if key in keysets[s])
        if len(present) < len(enabled_stores):
            missing = tuple(s for s in enabled_stores if key not in keysets[s])
            result.append(MissingKey(key, present, missing))
    return tuple(result)


def detect_conflicts(
    builds: Mapping[str, CatalogBuild], enabled_stores: Sequence[str]
) -> CatalogConflicts:
    """Combine per-store duplicate keys with keys missing on some enabled stores (FR-CAT-03).

    Duplicates are reported for every store in ``builds`` (disabled stores included); missing
    keys only across ``enabled_stores`` (ADR-0010).
    """
    duplicates = {s: b.duplicates for s, b in builds.items() if b.duplicates}
    missing = find_missing_keys({s: b.entries for s, b in builds.items()}, enabled_stores)
    return CatalogConflicts(duplicates=duplicates, missing=missing)


@dataclass(frozen=True, slots=True)
class ObjectReference:
    """A catalog entry that still points at an object base."""

    store_id: str
    mapping_key: str
    product_id: int


def find_object_reference(
    object_key: str,
    catalogs: Mapping[str, Mapping[str, CatalogEntry]],
    skip_products: Set[tuple[str, int]] = frozenset(),
) -> ObjectReference | None:
    """Return the first catalog entry whose base equals ``object_key`` (case-insensitive, no .zip).

    Entries of ``(store_id, product_id)`` pairs in ``skip_products`` (just rewritten, so their
    catalog rows are stale) are ignored. Callers pass every configured store, disabled ones too
    (ADR-0010). legacy:omnisync/engine.py:224-254 (legacy returned a display string).
    """
    target = strip_zip(object_key).lower()
    for store_id, catalog in catalogs.items():
        for key, entry in catalog.items():
            if (store_id, entry.product_id) in skip_products:
                continue
            if strip_zip(entry.base).lower() == target:
                return ObjectReference(store_id, key, entry.product_id)
    return None


# --------------------------------------------------------------------------- pagination


@dataclass(frozen=True, slots=True)
class TotalPages:
    """Interpretation of an ``X-WP-TotalPages`` header. ``value`` ``None`` = unknown."""

    value: int | None
    warning: str | None = None


def parse_total_pages(header: str | None, max_pages: int = MAX_PAGES) -> TotalPages:
    """Interpret ``X-WP-TotalPages``: a number (capped at ``max_pages``) or unknown.

    Missing or non-numeric headers mean "fetch until a short page".
    legacy:omnisync/mappings.py:270-292.
    """
    if not header:
        return TotalPages(
            None,
            "X-WP-TotalPages header missing; fetching pages until a short page is returned.",
        )
    try:
        total = int(header)
    except ValueError:
        return TotalPages(
            None,
            "non-numeric X-WP-TotalPages header; fetching pages until a short page is returned.",
        )
    if total > max_pages:
        return TotalPages(
            max_pages,
            f"X-WP-TotalPages header ({total}) exceeds safety limit ({max_pages}); capping.",
        )
    return TotalPages(total)


class PageAction(StrEnum):
    FETCH = "fetch"
    STOP = "stop"
    ABORT = "abort"


@dataclass(frozen=True, slots=True)
class PageCursor:
    """Paging position. ``page`` is the next page to fetch; ``total_pages`` is known after the
    first non-empty page (``None`` = unknown, fetch until a short page)."""

    page: int = 1
    total_pages: int | None = None
    started: bool = False


@dataclass(frozen=True, slots=True)
class PageStep:
    """Decision after one page: ``FETCH`` ``cursor.page`` next, ``STOP`` or ``ABORT``.

    ``reason`` is one of ``empty_page``, ``short_page``, ``declared_total``, ``page_cap``, ``next``.
    ``warning`` carries the header warning produced when the first page was seen.
    """

    action: PageAction
    cursor: PageCursor
    reason: str
    warning: str | None = None


def next_page(
    cursor: PageCursor,
    batch_size: int,
    total_pages_header: str | None = None,
    *,
    per_page: int = PER_PAGE,
    max_pages: int = MAX_PAGES,
) -> PageStep:
    """Decide what to do after receiving ``batch_size`` products for ``cursor.page``.

    Service loop: start with ``PageCursor()``, fetch ``cursor.page``, call this with the batch size
    and the response's ``X-WP-TotalPages`` header (read on the first non-empty page only), extend
    the product list, then continue while the action is ``FETCH``. ``ABORT`` means the 1,000-page
    cap was exceeded without a usable header (legacy raised ``MappingFetchError`` code 5).
    legacy:omnisync/mappings.py:203-306.
    """
    if batch_size <= 0:
        return PageStep(PageAction.STOP, cursor, "empty_page")
    warning: str | None = None
    if not cursor.started:
        parsed = parse_total_pages(total_pages_header, max_pages)
        cursor = PageCursor(cursor.page, parsed.value, started=True)
        warning = parsed.warning
    if batch_size < per_page:
        return PageStep(PageAction.STOP, cursor, "short_page", warning)
    following = PageCursor(cursor.page + 1, cursor.total_pages, started=True)
    if cursor.total_pages is None:
        if following.page > max_pages:
            return PageStep(PageAction.ABORT, cursor, "page_cap", warning)
    elif following.page > cursor.total_pages:
        return PageStep(PageAction.STOP, cursor, "declared_total", warning)
    return PageStep(PageAction.FETCH, following, "next", warning)
