"""Shared config document synchronised between instances (FR-COORD-03, FR-RULE-02, C-18).

Protocol: 03-ARCHITECTURE section 6.3, 04-DATA-MODEL sections 3 and 5, 10-OPERATIONS section 8.

Document ``<control prefix>config/shared.json`` (schema 1)::

    {"schema": 1, "version": 7, "updated_at": "...Z", "updated_by": "owner@owner-pc",
     "stores": [{"key": "gplfamily", "label": "GPLFamily",
                 "base_url": "https://www.gplfamily.com", "enabled": true}],
     "aliases": {"detected-slug": "mapping-key"},
     "overrides": {"filename.zip": "mapping-key"},
     "download_names": {"slug": "Download name"},
     "policies": {"downgrade": "hold"},
     "history": [{"at": "...Z", "actor": "owner", "instance": "owner-pc", "source": "manual",
                  "section": "aliases", "key": "detected-slug", "old": null, "new": "x"}]}

Rules are keyed: ``(section, key)``. ``stores`` is a list of objects keyed by ``key``; the other
sections are JSON objects. A change carries the value the user saw (``old``, or :data:`ABSENT`)
and the value they want (``new``, or :data:`ABSENT` to delete). Against a document, a change is

* **applied** when the current value equals ``old``,
* a **no-op** when the current value already equals ``new`` (both instances made the same edit),
* a **conflict** otherwise (the same key was changed differently).

Pull: replace the cached copy when the ETag changes; a document that fails validation is
rejected and the last good copy kept (C-18).

Push (:meth:`SharedConfigService.apply`, all-or-nothing): apply to a copy of the cached document,
increment ``version`` and ``PUT If-Match: <cached ETag>`` (``If-None-Match: *`` when the document
does not exist). On 412 (or a conflict against a possibly stale cache) re-pull, re-apply the same
logical changes and retry once. Key conflicts raise :class:`SharedConfigConflict`
(``config.conflict``, both values) and nothing is written. Because only a successful conditional
PUT proves that the cached base was current, an outcome that writes nothing (all no-ops) is
re-checked against a fresh pull before it is reported.

Legacy import (:meth:`SharedConfigService.merge_missing`, 04-DATA-MODEL section 5): keys absent
from the shared config are added; equal keys are unchanged; a key with a different value is
reported as a conflict and **not** overwritten, while the rest are still merged.

Schema: this build reads and writes schema 1. A document with a newer schema is readable when
its known fields validate, but this instance refuses to push (``config.schema_too_new``) until
it is upgraded (10-OPERATIONS section 8). An older or missing schema is invalid.
"""

from __future__ import annotations

import copy
import enum
import logging
import re
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Literal, TypeGuard, get_args

from omnisync.clock import Clock, SystemClock
from omnisync.services.errors import ServiceError
from omnisync.services.object_store import JsonObjectStore, PreconditionFailed

__all__ = [
    "ABSENT",
    "CONFIG_CONFLICT",
    "CONFIG_INVALID",
    "CONFIG_SCHEMA_TOO_NEW",
    "HISTORY_LIMIT",
    "PULL_INTERVAL_SECONDS",
    "SHARED_CONFIG_OBJECT",
    "SHARED_CONFIG_SCHEMA",
    "Absent",
    "ConfigChange",
    "KeyConflict",
    "MergeReport",
    "PullResult",
    "PushResult",
    "Section",
    "SharedConfigConflict",
    "SharedConfigService",
    "SharedConfigSnapshot",
    "default_document",
    "validate_document",
]

log = logging.getLogger(__name__)

SHARED_CONFIG_SCHEMA: Final = 1
SHARED_CONFIG_OBJECT: Final = "config/shared.json"
HISTORY_LIMIT: Final = 500
PULL_INTERVAL_SECONDS: Final = 60  # FR-COORD-03; scheduling is the runner's job

CONFIG_CONFLICT: Final = "config.conflict"
CONFIG_INVALID: Final = "config.invalid"
CONFIG_SCHEMA_TOO_NEW: Final = "config.schema_too_new"

Section = Literal["stores", "aliases", "overrides", "download_names", "policies"]
SECTIONS: Final[tuple[Section, ...]] = get_args(Section)
MAP_SECTIONS: Final[tuple[Section, ...]] = ("aliases", "overrides", "download_names", "policies")

