"""Secret resolution: environment variable, then OS keyring (FR-SET-03, C-05, C-06).

Secrets are resolved only here. Precedence follows legacy (``legacy:omnisync/config.py:188-202``)
minus ``config.env``: a non-empty environment variable wins, otherwise the keyring entry under
service ``omnisync`` with the same name (10-OPERATIONS section 3.1), so a legacy installation's
Windows Credential Manager entries are picked up without re-entry.

* Values are returned wrapped in :class:`SecretValue`, whose ``repr``/``str`` are masked.
* Every resolved or stored sensitive value is registered with the log redaction filter
  (:func:`omnisync.logging_setup.register_secret`), so it never appears in a log line.
* The API is write-only: :meth:`SecretStore.list_status` reports configured/missing and the
  source, never a value. A secret set by an environment variable cannot be changed here
  (``secret.set_by_environment``).
* The keyring backend is injectable; :class:`MemoryKeyring` is an in-memory fake for tests.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Final, Literal, Protocol

from omnisync.logging_setup import register_secret
from omnisync.services.errors import (
    SECRET_INVALID_VALUE,
    SECRET_KEYRING_UNAVAILABLE,
    SECRET_SET_BY_ENVIRONMENT,
    SECRET_UNKNOWN_NAME,
    ServiceError,
)

__all__ = [
    "AI_SECRET_NAMES",
    "ANTHROPIC_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_BUCKET_NAME",
    "AWS_REGION",
    "AWS_SECRET_ACCESS_KEY",
    "KEYRING_SERVICE",
    "OPENAI_API_KEY",
    "S3_SECRET_NAMES",
    "KeyringBackend",
    "MemoryKeyring",
    "SecretSource",
    "SecretStatus",
    "SecretStore",
    "SecretValue",
    "is_known_secret",
    "is_sensitive",
    "store_secret_names",
]

log = logging.getLogger(__name__)

KEYRING_SERVICE: Final = "omnisync"

AWS_ACCESS_KEY_ID: Final = "AWS_ACCESS_KEY_ID"
AWS_SECRET_ACCESS_KEY: Final = "AWS_SECRET_ACCESS_KEY"  # noqa: S105 - a name
# Region and bucket are resolved the same way for legacy compatibility (legacy
# ``AWS_REGION``/``AWS_BUCKET_NAME``) but are not confidential, so they are not redacted.
AWS_REGION: Final = "AWS_REGION"
AWS_BUCKET_NAME: Final = "AWS_BUCKET_NAME"
ANTHROPIC_API_KEY: Final = "ANTHROPIC_API_KEY"
OPENAI_API_KEY: Final = "OPENAI_API_KEY"

S3_SECRET_NAMES: Final = (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_BUCKET_NAME)
AI_SECRET_NAMES: Final = (ANTHROPIC_API_KEY, OPENAI_API_KEY)
_STATIC_NAMES: Final = frozenset(S3_SECRET_NAMES + AI_SECRET_NAMES)
_NON_SENSITIVE: Final = frozenset({AWS_REGION, AWS_BUCKET_NAME})

_STORE_SECRET_RE: Final = re.compile(r"^[A-Z0-9][A-Z0-9_]*_WC_CONSUMER_(?:KEY|SECRET)$")
_STORE_KEY_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_MAX_VALUE_LEN: Final = 4096
_MASK: Final = "**********"

SecretSource = Literal["environment", "keyring"]


class SecretValue:
    """A secret string whose ``repr`` and ``str`` never reveal the value (C-05)."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def get_secret_value(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return f"SecretValue('{_MASK}')"

    def __str__(self) -> str:
        return _MASK

    def __eq__(self, other: object) -> bool:
        if isinstance(other, SecretValue):
            return self._value == other._value
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._value)

    def __reduce__(self) -> tuple[object, ...]:
        raise TypeError("SecretValue cannot be pickled")


class KeyringBackend(Protocol):
    """The subset of the ``keyring`` module API that OmniSync uses."""

    def get_password(self, service_name: str, username: str) -> str | None: ...
    def set_password(self, service_name: str, username: str, password: str) -> None: ...
    def delete_password(self, service_name: str, username: str) -> None: ...


class MemoryKeyring:
    """In-memory keyring for tests. ``fail=True`` simulates an unavailable OS keyring."""

    def __init__(self, entries: Mapping[tuple[str, str], str] | None = None) -> None:
        self.entries: dict[tuple[str, str], str] = dict(entries or {})
        self.fail = False

    def _check(self) -> None:
        if self.fail:
            raise RuntimeError("keyring backend unavailable")

    def get_password(self, service_name: str, username: str) -> str | None:
        self._check()
        return self.entries.get((service_name, username))

    def set_password(self, service_name: str, username: str, password: str) -> None:
        self._check()
        self.entries[(service_name, username)] = password

    def delete_password(self, service_name: str, username: str) -> None:
        self._check()
        try:
            del self.entries[(service_name, username)]
        except KeyError:
            raise LookupError("no such password") from None


@dataclass(frozen=True, slots=True)
class SecretStatus:
    """Status of one secret for Settings -> Secrets. Never carries the value."""

    name: str
    configured: bool
    source: SecretSource | None
    sensitive: bool


def store_secret_names(store_key: str) -> tuple[str, str]:
    """Consumer key and secret names for a store (10-OPERATIONS section 3.1).

    The store key is upper-cased with ``-`` replaced by ``_``: ``my-shop`` gives
    ``MY_SHOP_WC_CONSUMER_KEY`` and ``MY_SHOP_WC_CONSUMER_SECRET``.
    """
    if not _STORE_KEY_RE.match(store_key):
        raise ServiceError(SECRET_UNKNOWN_NAME, f"invalid store key {store_key!r}")
    prefix = store_key.upper().replace("-", "_")
    return f"{prefix}_WC_CONSUMER_KEY", f"{prefix}_WC_CONSUMER_SECRET"


