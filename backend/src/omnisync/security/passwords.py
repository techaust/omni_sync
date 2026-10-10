"""Password hashing (argon2id) and policy (06-SECURITY section 3, C-13)."""

from __future__ import annotations

from functools import cache
from importlib.resources import files

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

MIN_LENGTH = 12
MAX_LENGTH = 256  # bounds hashing cost for hostile input

# argon2-cffi defaults (RFC 9106 low-memory profile): time_cost=3, memory_cost=64 MiB, parallelism=4
_hasher = PasswordHasher()
# Verified against when a username does not exist, so response time does not reveal it.
_DUMMY_HASH = _hasher.hash("omnisync-dummy-password-for-timing")


@cache
def _common_passwords() -> frozenset[str]:
    text = (
        files("omnisync.security").joinpath("data/common-passwords.txt").read_text(encoding="utf-8")
    )
    return frozenset(line.strip().lower() for line in text.splitlines() if line.strip())


def policy_violations(password: str, username: str | None = None) -> list[str]:
    """Return human-readable reasons why ``password`` is rejected (empty list = acceptable)."""
    problems: list[str] = []
    if len(password) < MIN_LENGTH:
        problems.append(f"must be at least {MIN_LENGTH} characters")
    if len(password) > MAX_LENGTH:
        problems.append(f"must be at most {MAX_LENGTH} characters")
    if password.lower() in _common_passwords():
        problems.append("is too common")
    if username and username.lower() in password.lower():
        problems.append("must not contain the username")
    if password.strip() != password:
        problems.append("must not start or end with whitespace")
    return problems


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """Constant-work verification; ``password_hash=None`` verifies against a dummy hash."""
    if len(password) > MAX_LENGTH:
        password = password[:MAX_LENGTH]
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)
