# OmniSync 2.0

**Publish WordPress plugin and theme updates to every WooCommerce store from one browser upload: uploaded to S3 once, linked everywhere, with AI help for the hard cases.**

> **Project status: Milestone 3 (foundation) complete; Milestone 4a next.** The design is approved. What exists today is the application foundation: the server, database, sign-in, users and roles, the browser shell, and the test and CI pipeline. **No publishing features exist yet.** Everything under *What it will do* is planned, so keep using the legacy tool at `../OmniSync` for publishing until release. Progress is tracked in the [roadmap](docs/11-ROADMAP.md).

## What it will do

You drop vendor ZIP files into the browser. For each one, OmniSync:

1. identifies the WordPress installable and its version (plugins, themes, Envato wrappers, nested bundles);
2. matches it to the right product in every configured store;
3. uploads it **once** to S3 as `<slug>-<version>.zip`;
4. points each store's download button at that object, then verifies the change;
5. removes the superseded object when nothing references it any more.

Anything it cannot decide safely becomes a clear question in a **Needs input** queue instead of a silent error:

- an unknown product, with ranked suggestions
- a missing button name
- an older version than the one already live
- an ambiguous download entry

An optional AI assistant ranks candidates, explains failures and answers questions about your runs. It can propose actions, but you approve every change.

| Area | Planned capability |
|---|---|
| Inbox | Drag-and-drop upload, watched folder, automatic inspection with per-store match preview |
| Runs | Dry-run plan, then confirm a live run; live progress; history; export |
| Safety | Plan confirmation, downgrade holds, reference-checked deletion, typed confirmation for cleanup, append-only audit log |
| Two PCs | Owner and colleague each run their own instance, coordinated through an S3 lease and shared rules |
| AI | Unmapped-archive resolver, button-name suggestions, run triage, assistant with approval-gated tools, budgets |
| Operations | Health page, backups and restore, legacy import, Windows auto-start |

## Documentation

Start with the **[documentation index](docs/README.md)**. Key documents:

| If you want to… | Read |
|---|---|
| Understand the legacy tool and why it is being rebuilt | [01 – Discovery report](docs/01-DISCOVERY-REPORT.md) |
| Know exactly what 2.0 must do | [02 – Product requirements](docs/02-PRODUCT-REQUIREMENTS.md) |
| Understand how it is built | [03 – Architecture](docs/03-ARCHITECTURE.md) and [ADRs](docs/adr/README.md) |
| Review security | [06 – Security and threat model](docs/06-SECURITY.md) |
| Review the AI design | [07 – AI design](docs/07-AI-DESIGN.md) |
| Install and operate it (after release) | [10 – Operations guide](docs/10-OPERATIONS.md) |
| See progress and what is next | [11 – Roadmap and traceability](docs/11-ROADMAP.md) |

## Technology

Python 3.12 · FastAPI · SQLite · React 19 + TypeScript · Tailwind CSS · shadcn/ui on Radix UI (zinc + indigo theme, light/dark) · Anthropic / OpenAI (optional, planned). The reasons are in [03-ARCHITECTURE §10](docs/03-ARCHITECTURE.md#10-technology-choices) and [ADR-0014](docs/adr/0014-milestone-3-implementation-adjustments.md).

## Developer quick start

Requirements: Python 3.12, [uv](https://docs.astral.sh/uv/), Node.js 24.

```powershell
# 1. Back end: install and run the tests
cd backend
uv sync
uv run python -m pytest

# 2. Front end: install, test and build (the server serves frontend/dist)
cd ../frontend
npm ci
npm test
npm run build

# 3. Run the server on http://127.0.0.1:8765 (prints a one-time setup token on first start)
cd ../backend
uv run python -m omnisync serve
```

Open <http://127.0.0.1:8765>, enter the setup token, and create the first admin. Data is stored in `%LOCALAPPDATA%\OmniSync`; set `OMNISYNC_DATA_DIR` to use another folder. For front-end development with hot reload, keep the server running and use `npm run dev` in `frontend/` (<http://localhost:5173>).

More detail: [backend/README.md](backend/README.md), [frontend/README.md](frontend/README.md).

## Checking the documentation

The documentation is verified automatically (links, anchors, identifiers, traceability, legacy citations):

```bash
python scripts/check_docs.py
npx --yes markdownlint-cli2 "**/*.md"
```

See the [documentation standard](docs/DOCUMENTATION-STANDARD.md) for the criteria.

## Contributing and security

- How to contribute: [CONTRIBUTING.md](CONTRIBUTING.md)
- How to report a vulnerability: [SECURITY.md](SECURITY.md)
- Release history: [CHANGELOG.md](CHANGELOG.md)

## License

GPL-3.0-only. See [LICENSE](LICENSE) and [ADR-0011](docs/adr/0011-license-gpl-3.md). Third-party components and their licences are listed in [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md). Copyright © 2026 TecHaust Technologies.