def is_known_secret(name: str) -> bool:
    return name in _STATIC_NAMES or bool(_STORE_SECRET_RE.match(name))


def is_sensitive(name: str) -> bool:
    return name not in _NON_SENSITIVE


def _require_known(name: str) -> None:
    if not is_known_secret(name):
        raise ServiceError(SECRET_UNKNOWN_NAME, f"unknown secret name {name!r}")


def _default_keyring() -> KeyringBackend | None:
    try:
        import keyring  # imported lazily: OS integration, not needed by tests
    except ImportError:  # pragma: no cover - keyring is a declared dependency
        return None
    return keyring


class SecretStore:
    """Resolve, store and report secrets. Environment first, then the OS keyring."""

    def __init__(
        self,
        keyring: KeyringBackend | None = None,
        *,
        environ: Mapping[str, str] | None = None,
        service: str = KEYRING_SERVICE,
    ) -> None:
        self._keyring = keyring if keyring is not None else _default_keyring()
        self._environ: Mapping[str, str] = os.environ if environ is None else environ
        self._service = service

    # -- reading ---------------------------------------------------------------------------

    def _from_env(self, name: str) -> str | None:
        value = self._environ.get(name, "").strip()
        return value or None

    def _from_keyring(self, name: str) -> str | None:
        if self._keyring is None:
            return None
        try:
            value = self._keyring.get_password(self._service, name)
        except Exception as exc:  # noqa: BLE001 - trust boundary with the OS keyring
            log.warning(
                "keyring read failed", extra={"data": {"name": name, "error": type(exc).__name__}}
            )
            return None
        value = (value or "").strip()
        return value or None

    def _lookup(self, name: str) -> tuple[str, SecretSource] | None:
        _require_known(name)
        env_value = self._from_env(name)
        if env_value is not None:
            return env_value, "environment"
        kr_value = self._from_keyring(name)
        if kr_value is not None:
            return kr_value, "keyring"
        return None

    def resolve(self, name: str) -> SecretValue | None:
        """The secret's value, or ``None`` when it is set nowhere."""
        found = self._lookup(name)
        if found is None:
            return None
        value = found[0]
        if is_sensitive(name):
            register_secret(value)
        return SecretValue(value)

    def source(self, name: str) -> SecretSource | None:
        """Where the effective value comes from: ``environment``, ``keyring`` or ``None``."""
        found = self._lookup(name)
        if found is None:
            return None
        if is_sensitive(name):
            register_secret(found[0])
        return found[1]

    def status(self, name: str) -> SecretStatus:
        src = self.source(name)
        return SecretStatus(name, src is not None, src, is_sensitive(name))

    def list_status(self, store_keys: Iterable[str] = ()) -> list[SecretStatus]:
        """Status of every known secret: S3, AI providers and each store's key pair."""
        names: list[str] = [*S3_SECRET_NAMES, *AI_SECRET_NAMES]
        for store_key in store_keys:
            names.extend(store_secret_names(store_key))
        return [self.status(n) for n in dict.fromkeys(names)]

    def register_for_redaction(self, store_keys: Iterable[str] = ()) -> None:
        """Register every resolvable sensitive value with the log filter (call at start-up)."""
        self.list_status(store_keys)

    # -- writing ---------------------------------------------------------------------------

    def _writable_keyring(self, name: str) -> KeyringBackend:
        _require_known(name)
        if self._from_env(name) is not None:
            raise ServiceError(
                SECRET_SET_BY_ENVIRONMENT,
                f"{name} is set by an environment variable and cannot be changed here",
            )
        if self._keyring is None:  # pragma: no cover - keyring is a declared dependency
            raise ServiceError(SECRET_KEYRING_UNAVAILABLE, "no OS keyring is available")
        return self._keyring

    def set(self, name: str, value: str | SecretValue) -> None:
        """Store ``value`` in the keyring. Refused when an environment variable sets ``name``."""
        raw = value.get_secret_value() if isinstance(value, SecretValue) else value
        raw = raw.strip()
        if not raw or len(raw) > _MAX_VALUE_LEN or any(c in raw for c in "\r\n\0"):
            raise ServiceError(SECRET_INVALID_VALUE, f"invalid value for {name}")
        backend = self._writable_keyring(name)
        if is_sensitive(name):
            register_secret(raw)
        try:
            backend.set_password(self._service, name, raw)
        except Exception as exc:  # noqa: BLE001 - OS keyring trust boundary
            raise ServiceError(
                SECRET_KEYRING_UNAVAILABLE,
                f"could not store {name} in the OS keyring ({type(exc).__name__})",
                retryable=True,
            ) from None

    def delete(self, name: str) -> bool:
        """Remove ``name`` from the keyring. Returns whether an entry was removed.

        Refused when an environment variable sets ``name``, because the removal would have no
        visible effect.
        """
        backend = self._writable_keyring(name)
        try:
            if backend.get_password(self._service, name) is None:
                return False
            backend.delete_password(self._service, name)
        except Exception as exc:  # noqa: BLE001 - OS keyring trust boundary
            raise ServiceError(
                SECRET_KEYRING_UNAVAILABLE,
                f"could not remove {name} from the OS keyring ({type(exc).__name__})",
                retryable=True,
            ) from None
        return True
