# OmniSync backend

Python 3.12 package for the OmniSync 2.0 server (FastAPI, SQLite). Design: [03-ARCHITECTURE](../docs/03-ARCHITECTURE.md), [04-DATA-MODEL](../docs/04-DATA-MODEL.md), [05-API-SPEC](../docs/05-API-SPEC.md), [06-SECURITY](../docs/06-SECURITY.md).

## Commands

Run from this folder. `python -m <tool>` is used where Windows application control may block generated `.exe` launchers ([ADR-0014](../docs/adr/0014-milestone-3-implementation-adjustments.md)).

| Command | What it does |
|---|---|
| `uv sync` | Install exactly the locked dependencies into `.venv` |
| `uv run python -m omnisync serve` | Run the server on <http://127.0.0.1:8765> |
| `uv run python -m omnisync create-admin <name>` | Create an admin from the command line (prompts for the password) |
| `uv run python -m omnisync reset-password <name>` | Reset a password and sign out that user's sessions |
| `uv run python -m pytest` | Tests with the coverage gate (80% overall) |
| `uv run ruff check src tests scripts` and `uv run ruff format --check src tests scripts` | Lint and format check |
| `uv run python -m mypy` | Strict type checking |
| `uv run python -c "from importlinter.cli import lint_imports_command; lint_imports_command()"` | Architecture layer contract ([03-ARCHITECTURE §4](../docs/03-ARCHITECTURE.md#4-components-c4-level-3-backend)) |
| `uv run pip-audit --skip-editable` | Dependency vulnerability audit |
| `uv run python scripts/export_openapi.py <file>` | Write the OpenAPI schema (used by `npm run api:generate`) |

## Configuration

Environment variables (`OMNISYNC_DATA_DIR`, `OMNISYNC_PORT`, `OMNISYNC_INSTANCE_NAME`, `OMNISYNC_LOG_LEVEL`, `OMNISYNC_MAX_UPLOAD_BYTES`, `OMNISYNC_STATIC_DIR`) or `<data dir>/omnisync.toml`. See [10-OPERATIONS §3](../docs/10-OPERATIONS.md#3-configuration-reference).

## Layout

```text
src/omnisync/
  api/        FastAPI app, middleware (Host check, CSP, request IDs), routers, SPA serving
  db/         SQLAlchemy models, engine (WAL), Alembic migrations
  security/   passwords, tokens, permissions, rate limits, instance lock
  services/   accounts, audit, instance identity
  cli.py      command-line interface
tests/        pytest suite (API, security, database, CLI, permission matrix)
```
