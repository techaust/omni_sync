"""Match an archive's installable candidates to the store catalogs (FR-MATCH-03, FR-MATCH-04).

Cascade (first hit wins), recorded as :class:`MatchMethod`:

1. ``direct`` - a candidate's detected slug (or its lower-case form) is a mapping key;
2. ``alias`` - the slug's alias (``aliases[slug]``) is a mapping key;
3. ``override`` - an exact filename override names a key present on **any** store;
4. ``generic_filename`` - the version-free filename slug is a key on **every** store.

Steps 1-2 are :func:`choose_candidate` (``legacy:omnisync/engine.py:141-143,257-310``); steps 3-4
are the product-URL-slug fallback (``legacy:omnisync/engine.py:149-195,420-450``). Legacy
hard-coded two stores (GPLFamily, PluginWay). Here ``catalogs`` is keyed by the ids of the
**enabled** stores, primary first (ADR-0010); the tier-4 proof is "on every enabled store".

Candidates come from ``omnisync.core.archive``; only the attributes of :class:`CandidateLike` are
used. Catalog entries are opaque (any value type ``E``), so legacy mapping dicts work as well as
:class:`omnisync.core.catalog.CatalogEntry`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from omnisync.core.catalog import mapping_key_from_base
from omnisync.core.shortcodes import strip_zip
from omnisync.core.slugs import slugify
from omnisync.core.versions import version_key as legacy_version_key

__all__ = [
    "CandidateChoice",
    "CandidateLike",
    "MatchMethod",
    "MatchResult",
    "StoreMatch",
    "choose_candidate",
    "fallback_key_for_file",
    "lookup_override",
    "match_archive",
    "normalize_filename_overrides",
    "resolve_alias",
    "version_source_index",
]


class CandidateLike(Protocol):
    """What matching needs from an installable candidate (``core.archive``).

    Required read-only attributes: ``slug`` (``None``/empty = no slug detected), ``version`` and
    ``has_header`` (a WordPress header was found). Legacy ``InstallCandidate`` satisfies it.
    """

    @property
    def slug(self) -> str | None: ...

    @property
    def version(self) -> str | None: ...

    @property
    def has_header(self) -> bool: ...


VersionKey = Callable[[str | None], Any]
"""Sort key for version strings; default :func:`omnisync.core.versions.version_key` (legacy)."""


class MatchMethod(StrEnum):
    """``inspections.match_method`` / ``run_items.match_method`` (04-DATA-MODEL)."""

    DIRECT = "direct"
    ALIAS = "alias"
    OVERRIDE = "override"
    GENERIC_FILENAME = "generic_filename"


@dataclass(frozen=True, slots=True)
class StoreMatch[E]:
    """The catalog entry one store matched, and through which key/method."""

    store_id: str
    mapping_key: str
    entry: E
    method: MatchMethod


def resolve_alias(slug: str, aliases: Mapping[str, str]) -> str:
    """Map a detected slug to a mapping key via the alias rules. legacy:omnisync/engine.py:141-143."""
    return aliases.get(slug, slug)


# --------------------------------------------------------------------------- choose_candidate


@dataclass(frozen=True, slots=True)
class CandidateChoice[C: CandidateLike, E]:
    """Result of :func:`choose_candidate`.

    ``matches`` maps store id -> :class:`StoreMatch` in catalog (store) order. ``full`` is true when
    every store matched. ``ambiguous`` mirrors legacy's warning: no full match and several
    partially matching candidates.
    """

    index: int
    candidate: C
    matches: dict[str, StoreMatch[E]]
    full: bool
    ambiguous: bool

    @property
    def method(self) -> MatchMethod:
        """``alias`` when any store matched through the alias, else ``direct``."""
        if any(m.method is MatchMethod.ALIAS for m in self.matches.values()):
            return MatchMethod.ALIAS
        return MatchMethod.DIRECT

    @property
    def mapping_key(self) -> str:
        """Key matched on the first matching store (primary first)."""
        return next(iter(self.matches.values())).mapping_key


def _candidate_keys(slug: str, aliases: Mapping[str, str]) -> list[tuple[str, MatchMethod]]:
    keys = [(slug, MatchMethod.DIRECT)]
    resolved = resolve_alias(slug, aliases)
    if resolved != slug:
        keys.insert(0, (resolved, MatchMethod.ALIAS))
    lowered = slug.lower()
    if lowered not in (k for k, _ in keys):
        keys.append((lowered, MatchMethod.DIRECT))
    return keys


def choose_candidate[C: CandidateLike, E](
    candidates: Sequence[C],
    catalogs: Mapping[str, Mapping[str, E]],
    aliases: Mapping[str, str],
    filename: str,
    *,
    version_key: VersionKey = legacy_version_key,
) -> CandidateChoice[C, E] | None:
    """Pick the candidate that matches the store catalogs, or ``None`` if none matches any store.

    Per candidate and store, keys are tried in the order alias, slug, lower-case slug. Among
    candidates that matched **every** store (else among all that matched any), the highest of
    ``(len(slug) if slug is a substring of slugify(filename) else -1, has_header, version)`` wins;
    the first candidate wins ties. legacy:omnisync/engine.py:257-310.

    Legacy returned ``(candidate, {store: {"key": k, "entry": e}})`` or ``(None, {})``; here the
    same data is ``choice.candidate`` and ``choice.matches[store].mapping_key/.entry``.
    """
    base = slugify(strip_zip(filename))
    matches: dict[int, dict[str, StoreMatch[E]]] = {}
    for index, cand in enumerate(candidates):
        slug = cand.slug
        if not slug:
            continue
        keys = _candidate_keys(slug, aliases)
        per_store: dict[str, StoreMatch[E]] = {}
        for store_id, catalog in catalogs.items():
            for key, method in keys:
                entry = catalog.get(key)
                if entry is not None:
                    per_store[store_id] = StoreMatch(store_id, key, entry, method)
                    break
        if per_store:
            matches[index] = per_store
    if not matches:
        return None

    full = [i for i in matches if len(matches[i]) == len(catalogs)]
    pool = full or sorted(matches)

    def priority(index: int) -> tuple[int, int, Any]:
        cand = candidates[index]
        slug_len = len(cand.slug) if cand.slug and cand.slug in base else -1
        return (slug_len, 1 if cand.has_header else 0, version_key(cand.version))

    chosen = max(pool, key=priority)
    return CandidateChoice(
        index=chosen,
        candidate=candidates[chosen],
        matches=matches[chosen],
        full=bool(full),
        ambiguous=not full and len(pool) > 1,
    )


# --------------------------------------------------------------------------- fallback tiers


def normalize_filename_overrides(raw: Mapping[str, object]) -> dict[str, str]:
    """Clean a filename-override document: ``.zip`` keys with non-blank string values.

    Keys starting with ``_`` (comments) are dropped. Each override is stored under its exact key
    and its lower-case key. legacy:omnisync/engine.py:149-161 (without the file read).
    """
    overrides: dict[str, str] = {}
    for key, value in raw.items():
        if key.startswith("_") or not isinstance(value, str):
            continue
        if key.lower().endswith(".zip") and value.strip():
            overrides[key] = value.strip()
            overrides[key.lower()] = value.strip()
    return overrides


def lookup_override(filename: str, overrides: Mapping[str, str]) -> str | None:
    """Return the override for ``filename`` (exact, then lower-case), or ``None``."""
    for variant in (filename, filename.lower()):
        if variant in overrides:
            return overrides[variant]
    return None


def fallback_key_for_file(
    filename: str,
    catalogs: Mapping[str, Mapping[str, object]],
    overrides: Mapping[str, str],
) -> tuple[str, MatchMethod] | None:
    """Tier 3/4 fallback: an override key present on any store, else the generic filename key
    present on every store. Returns ``(mapping_key, method)`` or ``None``.

    ``overrides`` must already be normalised (:func:`normalize_filename_overrides`).
    legacy:omnisync/engine.py:164-195 (tier 2 "both stores" becomes "every enabled store").
    With no stores at all nothing matches (legacy's ``all([])`` accepted any key, but then found no
    entries, so the archive stayed unmapped either way).
    The returned method names the tier that produced the key. Legacy's audit label instead said
    "exact-file" whenever the filename had an override, even when that override's key was on no
    store and tier 2 matched (legacy:omnisync/engine.py:427-431); the key itself is identical.
    """
    for variant in (filename, filename.lower()):
        if variant in overrides:
            key = overrides[variant]
            if any(key in catalog for catalog in catalogs.values()):
                return key, MatchMethod.OVERRIDE
    key = mapping_key_from_base(strip_zip(filename))
    if not key or not catalogs:
        return None
    if all(key in catalog for catalog in catalogs.values()):
        return key, MatchMethod.GENERIC_FILENAME
    return None


def version_source_index(
    candidates: Sequence[CandidateLike], *, version_key: VersionKey = legacy_version_key
) -> int:
    """Index of the candidate whose version a fallback match publishes.

    Highest ``(has_header, version)``; the first wins ties. legacy:omnisync/engine.py:434-437.
    ``candidates`` must not be empty.
    """
    return max(
        range(len(candidates)),
        key=lambda i: (1 if candidates[i].has_header else 0, version_key(candidates[i].version)),
    )


# --------------------------------------------------------------------------- cascade


@dataclass(frozen=True, slots=True)
class MatchResult[E]:
    """Outcome of the full cascade for one archive.

    ``method`` ``None`` means unmapped (``matches`` empty). ``candidate_index`` is the chosen
    candidate (direct/alias) or the version source (override/generic). ``slug`` is the detected
    slug for direct/alias and the fallback mapping key for override/generic (legacy built a
    synthetic candidate with ``slug=fallback_key``). ``missing_stores`` lists stores without a
    match, in store order; a non-empty value blocks completion (legacy "partial store match").
    """

    method: MatchMethod | None
    candidate_index: int | None
    slug: str | None
    version: str | None
    matches: dict[str, StoreMatch[E]]
    missing_stores: tuple[str, ...]
    ambiguous: bool = False

    @property
    def matched(self) -> bool:
        return bool(self.matches)

    @property
    def full(self) -> bool:
        return self.matched and not self.missing_stores

    @property
    def mapping_key(self) -> str | None:
        """Key matched on the first matching store (primary first), ``None`` if unmapped."""
        return next(iter(self.matches.values())).mapping_key if self.matches else None


def match_archive[E](
    candidates: Sequence[CandidateLike],
    catalogs: Mapping[str, Mapping[str, E]],
    aliases: Mapping[str, str],
    overrides: Mapping[str, str],
    filename: str,
    *,
    version_key: VersionKey = legacy_version_key,
) -> MatchResult[E]:
    """Run the FR-MATCH-03 cascade for one archive.

    ``catalogs``: enabled stores only, primary first. ``overrides``: normalised filename overrides.
    A partial direct/alias match (not every store) is returned as such and does **not** fall back,
    as in legacy. The fallback downgrade hold (legacy:omnisync/engine.py:451-472) needs slug state
    and lives in the service layer.
    """
    stores = tuple(catalogs)
    choice = choose_candidate(candidates, catalogs, aliases, filename, version_key=version_key)
    if choice is not None:
        return MatchResult(
            method=choice.method,
            candidate_index=choice.index,
            slug=choice.candidate.slug,
            version=choice.candidate.version,
            matches=choice.matches,
            missing_stores=tuple(s for s in stores if s not in choice.matches),
            ambiguous=choice.ambiguous,
        )
    unmapped: MatchResult[E] = MatchResult(None, None, None, None, {}, stores)
    if not candidates:
        return unmapped
    fallback = fallback_key_for_file(filename, catalogs, overrides)
    if fallback is None:
        return unmapped
    key, method = fallback
    matches: dict[str, StoreMatch[E]] = {}
    for store_id, catalog in catalogs.items():
        entry = catalog.get(key)
        if entry is not None:
            matches[store_id] = StoreMatch(store_id, key, entry, method)
    src = version_source_index(candidates, version_key=version_key)
    return MatchResult(
        method=method,
        candidate_index=src,
        slug=key,
        version=candidates[src].version,
        matches=matches,
        missing_stores=tuple(s for s in stores if s not in matches),
    )
