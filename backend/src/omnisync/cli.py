"""Command-line interface (FR-OPS-03). Milestone 3 provides serve, create-admin and
reset-password; the remaining commands arrive with their milestones (docs/11-ROADMAP.md)."""

from __future__ import annotations

import argparse
import getpass
import sys
from collections.abc import Callable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from omnisync import __version__
from omnisync.clock import SystemClock
from omnisync.db.engine import make_engine, make_sessionmaker, transaction
from omnisync.db.migrate import upgrade_to_head
from omnisync.db.models import User
from omnisync.errors import AppError
from omnisync.logging_setup import configure_logging
from omnisync.security.instance_lock import InstanceLock, InstanceLockedError
from omnisync.services import accounts, audit
from omnisync.services.instance import ensure_instance
from omnisync.settings import Settings

EXIT_OK, EXIT_ERROR, EXIT_USAGE = 0, 1, 2

PasswordPrompt = Callable[[str], str]


def _prompt_new_password(prompt: PasswordPrompt) -> str:
    first = prompt("New password: ")
    if first != prompt("Repeat password: "):
        raise AppError("validation.failed", detail="Passwords do not match.")
    return first


def cmd_serve(settings: Settings) -> int:
    import uvicorn

    from omnisync.api.app import create_app

    lock = InstanceLock(settings.instance_lock_path)
    try:
        lock.acquire()
    except InstanceLockedError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    try:
        configure_logging(settings.log_level, settings.logs_dir)
        print(f"Listening on http://{settings.host}:{settings.port}", flush=True)
        uvicorn.run(
            create_app(settings),
            host=settings.host,
            port=settings.port,
            workers=1,  # mandatory: in-process job runner and SQLite (ADR-0004)
            log_config=None,
            server_header=False,
            proxy_headers=False,
        )
    finally:
        lock.release()
    return EXIT_OK


def _with_db(settings: Settings, action: Callable[[sessionmaker[Session]], int]) -> int:
    engine = make_engine(settings.db_path)
    try:
        upgrade_to_head(engine)
        return action(make_sessionmaker(engine))
    finally:
        engine.dispose()


def cmd_create_admin(
    settings: Settings, username: str, display_name: str | None, prompt: PasswordPrompt
) -> int:
    def run(factory: sessionmaker[Session]) -> int:
        password = _prompt_new_password(prompt)
        now = SystemClock().now()
        with transaction(factory) as db:
            instance = ensure_instance(db, settings.instance_name, now)
            user = accounts.create_user(
                db,
                username=username,
                display_name=display_name or username,
                password=password,
                role="admin",
                now=now,
            )
            audit.record(
                db,
                now=now,
                actor="system",
                instance_name=instance.instance_name,
                action="user.created",
                target=user.username,
                details={"role": "admin"},
                via="cli",
            )
        settings.setup_token_path.unlink(missing_ok=True)
        print(f"Created admin '{user.username}'.")
        return EXIT_OK

    return _with_db(settings, run)


def cmd_reset_password(settings: Settings, username: str, prompt: PasswordPrompt) -> int:
    def run(factory: sessionmaker[Session]) -> int:
        password = _prompt_new_password(prompt)
        now = SystemClock().now()
        with transaction(factory) as db:
            user = db.scalar(select(User).where(User.username == username.strip().lower()))
            if user is None:
                print(f"error: no user named '{username}'", file=sys.stderr)
                return EXIT_ERROR
            accounts.update_user(db, user, now=now, new_password=password)
            instance = ensure_instance(db, settings.instance_name, now)
            audit.record(
                db,
                now=now,
                actor="system",
                instance_name=instance.instance_name,
                action="user.updated",
                target=user.username,
                details={"changed": ["password"]},
                via="cli",
            )
        print(f"Password reset for '{username}'; all of their sessions were signed out.")
        return EXIT_OK

    return _with_db(settings, run)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="omnisync", description="OmniSync 2.0 server")
    parser.add_argument("--version", action="version", version=f"omnisync {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve", help="Run the web server on http://127.0.0.1:<port>")
    create = sub.add_parser(
        "create-admin", help="Create an admin account (prompts for the password)"
    )
    create.add_argument("username")
    create.add_argument("--display-name")
    reset = sub.add_parser(
        "reset-password", help="Reset a user's password and sign out their sessions"
    )
    reset.add_argument("username")
    return parser


def main(argv: Sequence[str] | None = None, prompt: PasswordPrompt = getpass.getpass) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings()
    try:
        if args.command == "serve":
            return cmd_serve(settings)
        if args.command == "create-admin":
            return cmd_create_admin(settings, args.username, args.display_name, prompt)
        if args.command == "reset-password":
            return cmd_reset_password(settings, args.username, prompt)
    except AppError as exc:
        print(f"error: {exc.detail or exc.title}", file=sys.stderr)
        return EXIT_ERROR
    except (KeyboardInterrupt, EOFError):
        print("aborted", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_USAGE  # pragma: no cover - argparse enforces a command


if __name__ == "__main__":
    sys.exit(main())
