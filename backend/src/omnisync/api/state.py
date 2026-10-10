"""Process-wide application state shared by request handlers."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from omnisync.clock import Clock
from omnisync.security.ratelimit import RateLimiter
from omnisync.security.tokens import SetupToken
from omnisync.settings import Settings


@dataclass
class AppState:
    settings: Settings
    clock: Clock
    engine: Engine
    db_factory: sessionmaker[Session]
    rate_limiter: RateLimiter
    setup_token: SetupToken
    instance_id: str = ""
    instance_name: str = ""
    index_html: str | None = field(default=None, repr=False)