_STORE_KEY = re.compile(r"[a-z0-9-]{2,32}")
# C-06: secrets never enter the shared config.
_SECRET_FIELDS: Final = frozenset(
    {"consumer_key", "consumer_secret", "password", "secret", "token", "api_key"}
)


class Absent(enum.Enum):
    """Marker for "the key does not exist" in a change or conflict."""

    ABSENT = "absent"

    def __repr__(self) -> str:
        return "ABSENT"


ABSENT: Final = Absent.ABSENT


# --------------------------------------------------------------------------------------------
# Results and errors
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConfigChange:
    """One keyed rule change: ``old`` is what the user saw, ``new`` what they want."""

    section: Section
    key: str
    new: Any
    old: Any = ABSENT


@dataclass(frozen=True, slots=True)
class KeyConflict:
    section: Section
    key: str
    current: Any  # value in the shared config now (ABSENT if missing)
    yours: Any  # value this instance wanted to write (ABSENT = delete)
    base: Any = ABSENT  # value the change was based on

    def as_dict(self) -> dict[str, Any]:
        """JSON form for ``409 config.conflict`` (05-API-SPEC: ``current`` and ``yours``)."""

        def enc(value: Any) -> Any:
            return None if value is ABSENT else value

        return {
            "section": self.section,
            "key": self.key,
            "current": enc(self.current),
            "yours": enc(self.yours),
            "current_exists": self.current is not ABSENT,
            "yours_exists": self.yours is not ABSENT,
        }


class SharedConfigConflict(ServiceError):
    """``config.conflict``: the change cannot be applied without overwriting another edit.

    ``conflicts`` is empty (and ``retryable`` true) when the document kept changing during both
    attempts (high contention) rather than conflicting on a key.
    """

    def __init__(self, message: str, conflicts: Iterable[KeyConflict] = ()) -> None:
        self.conflicts: tuple[KeyConflict, ...] = tuple(conflicts)
        super().__init__(CONFIG_CONFLICT, message, retryable=not self.conflicts)


@dataclass(frozen=True, slots=True)
class SharedConfigSnapshot:
    """A validated copy of the document and its ETag. Treat ``document`` as read-only."""

    document: dict[str, Any]
    etag: str

    @property
    def schema(self) -> int:
        return int(self.document["schema"])

    @property
    def version(self) -> int:
        return int(self.document["version"])

    @property
    def writable(self) -> bool:
        """False when the document's schema is newer than this build supports."""
        return self.schema <= SHARED_CONFIG_SCHEMA

    def section(self, name: Section) -> Any:
        return copy.deepcopy(self.document[name])

    def get(self, section: Section, key: str) -> Any:
        return _get(self.document, section, key)


PullStatus = Literal["updated", "unchanged", "missing", "invalid"]


@dataclass(frozen=True, slots=True)
class PullResult:
    status: PullStatus
    snapshot: SharedConfigSnapshot | None  # the cached (last good) copy after the pull
    error: str | None = None


@dataclass(frozen=True, slots=True)
class PushResult:
    snapshot: SharedConfigSnapshot
    applied: tuple[ConfigChange, ...]
    unchanged: tuple[ConfigChange, ...]
    attempts: int


@dataclass(frozen=True, slots=True)
class MergeReport:
    snapshot: SharedConfigSnapshot | None
    added: tuple[ConfigChange, ...] = ()
    unchanged: tuple[ConfigChange, ...] = ()
    conflicts: tuple[KeyConflict, ...] = field(default_factory=tuple)


# --------------------------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------------------------


def default_document(actor: str = "system", updated_at: str = "") -> dict[str, Any]:
    return {
        "schema": SHARED_CONFIG_SCHEMA,
        "version": 0,
        "updated_at": updated_at,
        "updated_by": actor,
        "stores": [],
        "aliases": {},
        "overrides": {},
        "download_names": {},
        "policies": {"downgrade": "hold"},
        "history": [],
    }


def _is_int(value: Any) -> TypeGuard[int]:
    """An integer that is not a bool."""
    return type(value) is int


