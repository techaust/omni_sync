"""CLI commands (FR-OPS-03, Milestone 3 subset)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from omnisync import cli
from omnisync.api.app import create_app
from omnisync.clock import FakeClock
from omnisync.security.instance_lock import InstanceLock
from omnisync.settings import Settings
from tests.conftest import BASE_URL, login

PASSWORD = "violet-anchor-meadow-42"


def _prompt(*answers: str) -> Callable[[str], str]:
    it: Iterator[str] = iter(answers)
    return lambda _label: next(it)


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "cli-data"
    monkeypatch.setenv("OMNISYNC_DATA_DIR", str(path))
    return path


def test_create_admin_then_login(data_dir: Path) -> None:
    assert (
        cli.main(
            ["create-admin", "boss", "--display-name", "The Boss"],
            prompt=_prompt(PASSWORD, PASSWORD),
        )
        == 0
    )
    settings = Settings(data_dir=data_dir, port=8765, static_dir=None)
    with TestClient(create_app(settings, clock=FakeClock()), base_url=BASE_URL) as client:
        assert client.get("/api/v1/setup/status").json() == {"setup_required": False}
        assert login(client, "boss", PASSWORD).me["display_name"] == "The Boss"


def test_create_admin_rejects_mismatch_and_weak(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["create-admin", "boss"], prompt=_prompt(PASSWORD, PASSWORD + "x")) == 1
    assert "do not match" in capsys.readouterr().err
    assert cli.main(["create-admin", "boss"], prompt=_prompt("short", "short")) == 1
    assert "at least 12" in capsys.readouterr().err


def test_reset_password(data_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cli.main(["create-admin", "boss"], prompt=_prompt(PASSWORD, PASSWORD))
    new = "granite-harbour-lantern-9"
    assert cli.main(["reset-password", "boss"], prompt=_prompt(new, new)) == 0
    assert cli.main(["reset-password", "nobody"], prompt=_prompt(new, new)) == 1
    assert "no user named" in capsys.readouterr().err
    settings = Settings(data_dir=data_dir, port=8765, static_dir=None)
    with TestClient(create_app(settings, clock=FakeClock()), base_url=BASE_URL) as client:
        login(client, "boss", new)


def test_serve_refuses_when_data_dir_in_use(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with InstanceLock(data_dir / "instance.lock"):
        assert cli.main(["serve"]) == 1
    assert "in use" in capsys.readouterr().err


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert "omnisync" in capsys.readouterr().out
