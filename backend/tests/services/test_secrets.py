"""Secret resolution (FR-SET-03, C-05/C-06) and the canary-secret scan (NFR-SEC-03, C-07)."""

from __future__ import annotations

import io
import logging
import pickle
from collections.abc import Iterator
from pathlib import Path

import pytest
from moto import mock_aws

from omnisync.logging_setup import REDACTED, JsonFormatter, redact
from omnisync.services import secrets as secrets_module
from omnisync.services.errors import (
    SECRET_INVALID_VALUE,
    SECRET_KEYRING_UNAVAILABLE,
    SECRET_SET_BY_ENVIRONMENT,
    SECRET_UNKNOWN_NAME,
    ServiceError,
)
from omnisync.services.s3 import PreconditionFailed, S3Client, S3Settings
from omnisync.services.secrets import (
    AWS_ACCESS_KEY_ID,
    AWS_BUCKET_NAME,
    AWS_REGION,
    AWS_SECRET_ACCESS_KEY,
    KEYRING_SERVICE,
    MemoryKeyring,
    SecretStatus,
    SecretStore,
    SecretValue,
    is_known_secret,
    is_sensitive,
    store_secret_names,
)

GPL_KEY = "GPLFAMILY_WC_CONSUMER_KEY"


@pytest.fixture
def keyring() -> MemoryKeyring:
    return MemoryKeyring()


def make_store(keyring: MemoryKeyring, env: dict[str, str] | None = None) -> SecretStore:
    return SecretStore(keyring, environ=env or {})