def validate_store(value: Any) -> str | None:
    """Return an error message, or ``None`` when ``value`` is a valid store definition."""
    if not isinstance(value, dict):
        return "store must be an object"
    key = value.get("key")
    if not isinstance(key, str) or not _STORE_KEY.fullmatch(key):
        return "store key must match [a-z0-9-]{2,32}"
    if not isinstance(value.get("label"), str) or not value["label"]:
        return f"store {key}: label must be a non-empty string"
    base_url = value.get("base_url")
    if not isinstance(base_url, str) or not base_url.startswith("https://"):
        return f"store {key}: base_url must start with https://"
    if type(value.get("enabled")) is not bool:
        return f"store {key}: enabled must be a boolean"
    if "sort_order" in value and not _is_int(value["sort_order"]):
        return f"store {key}: sort_order must be an integer"
    leaked = _SECRET_FIELDS.intersection(value)
    if leaked:
        return f"store {key}: secrets are not allowed in the shared config ({sorted(leaked)})"
    return None


def _validate_value(section: Section, key: str, value: Any) -> str | None:
    if section == "stores":
        error = validate_store(value)
        if error is None and value["key"] != key:
            error = f"store key {value['key']!r} does not match rule key {key!r}"
        return error
    if section == "policies":
        if value is None or isinstance(value, str | bool | int | float):
            return None
        return f"policy {key}: value must be a JSON scalar"
    if not isinstance(value, str) or not value:
        return f"{section} {key}: value must be a non-empty string"
    return None


def validate_document(data: Any) -> dict[str, Any]:
    """Validate a pulled document and return a normalised deep copy.

    Missing rule sections default to empty. Raises ``ServiceError(config.invalid)``.
    """

    def bad(message: str) -> ServiceError:
        return ServiceError(CONFIG_INVALID, f"shared config rejected: {message}")

    if not isinstance(data, dict):
        raise bad("not a JSON object")
    doc = copy.deepcopy(data)
    schema = doc.get("schema")
    if not _is_int(schema) or schema < SHARED_CONFIG_SCHEMA:
        raise bad(f"unsupported schema {schema!r}")
    version = doc.get("version")
    if not _is_int(version) or version < 0:
        raise bad("version must be a non-negative integer")
    for name in ("updated_at", "updated_by"):
        if not isinstance(doc.get(name), str):
            raise bad(f"{name} must be a string")
    stores = doc.setdefault("stores", [])
    if not isinstance(stores, list):
        raise bad("stores must be a list")
    seen: set[str] = set()
    for store in stores:
        error = validate_store(store)
        if error is not None:
            raise bad(error)
        if store["key"] in seen:
            raise bad(f"duplicate store key {store['key']!r}")
        seen.add(store["key"])
    for section in MAP_SECTIONS:
        values = doc.setdefault(section, {})
        if not isinstance(values, dict):
            raise bad(f"{section} must be an object")
        for key, value in values.items():
            error = _validate_value(section, key, value)
            if error is not None:
                raise bad(error)
    history = doc.setdefault("history", [])
    if not isinstance(history, list) or not all(isinstance(h, dict) for h in history):
        raise bad("history must be a list of objects")
    return doc


# --------------------------------------------------------------------------------------------
# Document helpers
# --------------------------------------------------------------------------------------------


def _get(doc: Mapping[str, Any], section: Section, key: str) -> Any:
    if section == "stores":
        for store in doc["stores"]:
            if store["key"] == key:
                return copy.deepcopy(store)
        return ABSENT
    values = doc[section]
    return copy.deepcopy(values[key]) if key in values else ABSENT


def _set(doc: dict[str, Any], section: Section, key: str, value: Any) -> None:
    if section == "stores":
        stores: list[dict[str, Any]] = doc["stores"]
        if value is ABSENT:
            stores[:] = [s for s in stores if s["key"] != key]
            return
        index = next((i for i, s in enumerate(stores) if s["key"] == key), None)
        if index is None:
            stores.append(copy.deepcopy(value))
        else:
            stores[index] = copy.deepcopy(value)
        return
    if value is ABSENT:
        doc[section].pop(key, None)
    else:
        doc[section][key] = copy.deepcopy(value)


def _enc(value: Any) -> Any:
    return None if value is ABSENT else value


# --------------------------------------------------------------------------------------------
# Service
# --------------------------------------------------------------------------------------------


