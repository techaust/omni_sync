"""Instance configuration (docs/10-OPERATIONS.md section 3).

Precedence: environment variables (``OMNISYNC_*``) > ``<data dir>/omnisync.toml`` > defaults.
The bind host is fixed to 127.0.0.1 (C-04) and is deliberately not configurable.
"""

from __future__ import annotations

import os
import platform
import sys
from functools import cached_property
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

BIND_HOST = "127.0.0.1"
TOML_NAME = "omnisync.toml"


def default_data_dir() -> Path:
    """Per-user data directory, independent of the working directory (04-DATA-MODEL section 4)."""
    current: str = sys.platform  # a plain str keeps every branch type-checked on every OS
    if current == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "OmniSync"
    if current == "darwin":
        return Path.home() / "Library" / "Application Support" / "OmniSync"
    xdg = os.environ.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else Path.home() / ".local" / "share") / "omnisync"


def _default_static_dir() -> Path | None:
    """Built SPA location when running from a source checkout (``frontend/dist``)."""
    candidate = Path(__file__).resolve().parents[3] / "frontend" / "dist"
    return candidate if (candidate / "index.html").is_file() else None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OMNISYNC_", extra="ignore", frozen=True)

    data_dir: Path = Field(default_factory=default_data_dir)
    port: int = Field(default=8765, ge=1024, le=65535)
    instance_name: str = Field(default_factory=lambda: platform.node() or "omnisync", max_length=64)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    max_upload_bytes: int = Field(default=2 * 1024**3, gt=0)
    static_dir: Path | None = Field(default_factory=_default_static_dir)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        data_dir = Path(os.environ.get("OMNISYNC_DATA_DIR") or default_data_dir())
        toml = TomlConfigSettingsSource(settings_cls, toml_file=data_dir / TOML_NAME)
        return (init_settings, env_settings, toml)

    @property
    def host(self) -> str:
        return BIND_HOST

    @cached_property
    def allowed_hosts(self) -> frozenset[str]:
        """Host header values accepted by the DNS-rebinding guard (C-04)."""
        return frozenset({f"{BIND_HOST}:{self.port}", f"localhost:{self.port}"})

    @cached_property
    def allowed_origins(self) -> frozenset[str]:
        return frozenset(f"http://{h}" for h in self.allowed_hosts)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "omnisync.db"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def setup_token_path(self) -> Path:
        return self.data_dir / "setup-token.txt"

    @property
    def instance_lock_path(self) -> Path:
        return self.data_dir / "instance.lock"
