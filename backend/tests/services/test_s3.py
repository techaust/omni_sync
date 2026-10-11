"""S3 client wrapper (FR-PIPE-02, FR-PIPE-03, D-04) against moto and botocore Stubber."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import boto3
import pytest
from botocore.exceptions import EndpointConnectionError, NoCredentialsError
from botocore.stub import Stubber
from moto import mock_aws

from omnisync.services import object_store
from omnisync.services import s3 as s3_module
from omnisync.services.errors import (
    S3_ACCESS_DENIED,
    S3_DOCUMENT_INVALID,
    S3_ERROR,
    S3_PRECONDITION_FAILED,
    S3_UPLOAD_FAILED,
    ServiceError,
)
from omnisync.services.s3 import (
    LEGACY_META_VERSION,
    META_MD5,
    META_VERSION,
    MULTIPART_THRESHOLD,
    PART_SIZE,
    PreconditionFailed,
    S3Client,
    S3Settings,
    object_key,
)
from omnisync.services.secrets import KEYRING_SERVICE, MemoryKeyring, SecretStore, SecretValue

BUCKET = "omnisync-test-bucket"
REGION = "us-east-1"


def _settings(**kw: Any) -> S3Settings:
    return S3Settings(
        bucket=BUCKET,
        region=REGION,
        access_key_id=SecretValue("testing-access-key"),
        secret_access_key=SecretValue("testing-secret-key"),
        **kw,
    )


@dataclass
class Calls:
    """Records the S3 operations a client sends (``provide-client-params`` events)."""

    names: list[str] = field(default_factory=list)

    def __call__(self, model: Any, **_: Any) -> None:
        self.names.append(model.name)

    def count(self, name: str) -> int:
        return self.names.count(name)


@pytest.fixture
def s3() -> Iterator[S3Client]:
    with mock_aws():
        client = S3Client(_settings())
        client.raw.create_bucket(Bucket=BUCKET)
        yield client
        client.close()


@pytest.fixture
def calls(s3: S3Client) -> Calls:
    recorder = Calls()
    s3.raw.meta.events.register("provide-client-params.s3", recorder)
    return recorder


@pytest.fixture
def stubbed() -> Iterator[tuple[S3Client, Stubber]]:
    raw = boto3.client(
        "s3",
        region_name=REGION,
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )
    client = S3Client(_settings(), client=raw)
    with Stubber(raw) as stub:
        yield client, stub
        stub.assert_no_pending_responses()


def _payload(tmp_path: Path, size: int, name: str = "payload.zip") -> tuple[Path, str]:
    path = tmp_path / name
    data = (b"0123456789abcdef" * (size // 16 + 1))[:size]
    path.write_bytes(data)
    return path, hashlib.md5(data).hexdigest()  # noqa: S324 - content fingerprint


def _body(s3: S3Client, key: str) -> bytes:
    data: bytes = s3.raw.get_object(Bucket=BUCKET, Key=key)["Body"].read()
    return data


class TestConstruction:
    def test_creates_client(self, s3: S3Client) -> None:
        """Ported from legacy tests/test_s3.py::TestConstruction::test_creates_client."""
        assert s3.bucket == BUCKET
        assert s3.raw is not None

    def test_close_releases_client(self) -> None:
        """Ported from legacy tests/test_s3.py::TestRetries::test_close_releases_client."""

        class Raw:
            closed = False

            def close(self) -> None:
                self.closed = True

        raw = Raw()
        S3Client(_settings(), client=raw).close()
        assert raw.closed

    def test_boto_config_is_lease_safe(self) -> None:
        """Replaces legacy tests/test_s3.py::TestRetries (hand-written retry loop).

        Retries are botocore standard mode with a small attempt limit and explicit timeouts,
        so a single call stays well inside the 60 s lease-safe deadline.
        """
        cfg = S3Client.boto_config(_settings())
        assert cfg.retries == {"mode": "standard", "total_max_attempts": 3}
        assert cfg.connect_timeout == s3_module.CONNECT_TIMEOUT_SECONDS
        assert cfg.read_timeout == s3_module.READ_TIMEOUT_SECONDS
        assert (
            s3_module.CONNECT_TIMEOUT_SECONDS + s3_module.READ_TIMEOUT_SECONDS
            < s3_module.CALL_DEADLINE_SECONDS
        )
        assert cfg.s3 is None
        custom = S3Client.boto_config(_settings(endpoint_url="http://127.0.0.1:9000"))
        assert custom.s3 == {"addressing_style": "path"}

    def test_endpoint_url_and_default_credentials(self) -> None:
        client = S3Client(S3Settings(bucket=BUCKET, endpoint_url="http://127.0.0.1:9000"))
        assert client.raw.meta.endpoint_url == "http://127.0.0.1:9000"
        assert client.raw.meta.region_name == "ap-south-1"
        client.close()

    def test_settings_repr_hides_credentials(self) -> None:
        text = repr(_settings())
        assert "testing-secret-key" not in text
        assert "testing-access-key" not in text

    def test_settings_from_secrets(self) -> None:
        keyring = MemoryKeyring(
            {
                (KEYRING_SERVICE, "AWS_ACCESS_KEY_ID"): "AKIAKEYRING",
                (KEYRING_SERVICE, "AWS_SECRET_ACCESS_KEY"): "secret-from-keyring",
                (KEYRING_SERVICE, "AWS_BUCKET_NAME"): "bucket-from-keyring",
            }
        )
        store = SecretStore(keyring, environ={"AWS_REGION": "eu-west-1"})
        settings = S3Settings.from_secrets(store, object_prefix="live-test/")
        assert settings.bucket == "bucket-from-keyring"
        assert settings.region == "eu-west-1"
        assert settings.access_key_id == SecretValue("AKIAKEYRING")
        assert settings.secret_access_key == SecretValue("secret-from-keyring")
        assert settings.object_prefix == "live-test/"
        assert settings.control_prefix == "_omnisync/"
        explicit = S3Settings.from_secrets(store, bucket="b2", region="r2")
        assert (explicit.bucket, explicit.region) == ("b2", "r2")
        empty = S3Settings.from_secrets(SecretStore(MemoryKeyring(), environ={}))
        assert (empty.bucket, empty.region) == ("productrepo", "ap-south-1")
        assert empty.access_key_id is None


class TestObjectKey:
    def test_key_layout(self) -> None:
        assert object_key("flatsome", "3.20.9") == "flatsome-3.20.9.zip"
        assert object_key("flatsome", "3.20.9", "live/") == "live/flatsome-3.20.9.zip"

    @pytest.mark.parametrize(
        ("slug", "version"), [("", "1"), ("a", ""), ("a/b", "1"), ("a", "1/2")]
    )
    def test_invalid(self, slug: str, version: str) -> None:
        with pytest.raises(ValueError, match="invalid"):
            object_key(slug, version)


class TestUpload:
    def test_upload_uses_zip_content_type_and_metadata(self, s3: S3Client, tmp_path: Path) -> None:
        """Ported from legacy tests/test_s3.py::TestUpload::test_upload_uses_file_api_with_zip_metadata.

        Metadata is now ``latest-version`` plus ``content-md5-hex`` (FR-PIPE-02).
        """
        path, md5 = _payload(tmp_path, 1000)
        result = s3.upload(path, "flatsome-3.20.9.zip", "3.20.9", md5)
        assert result.uploaded
        assert result.parts == 1
        assert result.size == 1000
        head = s3.raw.head_object(Bucket=BUCKET, Key="flatsome-3.20.9.zip")
        assert head["ContentType"] == "application/zip"
        assert head["Metadata"] == {META_VERSION: "3.20.9", META_MD5: md5}
        assert result.etag == head["ETag"]
        assert _body(s3, "flatsome-3.20.9.zip") == path.read_bytes()

    def test_md5_is_computed_when_not_given(self, s3: S3Client, tmp_path: Path) -> None:
        path, md5 = _payload(tmp_path, 123)
        s3.upload(path, "x-1.zip", "1")
        info = s3.head("x-1.zip")
        assert info is not None
        assert info.md5_hex == md5
        assert info.version == "1"

    def test_exactly_threshold_is_single_put(
        self, s3: S3Client, calls: Calls, tmp_path: Path
    ) -> None:
        path, _ = _payload(tmp_path, MULTIPART_THRESHOLD)
        result = s3.upload(path, "big-1.zip", "1")
        assert result.parts == 1
        assert calls.count("PutObject") == 1
        assert calls.count("CreateMultipartUpload") == 0

    def test_above_threshold_uses_8_mib_parts(
        self, s3: S3Client, calls: Calls, tmp_path: Path
    ) -> None:
        size = 2 * PART_SIZE + 5
        path, md5 = _payload(tmp_path, size)
        gate_calls: list[int] = []
        result = s3.upload(path, "big-2.zip", "2.0", md5, before_write=lambda: gate_calls.append(1))
        assert result.parts == 3
        assert result.size == size
        assert calls.count("PutObject") == 0
        assert calls.count("CreateMultipartUpload") == 1
        assert calls.count("UploadPart") == 3
        assert calls.count("CompleteMultipartUpload") == 1
        # create + 3 parts + complete: the write deadline is checked before each call.
        assert len(gate_calls) == 5
        info = s3.head("big-2.zip")
        assert info is not None
        assert (info.size, info.version, info.md5_hex) == (size, "2.0", md5)
        assert _body(s3, "big-2.zip") == path.read_bytes()

    def test_gate_failure_aborts_multipart(
        self, s3: S3Client, calls: Calls, tmp_path: Path
    ) -> None:
        path, _ = _payload(tmp_path, PART_SIZE + 1)
        seen = {"n": 0}

        class LeaseLost(Exception):
            pass

        def gate() -> None:
            seen["n"] += 1
            if seen["n"] == 3:  # before the second part
                raise LeaseLost

        with pytest.raises(LeaseLost):
            s3.upload(path, "big-3.zip", "3", before_write=gate)
        assert calls.count("UploadPart") == 1
        assert calls.count("AbortMultipartUpload") == 1
        assert calls.count("CompleteMultipartUpload") == 0
        assert s3.head("big-3.zip") is None
        assert "Uploads" not in s3.raw.list_multipart_uploads(Bucket=BUCKET)

    def test_gate_failure_before_single_put_sends_nothing(
        self, s3: S3Client, calls: Calls, tmp_path: Path
    ) -> None:
        path, _ = _payload(tmp_path, 10)

        def gate() -> None:
            raise TimeoutError("write deadline passed")

        with pytest.raises(ServiceError) as info:
            s3.upload(path, "small-1.zip", "1", before_write=gate)
        assert info.value.code == S3_UPLOAD_FAILED
        assert calls.count("PutObject") == 0

    def test_transport_error_during_multipart_aborts(
        self, s3: S3Client, calls: Calls, tmp_path: Path
    ) -> None:
        path, _ = _payload(tmp_path, PART_SIZE + 1)

        def fail_part(**_: Any) -> None:
            raise EndpointConnectionError(endpoint_url="https://s3.example")

        s3.raw.meta.events.register("before-call.s3.UploadPart", fail_part)
        with pytest.raises(ServiceError) as info:
            s3.upload(path, "big-4.zip", "4")
        assert info.value.code == S3_UPLOAD_FAILED
        assert info.value.retryable is True
        assert calls.count("AbortMultipartUpload") == 1

    def test_put_failure_maps_to_upload_failed(
        self, stubbed: tuple[S3Client, Stubber], tmp_path: Path
    ) -> None:
        client, stub = stubbed
        stub.add_client_error("put_object", "InternalError", http_status_code=500)
        path, md5 = _payload(tmp_path, 10)
        with pytest.raises(ServiceError) as info:
            client.upload(path, "k.zip", "1", md5)
        assert (info.value.code, info.value.retryable) == (S3_UPLOAD_FAILED, True)

    def test_put_403_maps_to_access_denied(
        self, stubbed: tuple[S3Client, Stubber], tmp_path: Path
    ) -> None:
        client, stub = stubbed
        stub.add_client_error("put_object", "AccessDenied", http_status_code=403)
        path, md5 = _payload(tmp_path, 10)
        with pytest.raises(ServiceError) as info:
            client.upload(path, "k.zip", "1", md5)
        assert info.value.code == S3_ACCESS_DENIED

    def test_create_multipart_failure(
        self, stubbed: tuple[S3Client, Stubber], tmp_path: Path
    ) -> None:
        client, stub = stubbed
        stub.add_client_error("create_multipart_upload", "SlowDown", http_status_code=503)
        path, md5 = _payload(tmp_path, PART_SIZE + 1)
        with pytest.raises(ServiceError) as info:
            client.upload(path, "k.zip", "1", md5)
        assert (info.value.code, info.value.retryable) == (S3_UPLOAD_FAILED, True)

    def test_abort_failure_is_logged_not_raised(
        self,
        stubbed: tuple[S3Client, Stubber],
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        client, stub = stubbed
        stub.add_response("create_multipart_upload", {"UploadId": "u-1"})
        stub.add_client_error("upload_part", "InternalError", http_status_code=500)
        stub.add_client_error("abort_multipart_upload", "InternalError", http_status_code=500)
        path, md5 = _payload(tmp_path, PART_SIZE + 1)
        with caplog.at_level(logging.WARNING), pytest.raises(ServiceError) as info:
            client.upload(path, "k.zip", "1", md5)
        assert info.value.code == S3_UPLOAD_FAILED
        assert "abort multipart failed" in caplog.text


class TestHead:
    def test_missing_object_returns_none(self, s3: S3Client) -> None:
        """Ported from legacy tests/test_s3.py::TestExists::test_missing_object_returns_false."""
        assert s3.head("nope.zip") is None
        assert s3.exists("nope.zip") is False

    def test_present_object(self, s3: S3Client) -> None:
        """Ported from legacy tests/test_s3.py::TestExists::test_present_object_returns_true."""
        s3.raw.put_object(Bucket=BUCKET, Key="a.zip", Body=b"x")
        assert s3.exists("a.zip") is True

    def test_legacy_version_metadata_is_read(self, s3: S3Client) -> None:
        s3.raw.put_object(
            Bucket=BUCKET, Key="old.zip", Body=b"x", Metadata={LEGACY_META_VERSION: "1.2"}
        )
        info = s3.head("old.zip")
        assert info is not None
        assert info.version == "1.2"
        assert info.md5_hex is None

    @pytest.mark.behaviour_change("D-04")
    def test_unknown_error_raises(self, stubbed: tuple[S3Client, Stubber]) -> None:
        """Ported from legacy tests/test_s3.py::TestExists::test_unknown_error_returns_false.

        Legacy returned False ("missing"); now any non-404 error raises ``s3.error``.
        """
        client, stub = stubbed
        stub.add_client_error("head_object", "503", http_status_code=503)
        with pytest.raises(ServiceError) as info:
            client.exists("a.zip")
        assert (info.value.code, info.value.retryable) == (S3_ERROR, True)

    @pytest.mark.behaviour_change("D-04")
    def test_expired_token_is_access_denied(self, stubbed: tuple[S3Client, Stubber]) -> None:
        client, stub = stubbed
        stub.add_client_error("head_object", "ExpiredToken", http_status_code=400)
        with pytest.raises(ServiceError) as info:
            client.head("a.zip")
        assert (info.value.code, info.value.retryable) == (S3_ACCESS_DENIED, False)

    def test_connection_error_is_retryable(self, s3: S3Client) -> None:
        def fail(**_: Any) -> None:
            raise EndpointConnectionError(endpoint_url="https://s3.example")

        s3.raw.meta.events.register("before-call.s3.HeadObject", fail)
        with pytest.raises(ServiceError) as info:
            s3.head("a.zip")
        assert (info.value.code, info.value.retryable) == (S3_ERROR, True)
        assert "EndpointConnectionError" in info.value.message

    def test_missing_credentials_is_access_denied(self) -> None:
        err = s3_module._translate(NoCredentialsError(), "HEAD k")
        assert (err.code, err.retryable) == (S3_ACCESS_DENIED, False)


@pytest.mark.behaviour_change("D-04")
def test_d04_head_403_fails_with_access_denied_and_zero_uploads(
    stubbed: tuple[S3Client, Stubber], tmp_path: Path
) -> None:
    """D-04 regression (09-TESTING-STRATEGY 5): HEAD 403 -> ``s3.access_denied``, no PUT."""
    client, stub = stubbed
    recorder = Calls()
    client.raw.meta.events.register("provide-client-params.s3", recorder)
    stub.add_client_error("head_object", "403", "Forbidden", http_status_code=403)
    path, md5 = _payload(tmp_path, PART_SIZE + 1)
    with pytest.raises(ServiceError) as info:
        client.upload_if_changed(path, "flatsome-3.20.9.zip", "3.20.9", md5)
    assert info.value.code == S3_ACCESS_DENIED
    assert info.value.retryable is False
    assert recorder.names == ["HeadObject"]


class TestDedupe:
    def test_same_md5_skips(self, s3: S3Client, calls: Calls, tmp_path: Path) -> None:
        path, md5 = _payload(tmp_path, 50)
        first = s3.upload_if_changed(path, "d-1.zip", "1", md5)
        second = s3.upload_if_changed(path, "d-1.zip", "1", md5.upper())
        assert first.uploaded
        assert not second.uploaded
        assert second.parts == 0
        assert second.etag is None
        assert calls.count("PutObject") == 1

    def test_different_md5_uploads(self, s3: S3Client, calls: Calls, tmp_path: Path) -> None:
        path, md5 = _payload(tmp_path, 50)
        s3.upload(path, "d-2.zip", "1", md5)
        path.write_bytes(b"changed")
        result = s3.upload_if_changed(path, "d-2.zip", "1")
        assert result.uploaded
        assert calls.count("PutObject") == 2

    def test_object_without_md5_metadata_is_reuploaded(self, s3: S3Client, tmp_path: Path) -> None:
        s3.raw.put_object(Bucket=BUCKET, Key="d-3.zip", Body=b"legacy")
        path, md5 = _payload(tmp_path, 10)
        assert not s3.is_duplicate("d-3.zip", md5)
        assert s3.upload_if_changed(path, "d-3.zip", "1", md5).uploaded


class TestListAndDelete:
    def test_delete_object(self, s3: S3Client) -> None:
        """Ported from legacy tests/test_s3.py::TestDeleteAndList::test_delete_object."""
        s3.raw.put_object(Bucket=BUCKET, Key="old.zip", Body=b"x")
        gate: list[int] = []
        s3.delete("old.zip", before_write=lambda: gate.append(1))
        assert s3.head("old.zip") is None
        assert gate == [1]

    def test_list_keys_pages(self, stubbed: tuple[S3Client, Stubber]) -> None:
        """Ported from legacy tests/test_s3.py::TestDeleteAndList::test_list_keys_pages."""
        client, stub = stubbed
        stub.add_response(
            "list_objects_v2",
            {
                "Contents": [{"Key": "a.zip", "Size": 1}, {"Key": "b.zip", "Size": 2}],
                "IsTruncated": True,
                "NextContinuationToken": "t1",
            },
            {"Bucket": BUCKET, "Prefix": ""},
        )
        stub.add_response(
            "list_objects_v2",
            {"Contents": [{"Key": "c.zip", "Size": 3}], "IsTruncated": False},
            {"Bucket": BUCKET, "Prefix": "", "ContinuationToken": "t1"},
        )
        assert client.list_keys() == ["a.zip", "b.zip", "c.zip"]

    def test_list_under_prefix(self, s3: S3Client) -> None:
        for key in ("_omnisync/control/lease.json", "_omnisync/config/shared.json", "a.zip"):
            s3.raw.put_object(Bucket=BUCKET, Key=key, Body=b"{}")
        objects = s3.list_objects("_omnisync/")
        assert sorted(o.key for o in objects) == [
            "_omnisync/config/shared.json",
            "_omnisync/control/lease.json",
        ]
        assert all(o.size == 2 and o.etag and o.last_modified for o in objects)
        assert s3.list_keys("missing/") == []

    def test_list_error(self, stubbed: tuple[S3Client, Stubber]) -> None:
        client, stub = stubbed
        stub.add_client_error("list_objects_v2", "AccessDenied", http_status_code=403)
        with pytest.raises(ServiceError) as info:
            client.list_keys()
        assert info.value.code == S3_ACCESS_DENIED

    def test_delete_many(self, s3: S3Client, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(s3_module, "DELETE_BATCH", 2)
        keys = [f"k{i}.zip" for i in range(5)]
        for key in keys:
            s3.raw.put_object(Bucket=BUCKET, Key=key, Body=b"x")
        s3.raw.put_object(Bucket=BUCKET, Key="keep.zip", Body=b"x")
        gate: list[int] = []
        assert s3.delete_many([*keys, keys[0]], before_write=lambda: gate.append(1)) == 5
        assert s3.list_keys() == ["keep.zip"]
        assert len(gate) == 3
        assert s3.delete_many([]) == 0

    def test_delete_403(self, stubbed: tuple[S3Client, Stubber]) -> None:
        client, stub = stubbed
        stub.add_client_error("delete_object", "AccessDenied", http_status_code=403)
        with pytest.raises(ServiceError) as info:
            client.delete("x.zip")
        assert info.value.code == S3_ACCESS_DENIED

    @pytest.mark.parametrize(
        ("code", "expected", "retryable"),
        [("AccessDenied", S3_ACCESS_DENIED, False), ("SlowDown", S3_ERROR, True)],
    )
    def test_delete_many_per_key_errors(
        self, stubbed: tuple[S3Client, Stubber], code: str, expected: str, retryable: bool
    ) -> None:
        client, stub = stubbed
        stub.add_response(
            "delete_objects", {"Errors": [{"Key": "a.zip", "Code": code, "Message": "no"}]}
        )
        with pytest.raises(ServiceError) as info:
            client.delete_many(["a.zip"])
        assert (info.value.code, info.value.retryable) == (expected, retryable)

    def test_delete_many_call_error(self, stubbed: tuple[S3Client, Stubber]) -> None:
        client, stub = stubbed
        stub.add_client_error("delete_objects", "InternalError", http_status_code=500)
        with pytest.raises(ServiceError) as info:
            client.delete_many(["a.zip"])
        assert (info.value.code, info.value.retryable) == (S3_ERROR, True)


class TestJsonDocuments:
    KEY = "_omnisync/config/shared.json"

    def test_create_read_update_with_etags(self, s3: S3Client) -> None:
        etag1 = s3.put_json(self.KEY, {"version": 1, "name": "Zürich"}, if_none_match=True)
        doc = s3.get_json(self.KEY)
        assert doc is not None
        data, etag = doc
        assert data == {"version": 1, "name": "Zürich"}
        assert etag == etag1 == doc.etag
        assert doc.data == data
        etag2 = s3.put_json(self.KEY, {"version": 2}, if_match=etag1)
        assert etag2 != etag1
        assert s3.get_json(self.KEY) == ({"version": 2}, etag2)

    def test_if_none_match_conflict(self, s3: S3Client) -> None:
        s3.put_json(self.KEY, {"v": 1}, if_none_match=True)
        with pytest.raises(PreconditionFailed) as info:
            s3.put_json(self.KEY, {"v": 2}, if_none_match=True)
        assert info.value.code == S3_PRECONDITION_FAILED
        assert info.value.retryable is False
        assert s3.get_json(self.KEY) is not None
        assert s3.get_json(self.KEY)[0] == {"v": 1}  # type: ignore[index]

    def test_stale_if_match_conflict(self, s3: S3Client) -> None:
        etag1 = s3.put_json(self.KEY, {"v": 1})
        s3.put_json(self.KEY, {"v": 2}, if_match=etag1)
        with pytest.raises(PreconditionFailed):
            s3.put_json(self.KEY, {"v": 3}, if_match=etag1)

    def test_conditional_conflict_409(self, stubbed: tuple[S3Client, Stubber]) -> None:
        client, stub = stubbed
        stub.add_client_error("put_object", "ConditionalRequestConflict", http_status_code=409)
        with pytest.raises(PreconditionFailed):
            client.put_json(self.KEY, {}, if_match='"e"')

    def test_put_error(self, stubbed: tuple[S3Client, Stubber]) -> None:
        client, stub = stubbed
        stub.add_client_error("put_object", "AccessDenied", http_status_code=403)
        with pytest.raises(ServiceError) as info:
            client.put_json(self.KEY, {})
        assert info.value.code == S3_ACCESS_DENIED

    def test_gate_runs_before_put(self, s3: S3Client, calls: Calls) -> None:
        def gate() -> None:
            raise RuntimeError("deadline")

        with pytest.raises(RuntimeError):
            s3.put_json(self.KEY, {}, before_write=gate)
        assert calls.count("PutObject") == 0

    def test_mutually_exclusive_conditions(self, s3: S3Client) -> None:
        with pytest.raises(ValueError, match="mutually exclusive"):
            s3.put_json(self.KEY, {}, if_match='"x"', if_none_match=True)

    def test_missing_document(self, s3: S3Client) -> None:
        assert s3.get_json("_omnisync/control/lease.json") is None

    def test_invalid_json(self, s3: S3Client) -> None:
        s3.raw.put_object(Bucket=BUCKET, Key=self.KEY, Body=b"\xff not json")
        with pytest.raises(ServiceError) as info:
            s3.get_json(self.KEY)
        assert info.value.code == S3_DOCUMENT_INVALID

    def test_too_large(self, s3: S3Client, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(s3_module, "MAX_JSON_BYTES", 4)
        s3.raw.put_object(Bucket=BUCKET, Key=self.KEY, Body=b'{"a": 1}')
        with pytest.raises(ServiceError) as info:
            s3.get_json(self.KEY)
        assert info.value.code == S3_DOCUMENT_INVALID

    def test_get_403(self, stubbed: tuple[S3Client, Stubber]) -> None:
        client, stub = stubbed
        stub.add_client_error("get_object", "AccessDenied", http_status_code=403)
        with pytest.raises(ServiceError) as info:
            client.get_json(self.KEY)
        assert info.value.code == S3_ACCESS_DENIED

    def test_get_transport_error(self, s3: S3Client) -> None:
        def fail(**_: Any) -> None:
            raise EndpointConnectionError(endpoint_url="https://s3.example")

        s3.raw.meta.events.register("before-call.s3.GetObject", fail)
        with pytest.raises(ServiceError) as info:
            s3.get_json(self.KEY)
        assert (info.value.code, info.value.retryable) == (S3_ERROR, True)

    def test_body_read_error(self) -> None:
        class Body:
            closed = False

            def read(self, _n: int) -> bytes:
                raise OSError("connection reset")

            def close(self) -> None:
                self.closed = True

        body = Body()

        class Raw:
            def get_object(self, **_: Any) -> dict[str, Any]:
                return {"Body": body, "ETag": '"e"'}

        client = S3Client(_settings(), client=Raw())
        with pytest.raises(ServiceError) as info:
            client.get_json(self.KEY)
        assert info.value.code == S3_ERROR
        assert body.closed


class TestCoordinationProtocol:
    """``S3Client`` as the ``JsonObjectStore`` / ``ServerDateSource`` of services/object_store.py."""

    KEY = "_omnisync/control/lease.json"

    def test_precondition_failed_is_the_canonical_type(self, s3: S3Client) -> None:
        s3.put_json(self.KEY, {"v": 1}, if_none_match=True)
        with pytest.raises(object_store.PreconditionFailed):
            s3.put_json(self.KEY, {"v": 2}, if_none_match=True)

    def test_put_if_match_on_missing_object(self, s3: S3Client) -> None:
        with pytest.raises(PreconditionFailed):
            s3.put_json(self.KEY, {"v": 1}, if_match='"gone"')

    def test_get_non_object_document(self, s3: S3Client) -> None:
        s3.raw.put_object(Bucket=BUCKET, Key=self.KEY, Body=b"[1, 2]")
        with pytest.raises(ServiceError) as info:
            s3.get_json(self.KEY)
        assert info.value.code == S3_DOCUMENT_INVALID

    def test_conditional_delete(self, s3: S3Client) -> None:
        etag = s3.put_json(self.KEY, {"v": 1})
        with pytest.raises(PreconditionFailed):
            s3.delete(self.KEY, if_match='"other"')
        assert s3.head(self.KEY) is not None
        s3.delete(self.KEY, if_match=etag)
        assert s3.head(self.KEY) is None
        s3.delete(self.KEY)  # missing: no-op
        with pytest.raises(PreconditionFailed):
            s3.delete(self.KEY, if_match=etag)

    def test_unconditional_delete_404_is_noop(self, stubbed: tuple[S3Client, Stubber]) -> None:
        client, stub = stubbed
        stub.add_client_error("delete_object", "NoSuchKey", http_status_code=404)
        client.delete(self.KEY)

    def test_delete_transport_error(self, s3: S3Client) -> None:
        def fail(**_: Any) -> None:
            raise EndpointConnectionError(endpoint_url="https://s3.example")

        s3.raw.meta.events.register("before-call.s3.DeleteObject", fail)
        with pytest.raises(ServiceError) as info:
            s3.delete(self.KEY)
        assert (info.value.code, info.value.retryable) == (S3_ERROR, True)

    def test_server_date_from_head(self, stubbed: tuple[S3Client, Stubber]) -> None:
        client, stub = stubbed
        stub.add_response(
            "head_object",
            {"ResponseMetadata": {"HTTPHeaders": {"date": "Sun, 11 Oct 2026 10:00:00 GMT"}}},
            {"Bucket": BUCKET, "Key": self.KEY},
        )
        assert client.server_date() == 1791712800.0

    def test_server_date_from_404(self, stubbed: tuple[S3Client, Stubber]) -> None:
        client, stub = stubbed
        stub.add_client_error(
            "head_object",
            "404",
            http_status_code=404,
            response_meta={"HTTPHeaders": {"date": "Sun, 11 Oct 2026 10:00:01 GMT"}},
        )
        assert client.server_date() == 1791712801.0

    def test_server_date_missing_header(self, stubbed: tuple[S3Client, Stubber]) -> None:
        client, stub = stubbed
        stub.add_client_error("head_object", "404", http_status_code=404)
        with pytest.raises(ServiceError) as info:
            client.server_date()
        assert (info.value.code, info.value.retryable) == (S3_ERROR, True)

    def test_server_date_403(self, stubbed: tuple[S3Client, Stubber]) -> None:
        client, stub = stubbed
        stub.add_client_error("head_object", "403", http_status_code=403)
        with pytest.raises(ServiceError) as info:
            client.server_date()
        assert info.value.code == S3_ACCESS_DENIED

    def test_put_transport_error_is_retryable(self, s3: S3Client) -> None:
        def fail(**_: Any) -> None:
            raise EndpointConnectionError(endpoint_url="https://s3.example")

        s3.raw.meta.events.register("before-call.s3.PutObject", fail)
        with pytest.raises(ServiceError) as info:
            s3.put_json(self.KEY, {"v": 1}, if_match='"e"')
        assert not isinstance(info.value, PreconditionFailed)
        assert (info.value.code, info.value.retryable) == (S3_ERROR, True)

    def test_server_date_transport_error(self, s3: S3Client) -> None:
        def fail(**_: Any) -> None:
            raise EndpointConnectionError(endpoint_url="https://s3.example")

        s3.raw.meta.events.register("before-call.s3.HeadObject", fail)
        with pytest.raises(ServiceError) as info:
            s3.server_date()
        assert info.value.retryable is True


def test_service_error_repr() -> None:
    err = ServiceError("s3.error", "boom", retryable=True)
    assert repr(err) == "ServiceError(code='s3.error', message='boom', retryable=True)"
    assert str(err) == "s3.error: boom"