class SharedConfigService:
    """Pull/push of the shared config for one instance (thread-safe)."""

    def __init__(
        self,
        store: JsonObjectStore,
        *,
        instance_name: str,
        control_prefix: str = "_omnisync/",
        clock: Clock | None = None,
    ) -> None:
        self.store = store
        self.instance_name = instance_name
        self.key = control_prefix + SHARED_CONFIG_OBJECT
        self._clock = clock or SystemClock()
        self._cached: SharedConfigSnapshot | None = None
        self._lock = threading.RLock()

    @property
    def cached(self) -> SharedConfigSnapshot | None:
        return self._cached

    # -- pull ---------------------------------------------------------------------------------

    def pull(self) -> PullResult:
        """Fetch the document; replace the cache when the ETag changed (C-18 validation).

        Store errors propagate (the caller retries at the next interval).
        """
        with self._lock:
            got = self.store.get_json(self.key)
            if got is None:
                return PullResult("missing", self._cached)
            data, etag = got
            if self._cached is not None and self._cached.etag == etag:
                return PullResult("unchanged", self._cached)
            try:
                doc = validate_document(data)
            except ServiceError as exc:
                log.warning("shared config rejected; keeping the last good copy: %s", exc.message)
                return PullResult("invalid", self._cached, exc.message)
            if self._cached is not None and doc["version"] < self._cached.version:
                log.warning("shared config version went backwards (restored from a backup?)")
            self._cached = SharedConfigSnapshot(doc, etag)
            return PullResult("updated", self._cached)

    def bootstrap(self, seed: Mapping[str, Any] | None = None, *, actor: str) -> PullResult:
        """Create the document with ``If-None-Match: *`` when absent (03-ARCHITECTURE 6.3)."""
        with self._lock:
            result = self.pull()
            if result.status != "missing":
                return result
            doc = self._fresh(actor)
            doc.update(copy.deepcopy(dict(seed or {})))
            doc = validate_document(doc)
            doc["schema"] = SHARED_CONFIG_SCHEMA
            doc["updated_at"] = self._timestamp()
            doc["updated_by"] = self._actor(actor)
            try:
                etag = self.store.put_json(self.key, doc, if_none_match=True)
            except PreconditionFailed:  # the other instance bootstrapped first
                return self.pull()
            self._cached = SharedConfigSnapshot(doc, etag)
            return PullResult("updated", self._cached)

    # -- push ---------------------------------------------------------------------------------

    def apply(
        self,
        changes: Iterable[ConfigChange],
        *,
        actor: str,
        source: str = "manual",
        before_write: Callable[[], None] | None = None,
    ) -> PushResult:
        """Apply keyed changes atomically (all or nothing). See the module docstring.

        ``before_write`` runs immediately before every PUT (e.g. ``LeaseHandle.check``).
        """
        wanted = tuple(changes)
        for change in wanted:
            self._validate_change(change)
        with self._lock:
            last_conflicts: list[KeyConflict] = []
            for attempt in (1, 2):
                base = self._base(fresh=attempt == 2)
                doc = self._writable_copy(base)
                applied, unchanged, conflicts = [], [], []
                for change in wanted:
                    current = _get(doc, change.section, change.key)
                    if current == change.new:
                        unchanged.append(change)
                    elif current == change.old:
                        _set(doc, change.section, change.key, change.new)
                        applied.append(change)
                    else:
                        conflicts.append(
                            KeyConflict(change.section, change.key, current, change.new, change.old)
                        )
                if conflicts:
                    last_conflicts = conflicts
                    continue  # attempt 1 may have used a stale cache: re-pull once
                if not applied and base is not None:
                    # Nothing to write. Only a successful conditional PUT proves the base was
                    # current, so confirm a no-op against a fresh copy before reporting it.
                    if attempt == 1:
                        continue
                    return PushResult(base, (), tuple(unchanged), attempt)
                history = [self._history(c, actor, source) for c in applied]
                snapshot = self._write(doc, base, actor, history, before_write)
                if snapshot is not None:
                    return PushResult(snapshot, tuple(applied), tuple(unchanged), attempt)
                last_conflicts = []
            if last_conflicts:
                raise SharedConfigConflict(
                    "the shared config was changed differently on another instance",
                    last_conflicts,
                )
            raise SharedConfigConflict("the shared config kept changing; try again")

    def merge_missing(
        self,
        entries: Iterable[tuple[Section, str, Any]],
        *,
        actor: str,
        source: str = "legacy_import",
        before_write: Callable[[], None] | None = None,
    ) -> MergeReport:
        """Add keys absent from the shared config; report different values as conflicts.

        Used by the legacy import (``push_rules``), which requires the lease: pass
        ``before_write=handle.check``.
        """
        items = tuple(entries)
        for section, key, value in items:
            self._validate_change(ConfigChange(section, key, value))
        with self._lock:
            for attempt in (1, 2):
                base = self._base(fresh=attempt == 2)
                doc = self._writable_copy(base)
                added, unchanged, conflicts = [], [], []
                for section, key, value in items:
                    current = _get(doc, section, key)
                    change = ConfigChange(section, key, value)
                    if current is ABSENT:
                        _set(doc, section, key, value)
                        added.append(change)
                    elif current == value:
                        unchanged.append(change)
                    else:
                        conflicts.append(KeyConflict(section, key, current, value))
                snapshot: SharedConfigSnapshot | None = base
                if not added and base is not None and attempt == 1:
                    continue  # confirm "unchanged"/conflicts against a fresh copy
                if added:
                    history = [self._history(c, actor, source) for c in added]
                    snapshot = self._write(doc, base, actor, history, before_write)
                    if snapshot is None:
                        continue
                return MergeReport(snapshot, tuple(added), tuple(unchanged), tuple(conflicts))
            raise SharedConfigConflict("the shared config kept changing; try again")

    # -- internals ----------------------------------------------------------------------------

    def _validate_change(self, change: ConfigChange) -> None:
        if change.section not in SECTIONS:
            raise ServiceError("validation.failed", f"unknown section {change.section!r}")
        if not isinstance(change.key, str) or not change.key:
            raise ServiceError("validation.failed", "rule key must be a non-empty string")
        if change.new is not ABSENT:
            error = _validate_value(change.section, change.key, change.new)
            if error is not None:
                raise ServiceError("validation.failed", error)

    def _base(self, *, fresh: bool) -> SharedConfigSnapshot | None:
        """Cached copy (pulled first if there is none), or a fresh pull on retry.

        Returns ``None`` when the document does not exist. Raises ``config.invalid`` when the
        remote document is invalid: an instance never overwrites what it cannot validate.
        """
        if fresh or self._cached is None:
            result = self.pull()
            if result.status == "invalid":
                raise ServiceError(CONFIG_INVALID, result.error or "shared config invalid")
            if result.status == "missing":
                return None
        return self._cached

    def _writable_copy(self, base: SharedConfigSnapshot | None) -> dict[str, Any]:
        if base is None:
            # The document is missing remotely. Recreate it from the last good copy if we have
            # one (so a deleted document does not silently drop every rule), else defaults.
            base = self._cached
            if base is None:
                return self._fresh("system")
        if not base.writable:
            raise ServiceError(
                CONFIG_SCHEMA_TOO_NEW,
                f"the shared config uses schema {base.schema}; this instance supports "
                f"{SHARED_CONFIG_SCHEMA}. Upgrade OmniSync before changing shared rules.",
            )
        return copy.deepcopy(base.document)

    def _write(
        self,
        doc: dict[str, Any],
        base: SharedConfigSnapshot | None,
        actor: str,
        history: list[dict[str, Any]],
        before_write: Callable[[], None] | None,
    ) -> SharedConfigSnapshot | None:
        """Conditional PUT. Returns the new snapshot, or ``None`` on a failed precondition."""
        doc["version"] = int(doc["version"]) + 1
        doc["updated_at"] = self._timestamp()
        doc["updated_by"] = self._actor(actor)
        doc["history"] = (list(doc["history"]) + history)[-HISTORY_LIMIT:]
        validate_document(doc)  # never publish something peers would reject
        if before_write is not None:
            before_write()
        try:
            if base is None:
                etag = self.store.put_json(self.key, doc, if_none_match=True)
            else:
                etag = self.store.put_json(self.key, doc, if_match=base.etag)
        except PreconditionFailed:
            return None
        self._cached = SharedConfigSnapshot(doc, etag)
        return self._cached

    def _history(self, change: ConfigChange, actor: str, source: str) -> dict[str, Any]:
        return {
            "at": self._timestamp(),
            "actor": actor,
            "instance": self.instance_name,
            "source": source,
            "section": change.section,
            "key": change.key,
            "old": _enc(change.old),
            "new": _enc(change.new),
        }

    def _fresh(self, actor: str) -> dict[str, Any]:
        return default_document(self._actor(actor), self._timestamp())

    def _actor(self, actor: str) -> str:
        return f"{actor}@{self.instance_name}"

    def _timestamp(self) -> str:
        return self._clock.now().strftime("%Y-%m-%dT%H:%M:%SZ")