class TestPrecedence:
    def test_env_var_wins(self, keyring: MemoryKeyring) -> None:
        """Ported from legacy tests/test_config.py::test_env_var_wins."""
        keyring.entries[(KEYRING_SERVICE, GPL_KEY)] = "from-keyring"
        store = make_store(keyring, {GPL_KEY: "from-env"})
        value = store.resolve(GPL_KEY)
        assert value is not None
        assert value.get_secret_value() == "from-env"
        assert store.source(GPL_KEY) == "environment"

    def test_keyring_used_when_env_absent(self, keyring: MemoryKeyring) -> None:
        """Ported from legacy tests/test_config.py::test_keyring_before_env_file.

        There is no ``config.env`` any more (10-OPERATIONS 3.1); the keyring is the fallback.
        """
        keyring.entries[(KEYRING_SERVICE, GPL_KEY)] = "from-keyring"
        store = make_store(keyring)
        value = store.resolve(GPL_KEY)
        assert value is not None
        assert value.get_secret_value() == "from-keyring"
        assert store.source(GPL_KEY) == "keyring"

    def test_default_when_absent(self, keyring: MemoryKeyring) -> None:
        """Ported from legacy tests/test_config.py::test_default_when_absent."""
        store = make_store(keyring)
        assert store.resolve(AWS_SECRET_ACCESS_KEY) is None
        assert store.source(AWS_SECRET_ACCESS_KEY) is None

    def test_blank_env_is_unset_and_values_are_stripped(self, keyring: MemoryKeyring) -> None:
        keyring.entries[(KEYRING_SERVICE, GPL_KEY)] = "  padded-value  "
        store = make_store(keyring, {GPL_KEY: "   "})
        value = store.resolve(GPL_KEY)
        assert value is not None
        assert value.get_secret_value() == "padded-value"
        assert store.source(GPL_KEY) == "keyring"

    def test_blank_keyring_entry_is_unset(self, keyring: MemoryKeyring) -> None:
        keyring.entries[(KEYRING_SERVICE, GPL_KEY)] = "  "
        assert make_store(keyring).resolve(GPL_KEY) is None

    def test_uses_process_environment_by_default(
        self, keyring: MemoryKeyring, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-from-process-env")
        store = SecretStore(keyring)
        assert store.source("OPENAI_API_KEY") == "environment"

    def test_keyring_read_failure_is_treated_as_unset(
        self, keyring: MemoryKeyring, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Ported from legacy tests/test_config.py::TestKeyringAvailability::test_returns_bool.

        An unusable OS keyring must not crash resolution.
        """
        keyring.fail = True
        with caplog.at_level(logging.WARNING):
            assert make_store(keyring).resolve(GPL_KEY) is None
        assert "keyring read failed" in caplog.text

    def test_without_any_keyring_backend(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(secrets_module, "_default_keyring", lambda: None)
        store = SecretStore(environ={GPL_KEY: "ck_env_value"})
        assert store.source(GPL_KEY) == "environment"
        assert store.resolve("OPENAI_API_KEY") is None

    def test_default_backend_is_the_keyring_module(self) -> None:
        backend = secrets_module._default_keyring()
        assert backend is not None
        assert callable(backend.get_password)


class TestNames:
    def test_contains_all_six_legacy_secrets(self) -> None:
        """Ported from legacy tests/test_config.py::TestSecretKeys::test_contains_all_six."""
        for name in (
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "GPLFAMILY_WC_CONSUMER_KEY",
            "GPLFAMILY_WC_CONSUMER_SECRET",
            "PLUGINWAY_WC_CONSUMER_KEY",
            "PLUGINWAY_WC_CONSUMER_SECRET",
        ):
            assert is_known_secret(name)

    def test_ai_and_s3_settings_names(self) -> None:
        for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", AWS_REGION, AWS_BUCKET_NAME):
            assert is_known_secret(name)
        assert not is_known_secret("PATH")
        assert not is_known_secret("_WC_CONSUMER_KEY")

    def test_store_secret_names(self) -> None:
        assert store_secret_names("gplfamily") == (
            "GPLFAMILY_WC_CONSUMER_KEY",
            "GPLFAMILY_WC_CONSUMER_SECRET",
        )
        assert store_secret_names("my-shop") == (
            "MY_SHOP_WC_CONSUMER_KEY",
            "MY_SHOP_WC_CONSUMER_SECRET",
        )

    @pytest.mark.parametrize("bad", ["", "-shop", "my shop", "shop/x"])
    def test_invalid_store_key(self, bad: str) -> None:
        with pytest.raises(ServiceError) as info:
            store_secret_names(bad)
        assert info.value.code == SECRET_UNKNOWN_NAME

    def test_sensitivity(self) -> None:
        assert is_sensitive(AWS_SECRET_ACCESS_KEY)
        assert not is_sensitive(AWS_REGION)
        assert not is_sensitive(AWS_BUCKET_NAME)

    def test_unknown_name_is_rejected(self, keyring: MemoryKeyring) -> None:
        store = make_store(keyring)
        for call in (store.resolve, store.source, store.delete):
            with pytest.raises(ServiceError) as info:
                call("HOME")
            assert info.value.code == SECRET_UNKNOWN_NAME
        with pytest.raises(ServiceError):
            store.set("HOME", "value-123")


class TestSecretValue:
    def test_repr_and_str_are_masked(self) -> None:
        value = SecretValue("hunter2-very-secret")
        assert "hunter2" not in repr(value)
        assert "hunter2" not in str(value)
        assert "hunter2" not in f"{value} {value!r}"
        assert value.get_secret_value() == "hunter2-very-secret"

    def test_equality_and_hash(self) -> None:
        assert SecretValue("abc") == SecretValue("abc")
        assert SecretValue("abc") != SecretValue("abd")
        assert SecretValue("abc") != "abc"
        assert len({SecretValue("abc"), SecretValue("abc")}) == 1

    def test_cannot_be_pickled(self) -> None:
        with pytest.raises(TypeError):
            pickle.dumps(SecretValue("abc"))


class TestWrite:
    def test_set_stores_in_keyring(self, keyring: MemoryKeyring) -> None:
        store = make_store(keyring)
        store.set(AWS_ACCESS_KEY_ID, "  AKIAEXAMPLE123  ")
        assert keyring.entries[(KEYRING_SERVICE, AWS_ACCESS_KEY_ID)] == "AKIAEXAMPLE123"
        assert store.source(AWS_ACCESS_KEY_ID) == "keyring"

    def test_set_non_sensitive_value(self, keyring: MemoryKeyring) -> None:
        make_store(keyring).set(AWS_REGION, "eu-south-9")
        assert keyring.entries[(KEYRING_SERVICE, AWS_REGION)] == "eu-south-9"
        assert redact("eu-south-9") == "eu-south-9"

    def test_set_accepts_secret_value(self, keyring: MemoryKeyring) -> None:
        make_store(keyring).set(AWS_ACCESS_KEY_ID, SecretValue("AKIAWRAPPED1"))
        assert keyring.entries[(KEYRING_SERVICE, AWS_ACCESS_KEY_ID)] == "AKIAWRAPPED1"

    def test_set_refused_when_set_by_environment(self, keyring: MemoryKeyring) -> None:
        store = make_store(keyring, {AWS_ACCESS_KEY_ID: "AKIAENV"})
        with pytest.raises(ServiceError) as info:
            store.set(AWS_ACCESS_KEY_ID, "AKIANEW")
        assert info.value.code == SECRET_SET_BY_ENVIRONMENT
        assert info.value.retryable is False
        assert keyring.entries == {}

    @pytest.mark.parametrize("bad", ["", "   ", "a\nb", "a\rb", "a\0b", "x" * 4097])
    def test_set_rejects_invalid_values(self, keyring: MemoryKeyring, bad: str) -> None:
        with pytest.raises(ServiceError) as info:
            make_store(keyring).set(AWS_ACCESS_KEY_ID, bad)
        assert info.value.code == SECRET_INVALID_VALUE
        assert keyring.entries == {}

    def test_set_keyring_failure(self, keyring: MemoryKeyring) -> None:
        keyring.fail = True
        with pytest.raises(ServiceError) as info:
            make_store(keyring).set(AWS_ACCESS_KEY_ID, "AKIAEXAMPLE123")
        assert info.value.code == SECRET_KEYRING_UNAVAILABLE
        assert info.value.retryable is True
        assert "AKIAEXAMPLE123" not in str(info.value)
        assert info.value.__cause__ is None

    def test_delete(self, keyring: MemoryKeyring) -> None:
        store = make_store(keyring)
        store.set(GPL_KEY, "ck_value_1234")
        assert store.delete(GPL_KEY) is True
        assert store.resolve(GPL_KEY) is None
        assert store.delete(GPL_KEY) is False

    def test_delete_refused_when_set_by_environment(self, keyring: MemoryKeyring) -> None:
        with pytest.raises(ServiceError) as info:
            make_store(keyring, {GPL_KEY: "ck_env"}).delete(GPL_KEY)
        assert info.value.code == SECRET_SET_BY_ENVIRONMENT

    def test_delete_keyring_failure(self, keyring: MemoryKeyring) -> None:
        keyring.entries[(KEYRING_SERVICE, GPL_KEY)] = "ck_value_1234"
        keyring.fail = True
        with pytest.raises(ServiceError) as info:
            make_store(keyring).delete(GPL_KEY)
        assert info.value.code == SECRET_KEYRING_UNAVAILABLE

    def test_memory_keyring_delete_missing(self, keyring: MemoryKeyring) -> None:
        with pytest.raises(LookupError):
            keyring.delete_password(KEYRING_SERVICE, "nope")


class TestStatus:
    def test_list_status_reports_source_never_values(self, keyring: MemoryKeyring) -> None:
        keyring.entries[(KEYRING_SERVICE, AWS_SECRET_ACCESS_KEY)] = "keyring-secret-value"
        store = make_store(keyring, {"PLUGINWAY_WC_CONSUMER_KEY": "env-consumer-key"})
        statuses = store.list_status(["gplfamily", "pluginway", "gplfamily"])
        by_name = {s.name: s for s in statuses}
        assert len(statuses) == len(by_name)  # de-duplicated
        assert by_name[AWS_SECRET_ACCESS_KEY] == SecretStatus(
            AWS_SECRET_ACCESS_KEY, True, "keyring", True
        )
        assert by_name["PLUGINWAY_WC_CONSUMER_KEY"].source == "environment"
        assert by_name[GPL_KEY] == SecretStatus(GPL_KEY, False, None, True)
        assert by_name[AWS_REGION].sensitive is False
        dump = repr(statuses)
        assert "keyring-secret-value" not in dump
        assert "env-consumer-key" not in dump

    def test_resolved_values_are_registered_for_redaction(self, keyring: MemoryKeyring) -> None:
        keyring.entries[(KEYRING_SERVICE, AWS_SECRET_ACCESS_KEY)] = "registered-on-resolve-1"
        keyring.entries[(KEYRING_SERVICE, AWS_REGION)] = "eu-north-77"
        store = make_store(keyring)
        store.register_for_redaction(["gplfamily"])
        assert redact("x registered-on-resolve-1 y") == f"x {REDACTED} y"
        assert store.resolve(AWS_REGION) == SecretValue("eu-north-77")
        assert redact("eu-north-77") == "eu-north-77"  # not confidential


# --------------------------------------------------------------------------------------------
# Canary-secret scan (NFR-SEC-03, C-07)
# --------------------------------------------------------------------------------------------

CANARY = "CANARY-7f3c1e9a-secret-DO-NOT-LOG"


def _explode(value: str) -> None:
    raise RuntimeError(f"boom {value}")


@pytest.fixture
def captured_logs() -> Iterator[io.StringIO]:
    """Route every logger (including botocore DEBUG) through the production JSON formatter."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        yield stream
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)


def test_canary_secret_never_appears_in_logs(
    captured_logs: io.StringIO, keyring: MemoryKeyring, tmp_path: Path
) -> None:
    store = make_store(keyring)
    names = [AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, "ANTHROPIC_API_KEY", "OPENAI_API_KEY"]
    names += [*store_secret_names("gplfamily"), *store_secret_names("my-shop")]
    for i, name in enumerate(names):
        store.set(name, f"{CANARY}-{i}")

    log = logging.getLogger("omnisync.canary")
    secret = store.resolve(AWS_SECRET_ACCESS_KEY)
    assert secret is not None
    raw = secret.get_secret_value()

    # Every level, raw value in the message, in args, in structured data, and the wrapper.
    for level in (logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL):
        log.log(level, "value %s wrapper %r", raw, secret, extra={"data": {"v": raw}})
        log.log(level, f"inline {raw}")
    # Exception path with the value in the exception message and traceback.
    try:
        _explode(raw)
    except RuntimeError:
        log.exception("failed with %s", raw)

    # Real S3 traffic through moto with canary credentials; botocore DEBUG logs the signed
    # request headers (Authorization: ... Credential=<access key id>/...).
    access_key = store.resolve(AWS_ACCESS_KEY_ID)
    with mock_aws():
        settings = S3Settings(
            bucket="canary-bucket",
            region="us-east-1",
            access_key_id=access_key,
            secret_access_key=secret,
        )
        client = S3Client(settings)
        client.raw.create_bucket(Bucket="canary-bucket")
        log.info("settings %r", settings)
        assert client.head("missing.zip") is None
        client.put_json("_omnisync/x.json", {"a": 1}, if_none_match=True)
        try:
            client.put_json("_omnisync/x.json", {"a": 2}, if_none_match=True)
        except PreconditionFailed:
            log.exception("precondition failed")
        payload = tmp_path / "p.zip"
        payload.write_bytes(b"zip")
        client.upload(payload, "p-1.0.zip", "1.0")
        client.close()

    # Status listing (what the API would expose).
    log.info("status %r", store.list_status(["gplfamily", "my-shop"]))

    output = captured_logs.getvalue()
    assert REDACTED in output  # the filter really ran on canary-bearing lines
    assert "Credential=" in output or "Authorization" in output  # botocore debug was captured
    assert CANARY not in output
