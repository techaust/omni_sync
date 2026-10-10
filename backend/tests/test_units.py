"""Unit tests for small security and infrastructure components."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from omnisync.clock import FakeClock
from omnisync.errors import AppError, problem
from omnisync.logging_setup import JsonFormatter, register_secret, request_id_var
from omnisync.security import passwords
from omnisync.security.instance_lock import InstanceLock, InstanceLockedError
from omnisync.security.permissions import ROLE_PERMISSIONS, Permission, role_has
from omnisync.security.ratelimit import RateLimiter
from omnisync.security.tokens import SetupToken, token_hash
from omnisync.settings import Settings


@pytest.mark.parametrize(
    ("password", "expected"),
    [
        ("short", "at least 12"),
        ("unbelievable", "too common"),  # 12 characters and in the bundled common-password list
        ("owner-is-my-name-2026", "username"),
        (" padded-passphrase-1 ", "whitespace"),
        ("x" * 257, "at most 256"),
    ],
)
def test_password_policy_rejections(password: str, expected: str) -> None:
    assert any(expected in p for p in passwords.policy_violations(password, "owner"))


def test_password_policy_accepts_strong_passphrase() -> None:
    assert passwords.policy_violations("violet-anchor-meadow-42", "owner") == []


def test_hash_and_verify() -> None:
    hashed = passwords.hash_password("violet-anchor-meadow-42")
    assert hashed.startswith("$argon2id$")
    assert passwords.verify_password(hashed, "violet-anchor-meadow-42")
    assert not passwords.verify_password(hashed, "wrong")
    assert not passwords.verify_password(None, "anything")
    assert not passwords.verify_password("not-a-hash", "anything")


def test_role_grants_match_security_doc() -> None:
    assert ROLE_PERMISSIONS["viewer"] == {
        Permission.VIEW,
        Permission.ACCOUNT_SELF,
        Permission.AI_USE,
    }
    assert role_has("operator", Permission.RUNS_WRITE)
    assert not role_has("operator", Permission.CLEANUP_MANAGE)
    assert not role_has("operator", Permission.SECRETS_MANAGE)
    assert ROLE_PERMISSIONS["admin"] == frozenset(Permission)
    assert not role_has("nobody", Permission.VIEW)


def test_rate_limiter_window() -> None:
    clock = FakeClock()
    limiter = RateLimiter(clock)
    assert [limiter.hit("b", "k", 2, 60) for _ in range(2)] == [None, None]
    assert limiter.hit("b", "k", 2, 60) == 60
    assert limiter.hit("b", "other", 2, 60) is None
    clock.advance(seconds=60)
    assert limiter.hit("b", "k", 2, 60) is None


def test_setup_token_lifecycle(tmp_path: Path) -> None:
    path = tmp_path / "setup-token.txt"
    token = SetupToken(path)
    assert not token.matches("anything")
    shown = token.generate()
    assert path.read_text(encoding="utf-8").strip() == shown == token.display
    assert token.matches(shown.lower().replace("-", " "))
    assert not token.matches(shown[:-1])
    token.consume()
    assert not path.exists()
    assert not token.matches(shown)


def test_token_hash_is_sha256_hex() -> None:
    assert len(token_hash("abc")) == 64


def test_instance_lock_is_exclusive(tmp_path: Path) -> None:
    lock_path = tmp_path / "instance.lock"
    with InstanceLock(lock_path), pytest.raises(InstanceLockedError):
        InstanceLock(lock_path).acquire()
    with InstanceLock(lock_path):
        pass


def test_json_logging_redacts_registered_secrets() -> None:
    register_secret("s3cr3t-value-123")
    record = logging.LogRecord(
        "t", logging.INFO, __file__, 1, "token is %s", ("s3cr3t-value-123",), None
    )
    token = request_id_var.set("req-1")
    try:
        line = json.loads(JsonFormatter().format(record))
    finally:
        request_id_var.reset(token)
    assert line["event"] == "token is [REDACTED]"
    assert line["request_id"] == "req-1"
    assert line["level"] == "INFO"


def test_problem_and_unknown_error_codes() -> None:
    body = problem("auth.forbidden", detail="nope", instance="/x")
    assert body["status"] == 403
    assert body["type"].endswith("/auth.forbidden")
    with pytest.raises(ValueError, match="unknown error code"):
        AppError("made.up")


def test_settings_precedence_and_hosts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = tmp_path / "data"
    data.mkdir()
    (data / "omnisync.toml").write_text(
        'port = 9000\ninstance_name = "from-toml"\n', encoding="utf-8"
    )
    monkeypatch.setenv("OMNISYNC_DATA_DIR", str(data))
    monkeypatch.setenv("OMNISYNC_INSTANCE_NAME", "from-env")
    settings = Settings()
    assert settings.port == 9000  # from TOML
    assert settings.instance_name == "from-env"  # env beats TOML
    assert settings.host == "127.0.0.1"
    assert settings.allowed_hosts == {"127.0.0.1:9000", "localhost:9000"}
    monkeypatch.setenv("OMNISYNC_PORT", "80")
    with pytest.raises(ValueError, match="greater than or equal to 1024"):
        Settings()
