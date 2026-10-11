"""Service-layer errors with stable codes (05-API-SPEC section 3).

Services talk to external systems (S3, WooCommerce, the OS keyring). A failure is reported as
:class:`ServiceError` with a stable ``code`` that jobs store on items and the API layer
translates into Problem Details. ``retryable`` tells the caller whether repeating the same
call later can succeed (network errors, throttling, 5xx) or not (access denied, bad input).

Messages never contain secret values (NFR-SEC-03).
"""

from __future__ import annotations

__all__ = [
    "S3_ACCESS_DENIED",
    "S3_DOCUMENT_INVALID",
    "S3_ERROR",
    "S3_PRECONDITION_FAILED",
    "S3_UPLOAD_FAILED",
    "SECRET_INVALID_VALUE",
    "SECRET_KEYRING_UNAVAILABLE",
    "SECRET_SET_BY_ENVIRONMENT",
    "SECRET_UNKNOWN_NAME",
    "ServiceError",
]

# S3 (services/s3.py)
S3_ACCESS_DENIED = "s3.access_denied"
S3_ERROR = "s3.error"
S3_UPLOAD_FAILED = "s3.upload_failed"
S3_PRECONDITION_FAILED = "s3.precondition_failed"
S3_DOCUMENT_INVALID = "s3.document_invalid"

# Secrets (services/secrets.py)
SECRET_SET_BY_ENVIRONMENT = "secret.set_by_environment"  # noqa: S105 - error code, not a password
SECRET_UNKNOWN_NAME = "secret.unknown_name"  # noqa: S105 - error code, not a password
SECRET_INVALID_VALUE = "secret.invalid_value"  # noqa: S105 - error code, not a password
SECRET_KEYRING_UNAVAILABLE = "secret.keyring_unavailable"  # noqa: S105 - error code, not a password


class ServiceError(Exception):
    """A failure of a service call with a stable ``code``.

    ``retryable`` is true when repeating the call later may succeed.
    """

    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retryable = retryable

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(code={self.code!r}, message={self.message!r}, "
            f"retryable={self.retryable!r})"
        )
