"""S3 client wrapper: product uploads, HEAD, list, delete and conditional JSON documents.

FR-PIPE-02, FR-PIPE-03, 03-ARCHITECTURE 5.2 and 6, 04-DATA-MODEL section 3.
Port of ``legacy:omnisync/s3.py`` with these intentional differences:

* **D-04 fix.** :meth:`S3Client.head` treats only HTTP 404 as "missing". 403 (and credential
  failures) raise :class:`ServiceError` ``s3.access_denied``; every other failure raises
  ``s3.error``. Legacy treated every error as "missing" and re-uploaded.
* **Streaming and multipart.** Uploads stream from the file on disk. Payloads above 8 MiB use
  multipart upload with 8 MiB parts, so each mutating call fits the 60 s lease-safe call
  deadline. The ``before_write`` hook (the lease write-deadline check) runs immediately before
  every mutating call: PUT, each part, complete-multipart and delete. If it raises during a
  multipart upload, the upload is aborted.
* **Metadata.** Objects carry ``latest-version`` and ``content-md5-hex``; the legacy
  ``latest_version`` key is still read (04-DATA-MODEL section 3).
* **Retries** are botocore's ``standard`` mode with a small attempt limit and explicit
  connect/read timeouts, instead of legacy's hand-written loop with 2-8 s sleeps. The total
  60 s per-call deadline is enforced by the caller (jobs layer).
* **Conditional documents.** :meth:`S3Client.get_json` / :meth:`S3Client.put_json` support
  ``If-Match`` / ``If-None-Match: *`` for the coordination objects under ``_omnisync/``;
  a failed precondition raises :class:`PreconditionFailed`.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, NamedTuple

import boto3  # type: ignore[import-untyped,unused-ignore]
from botocore.config import Config  # type: ignore[import-untyped,unused-ignore]
from botocore.exceptions import (  # type: ignore[import-untyped,unused-ignore]
    BotoCoreError,
    ClientError,
    NoCredentialsError,
    PartialCredentialsError,
)

from omnisync.core.archive import file_md5
from omnisync.services import object_store
from omnisync.services.errors import (
    S3_ACCESS_DENIED,
    S3_DOCUMENT_INVALID,
    S3_ERROR,
    S3_PRECONDITION_FAILED,
    S3_UPLOAD_FAILED,
    ServiceError,
)
from omnisync.services.secrets import (
    AWS_ACCESS_KEY_ID,
    AWS_BUCKET_NAME,
    AWS_REGION,
    AWS_SECRET_ACCESS_KEY,
    SecretStore,
    SecretValue,
)

__all__ = [
    "CALL_DEADLINE_SECONDS",
    "CONNECT_TIMEOUT_SECONDS",
    "CONTENT_TYPE_ZIP",
    "DEFAULT_BUCKET",
    "DEFAULT_CONTROL_PREFIX",
    "DEFAULT_REGION",
    "LEGACY_META_VERSION",
    "MAX_ATTEMPTS",
    "MAX_JSON_BYTES",
    "META_MD5",
    "META_VERSION",
    "MULTIPART_THRESHOLD",
    "PART_SIZE",
    "READ_TIMEOUT_SECONDS",
    "JsonDocument",
    "ObjectInfo",
    "ObjectSummary",
    "PreconditionFailed",
    "S3Client",
    "S3Settings",
    "UploadResult",
    "WriteGate",
    "object_key",
]

log = logging.getLogger(__name__)

MiB: Final = 1024 * 1024
PART_SIZE: Final = 8 * MiB
MULTIPART_THRESHOLD: Final = 8 * MiB  # payloads strictly larger use multipart
CALL_DEADLINE_SECONDS: Final = 60  # 03-ARCHITECTURE 5.2, enforced by the caller
CONNECT_TIMEOUT_SECONDS: Final = 10
READ_TIMEOUT_SECONDS: Final = 30
MAX_ATTEMPTS: Final = 3  # botocore "standard" mode: total attempts including the first
MAX_JSON_BYTES: Final = 4 * MiB
DELETE_BATCH: Final = 1000

CONTENT_TYPE_ZIP: Final = "application/zip"
CONTENT_TYPE_JSON: Final = "application/json"
META_VERSION: Final = "latest-version"
LEGACY_META_VERSION: Final = "latest_version"
META_MD5: Final = "content-md5-hex"

DEFAULT_BUCKET: Final = "productrepo"
DEFAULT_REGION: Final = "ap-south-1"
DEFAULT_CONTROL_PREFIX: Final = "_omnisync/"

_ACCESS_DENIED_CODES: Final = frozenset(
    {
        "AccessDenied",
        "AllAccessDisabled",
        "ExpiredToken",
        "InvalidAccessKeyId",
        "InvalidToken",
        "SignatureDoesNotMatch",
        "TokenRefreshRequired",
    }
)
_RETRYABLE_STATUS: Final = frozenset({408, 429, 500, 502, 503, 504})
_RETRYABLE_CODES: Final = frozenset(
    {"SlowDown", "Throttling", "ThrottlingException", "RequestTimeout", "InternalError"}
)
_PRECONDITION_CODES: Final = frozenset({"PreconditionFailed", "ConditionalRequestConflict"})

WriteGate = Callable[[], None]
"""Called immediately before every mutating call; raises to stop it (lease write deadline)."""


class PreconditionFailed(ServiceError, object_store.PreconditionFailed):
    """A conditional request was rejected: HTTP 412, 409 ``ConditionalRequestConflict``, or
    404 under ``If-Match``.

    It is both a :class:`ServiceError` (code ``s3.precondition_failed``) and the canonical
    :class:`omnisync.services.object_store.PreconditionFailed` caught by the lease and
    shared-config services.
    """

    def __init__(self, message: str) -> None:
        super().__init__(S3_PRECONDITION_FAILED, message, retryable=False)


@dataclass(frozen=True, slots=True)
class S3Settings:
    """Connection settings. Credentials are :class:`SecretValue`, so ``repr`` is safe."""

    bucket: str = DEFAULT_BUCKET
    region: str = DEFAULT_REGION
    access_key_id: SecretValue | None = None
    secret_access_key: SecretValue | None = None
    endpoint_url: str | None = None
    object_prefix: str = ""  # empty (bucket root) except for live tests (FR-PIPE-02)
    control_prefix: str = DEFAULT_CONTROL_PREFIX

    @classmethod
    def from_secrets(
        cls,
        secrets: SecretStore,
        *,
        bucket: str | None = None,
        region: str | None = None,
        endpoint_url: str | None = None,
        object_prefix: str = "",
        control_prefix: str = DEFAULT_CONTROL_PREFIX,
    ) -> S3Settings:
        """Build settings from the secret store; explicit arguments win over stored values."""
        stored_bucket = secrets.resolve(AWS_BUCKET_NAME)
        stored_region = secrets.resolve(AWS_REGION)
        return cls(
            bucket=bucket
            or (stored_bucket.get_secret_value() if stored_bucket else DEFAULT_BUCKET),
            region=region
            or (stored_region.get_secret_value() if stored_region else DEFAULT_REGION),
            access_key_id=secrets.resolve(AWS_ACCESS_KEY_ID),
            secret_access_key=secrets.resolve(AWS_SECRET_ACCESS_KEY),
            endpoint_url=endpoint_url,
            object_prefix=object_prefix,
            control_prefix=control_prefix,
        )


@dataclass(frozen=True, slots=True)
class ObjectInfo:
    """Result of a HEAD request."""

    key: str
    size: int
    etag: str
    metadata: dict[str, str] = field(default_factory=dict)
    last_modified: datetime | None = None

    @property
    def version(self) -> str | None:
        return self.metadata.get(META_VERSION) or self.metadata.get(LEGACY_META_VERSION)

    @property
    def md5_hex(self) -> str | None:
        return self.metadata.get(META_MD5)


@dataclass(frozen=True, slots=True)
class ObjectSummary:
    """One entry of a listing."""

    key: str
    size: int
    etag: str
    last_modified: datetime | None = None


@dataclass(frozen=True, slots=True)
class UploadResult:
    """Outcome of :meth:`S3Client.upload` / :meth:`S3Client.upload_if_changed`."""

    key: str
    uploaded: bool  # false when deduplicated (FR-PIPE-03)
    etag: str | None
    size: int
    parts: int  # 0 when skipped, 1 for a single PUT


class JsonDocument(NamedTuple):
    """``(data, etag)`` of a JSON document; unpacks like a 2-tuple."""

    data: dict[str, Any]
    etag: str


def object_key(slug: str, version: str, prefix: str = "") -> str:
    """Product object key ``<prefix><slug>-<version>.zip`` (FR-PIPE-02)."""
    if not slug or not version or "/" in slug or "/" in version:
        raise ValueError(f"invalid slug/version for an object key: {slug!r} {version!r}")
    return f"{prefix}{slug}-{version}.zip"


def _status(exc: Any) -> int | None:
    response = getattr(exc, "response", None) or {}
    meta = response.get("ResponseMetadata") or {}
    status = meta.get("HTTPStatusCode")
    return int(status) if status is not None else None


def _code(exc: Any) -> str:
    response = getattr(exc, "response", None) or {}
    return str((response.get("Error") or {}).get("Code") or "")


def _translate(exc: Exception, what: str, failure_code: str = S3_ERROR) -> ServiceError:
    """Map a botocore exception to a :class:`ServiceError`. Messages carry no secrets."""
    if isinstance(exc, ClientError):
        status = _status(exc)
        code = _code(exc)
        detail = f"{what}: HTTP {status} {code}".rstrip()
        if status == 412 or code in _PRECONDITION_CODES:
            return PreconditionFailed(detail)
        if status == 403 or code in _ACCESS_DENIED_CODES:
            return ServiceError(S3_ACCESS_DENIED, detail, retryable=False)
        retryable = (status in _RETRYABLE_STATUS) or code in _RETRYABLE_CODES
        return ServiceError(failure_code, detail, retryable=retryable)
    if isinstance(exc, NoCredentialsError | PartialCredentialsError):
        return ServiceError(
            S3_ACCESS_DENIED, f"{what}: AWS credentials are missing", retryable=False
        )
    # BotoCoreError (connect/read timeouts, endpoint errors) and OSError: transient.
    return ServiceError(failure_code, f"{what}: {type(exc).__name__}", retryable=True)


_BOTO_ERRORS: Final = (ClientError, BotoCoreError, OSError)


class S3Client:
    """Thin, typed wrapper over a boto3 S3 client bound to one bucket."""

    def __init__(self, settings: S3Settings, *, client: Any | None = None) -> None:
        self.settings = settings
        self.bucket = settings.bucket
        self._client: Any = client if client is not None else self._make_client(settings)

    @staticmethod
    def boto_config(settings: S3Settings) -> Any:
        """botocore config: explicit timeouts and a bounded number of standard-mode retries."""
        return Config(
            region_name=settings.region,
            connect_timeout=CONNECT_TIMEOUT_SECONDS,
            read_timeout=READ_TIMEOUT_SECONDS,
            retries={"mode": "standard", "total_max_attempts": MAX_ATTEMPTS},
            s3={"addressing_style": "path"} if settings.endpoint_url else None,
        )

    @classmethod
    def _make_client(cls, settings: S3Settings) -> Any:
        kwargs: dict[str, Any] = {
            "region_name": settings.region,
            "config": cls.boto_config(settings),
        }
        if settings.access_key_id is not None and settings.secret_access_key is not None:
            kwargs["aws_access_key_id"] = settings.access_key_id.get_secret_value()
            kwargs["aws_secret_access_key"] = settings.secret_access_key.get_secret_value()
        if settings.endpoint_url:
            kwargs["endpoint_url"] = settings.endpoint_url
        return boto3.client("s3", **kwargs)

    @property
    def raw(self) -> Any:
        """The underlying boto3 client (tests and capability probes only)."""
        return self._client

    def close(self) -> None:
        self._client.close()

    # -- reads ------------------------------------------------------------------------------

    def head(self, key: str) -> ObjectInfo | None:
        """Object metadata, or ``None`` if and only if S3 answers HTTP 404 (D-04 fix)."""
        try:
            resp = self._client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if _status(exc) == 404:
                return None
            raise _translate(exc, f"HEAD {key}") from None
        except _BOTO_ERRORS as exc:
            raise _translate(exc, f"HEAD {key}") from None
        return ObjectInfo(
            key=key,
            size=int(resp.get("ContentLength", 0)),
            etag=str(resp.get("ETag", "")),
            metadata={str(k).lower(): str(v) for k, v in (resp.get("Metadata") or {}).items()},
            last_modified=resp.get("LastModified"),
        )

    def exists(self, key: str) -> bool:
        return self.head(key) is not None

    def is_duplicate(self, key: str, md5_hex: str) -> bool:
        """True when ``key`` exists with the same ``content-md5-hex`` metadata (FR-PIPE-03)."""
        info = self.head(key)
        return info is not None and (info.md5_hex or "").lower() == md5_hex.lower()

    def list_objects(self, prefix: str = "") -> list[ObjectSummary]:
        """Every object under ``prefix`` (all pages)."""
        out: list[ObjectSummary] = []
        try:
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                out.extend(
                    ObjectSummary(
                        key=str(o["Key"]),
                        size=int(o.get("Size", 0)),
                        etag=str(o.get("ETag", "")),
                        last_modified=o.get("LastModified"),
                    )
                    for o in page.get("Contents", [])
                )
        except _BOTO_ERRORS as exc:
            raise _translate(exc, f"LIST {prefix or '/'}") from None
        return out

    def list_keys(self, prefix: str = "") -> list[str]:
        return [o.key for o in self.list_objects(prefix)]

    # -- uploads ----------------------------------------------------------------------------

    def upload(
        self,
        path: Path,
        key: str,
        version: str,
        md5_hex: str | None = None,
        *,
        before_write: WriteGate | None = None,
    ) -> UploadResult:
        """Upload ``path`` to ``key`` with metadata; multipart above 8 MiB (FR-PIPE-02)."""
        md5 = (md5_hex or file_md5(path)).lower()
        size = path.stat().st_size
        gate = before_write or _no_gate
        extra: dict[str, Any] = {
            "ContentType": CONTENT_TYPE_ZIP,
            "Metadata": {META_VERSION: version, META_MD5: md5},
        }
        if size > MULTIPART_THRESHOLD:
            return self._multipart(path, key, size, extra, gate)
        try:
            with path.open("rb") as body:
                gate()
                resp = self._client.put_object(Bucket=self.bucket, Key=key, Body=body, **extra)
        except _BOTO_ERRORS as exc:
            raise _translate(exc, f"PUT {key}", S3_UPLOAD_FAILED) from None
        log.info("s3 upload", extra={"data": {"key": key, "size": size, "parts": 1}})
        return UploadResult(key, True, str(resp.get("ETag", "")), size, 1)

    def _multipart(
        self, path: Path, key: str, size: int, extra: dict[str, Any], gate: WriteGate
    ) -> UploadResult:
        gate()
        try:
            created = self._client.create_multipart_upload(Bucket=self.bucket, Key=key, **extra)
        except _BOTO_ERRORS as exc:
            raise _translate(exc, f"CREATE-MULTIPART {key}", S3_UPLOAD_FAILED) from None
        upload_id = str(created["UploadId"])
        parts: list[dict[str, Any]] = []
        try:
            with path.open("rb") as fh:
                while chunk := fh.read(PART_SIZE):
                    number = len(parts) + 1
                    gate()
                    resp = self._client.upload_part(
                        Bucket=self.bucket,
                        Key=key,
                        UploadId=upload_id,
                        PartNumber=number,
                        Body=chunk,
                    )
                    parts.append({"PartNumber": number, "ETag": resp["ETag"]})
            gate()
            done = self._client.complete_multipart_upload(
                Bucket=self.bucket,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={"Parts": parts},
            )
        except BaseException as exc:
            self._abort(key, upload_id)
            if isinstance(exc, _BOTO_ERRORS):
                raise _translate(exc, f"MULTIPART {key}", S3_UPLOAD_FAILED) from None
            raise
        log.info("s3 upload", extra={"data": {"key": key, "size": size, "parts": len(parts)}})
        return UploadResult(key, True, str(done.get("ETag", "")), size, len(parts))

    def _abort(self, key: str, upload_id: str) -> None:
        try:
            self._client.abort_multipart_upload(Bucket=self.bucket, Key=key, UploadId=upload_id)
        except _BOTO_ERRORS as exc:
            log.warning(
                "s3 abort multipart failed",
                extra={"data": {"key": key, "error": type(exc).__name__}},
            )

    def upload_if_changed(
        self,
        path: Path,
        key: str,
        version: str,
        md5_hex: str | None = None,
        *,
        before_write: WriteGate | None = None,
    ) -> UploadResult:
        """Skip when ``key`` already has the same MD5 metadata, otherwise upload.

        HEAD errors other than 404 propagate (``s3.access_denied`` / ``s3.error``) and nothing
        is uploaded (FR-PIPE-03, D-04).
        """
        md5 = (md5_hex or file_md5(path)).lower()
        if self.is_duplicate(key, md5):
            log.info("s3 upload skipped", extra={"data": {"key": key, "reason": "same md5"}})
            return UploadResult(key, False, None, path.stat().st_size, 0)
        return self.upload(path, key, version, md5, before_write=before_write)

    # -- deletes ----------------------------------------------------------------------------

    def delete(
        self, key: str, *, if_match: str | None = None, before_write: WriteGate | None = None
    ) -> None:
        """Delete ``key``; deleting a missing object is a no-op.

        With ``if_match`` the delete is conditional: a different ETag or a missing object
        raises :class:`PreconditionFailed`.
        """
        kwargs: dict[str, Any] = {"Bucket": self.bucket, "Key": key}
        if if_match is not None:
            kwargs["IfMatch"] = if_match
        (before_write or _no_gate)()
        try:
            self._client.delete_object(**kwargs)
        except ClientError as exc:
            if _status(exc) == 404:
                if if_match is None:
                    return
                raise PreconditionFailed(f"DELETE {key}: HTTP 404 under If-Match") from None
            raise _translate(exc, f"DELETE {key}") from None
        except _BOTO_ERRORS as exc:
            raise _translate(exc, f"DELETE {key}") from None

    def delete_many(self, keys: Iterable[str], *, before_write: WriteGate | None = None) -> int:
        """Delete ``keys`` in batches of 1,000. Returns the number deleted.

        Any per-key error raises (``s3.access_denied`` when S3 reports ``AccessDenied``).
        """
        gate = before_write or _no_gate
        total = 0
        for batch in _batches(list(dict.fromkeys(keys)), DELETE_BATCH):
            gate()
            try:
                resp = self._client.delete_objects(
                    Bucket=self.bucket,
                    Delete={"Objects": [{"Key": k} for k in batch], "Quiet": True},
                )
            except _BOTO_ERRORS as exc:
                raise _translate(exc, f"DELETE {len(batch)} objects") from None
            errors = resp.get("Errors") or []
            if errors:
                first = errors[0]
                code = str(first.get("Code", ""))
                what = f"DELETE {first.get('Key', '?')}: {code} ({len(errors)} failed)"
                if code in _ACCESS_DENIED_CODES:
                    raise ServiceError(S3_ACCESS_DENIED, what, retryable=False)
                raise ServiceError(S3_ERROR, what, retryable=code in _RETRYABLE_CODES)
            total += len(batch)
        return total

    # -- JSON documents (coordination) ------------------------------------------------------

    def get_json(self, key: str) -> JsonDocument | None:
        """``(data, etag)`` of a small JSON document, or ``None`` when it does not exist."""
        try:
            resp = self._client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if _status(exc) == 404:
                return None
            raise _translate(exc, f"GET {key}") from None
        except _BOTO_ERRORS as exc:
            raise _translate(exc, f"GET {key}") from None
        body = resp["Body"]
        try:
            raw = body.read(MAX_JSON_BYTES + 1)
        except _BOTO_ERRORS as exc:
            raise _translate(exc, f"GET {key}") from None
        finally:
            body.close()
        if len(raw) > MAX_JSON_BYTES:
            raise ServiceError(S3_DOCUMENT_INVALID, f"GET {key}: document larger than 4 MiB")
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise ServiceError(S3_DOCUMENT_INVALID, f"GET {key}: not valid JSON") from None
        if not isinstance(data, dict):
            raise ServiceError(S3_DOCUMENT_INVALID, f"GET {key}: not a JSON object")
        return JsonDocument(data, str(resp.get("ETag", "")))

    def put_json(
        self,
        key: str,
        data: Any,
        *,
        if_match: str | None = None,
        if_none_match: bool = False,
        before_write: WriteGate | None = None,
    ) -> str:
        """Write a JSON document and return its new ETag.

        ``if_match`` sends ``If-Match: <etag>``; ``if_none_match`` sends ``If-None-Match: *``
        (create only). A failed precondition raises :class:`PreconditionFailed`.
        """
        if if_match is not None and if_none_match:
            raise ValueError("if_match and if_none_match are mutually exclusive")
        body = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        kwargs: dict[str, Any] = {
            "Bucket": self.bucket,
            "Key": key,
            "Body": body.encode("utf-8"),
            "ContentType": CONTENT_TYPE_JSON,
        }
        if if_match is not None:
            kwargs["IfMatch"] = if_match
        if if_none_match:
            kwargs["IfNoneMatch"] = "*"
        (before_write or _no_gate)()
        try:
            resp = self._client.put_object(**kwargs)
        except ClientError as exc:
            if if_match is not None and _status(exc) == 404:
                raise PreconditionFailed(f"PUT {key}: HTTP 404 under If-Match") from None
            raise _translate(exc, f"PUT {key}") from None
        except _BOTO_ERRORS as exc:
            raise _translate(exc, f"PUT {key}") from None
        return str(resp.get("ETag", ""))

    # -- S3 time -----------------------------------------------------------------------------

    def server_date(self) -> float:
        """S3's ``Date`` header (POSIX seconds) from one fresh HEAD on the lease key.

        A 404 also carries ``Date``, so the object need not exist (03-ARCHITECTURE 6.4).
        """
        key = f"{self.settings.control_prefix}control/lease.json"
        try:
            resp = self._client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if _status(exc) != 404:
                raise _translate(exc, f"HEAD {key}") from None
            resp = exc.response
        except _BOTO_ERRORS as exc:
            raise _translate(exc, f"HEAD {key}") from None
        headers = (resp.get("ResponseMetadata") or {}).get("HTTPHeaders") or {}
        raw = headers.get("date")
        try:
            return parsedate_to_datetime(str(raw)).timestamp()
        except (TypeError, ValueError):
            raise ServiceError(S3_ERROR, f"HEAD {key}: no usable Date header", True) from None


if TYPE_CHECKING:  # S3Client satisfies the coordination protocols (services/object_store.py)

    def _protocol_check(client: S3Client) -> object_store.JsonObjectStore:
        source: object_store.ServerDateSource = client
        del source
        return client


def _no_gate() -> None:
    return None


def _batches(items: list[str], size: int) -> Iterator[list[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]
