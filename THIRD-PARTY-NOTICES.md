# Third-Party Notices

OmniSync is licensed under GPL-3.0-only ([LICENSE](LICENSE)). It includes or depends on the third-party components below. Their licences are all compatible with GPL-3.0 ([ADR-0011](docs/adr/0011-license-gpl-3.md)).

The versions are those locked on 2026-10-11 (`backend/uv.lock`, `frontend/package-lock.json`). Licences were read from each package's installed metadata. Update this file whenever a direct dependency is added or replaced ([CONTRIBUTING](CONTRIBUTING.md#dependencies)).

## Bundled in this repository or in the built application

| Component | Used for | Licence | Licence text |
|---|---|---|---|
| shadcn/ui components and utilities (copied into `frontend/src/components/ui`, `frontend/src/styles/shadcn.css`) | UI components | MIT | <https://github.com/shadcn-ui/ui/blob/main/LICENSE.md> |
| Inter (via `@fontsource-variable/inter` 5.3.0) | UI typeface, shipped in the build | SIL Open Font License 1.1 | [frontend/public/licenses/Inter-OFL-1.1.txt](frontend/public/licenses/Inter-OFL-1.1.txt) |
| JetBrains Mono (via `@fontsource-variable/jetbrains-mono` 5.3.0) | Monospace typeface, shipped in the build | SIL Open Font License 1.1 | [frontend/public/licenses/JetBrainsMono-OFL-1.1.txt](frontend/public/licenses/JetBrainsMono-OFL-1.1.txt) |
| SecLists "10k most common" password list | Password policy blocklist | MIT, © 2018 Daniel Miessler | [backend/src/omnisync/security/data/common-passwords.LICENSE.txt](backend/src/omnisync/security/data/common-passwords.LICENSE.txt) |

## Front-end runtime dependencies (bundled into the built JavaScript)

| Package | Version | Licence |
|---|---|---|
| react, react-dom | 19.3.0 | MIT |
| react-router | 8.4.0 | MIT |
| @tanstack/react-query | 5.104.1 | MIT |
| radix-ui | 1.7.0 | MIT |
| lucide-react | 1.55.0 | ISC |
| sonner | 2.0.8 | MIT |
| class-variance-authority | 0.7.1 | Apache-2.0 |
| cn | 0.4.0 | MIT |
| get-nonce | 1.0.1 | MIT |

## Backend runtime dependencies

| Package | Version | Licence |
|---|---|---|
| fastapi | 0.143.0 | MIT |
| starlette | 1.7.0 | BSD-3-Clause |
| uvicorn | 0.54.0 | BSD-3-Clause |
| sqlalchemy | 2.1.4 | MIT |
| alembic | 1.20.0 | MIT |
| pydantic, pydantic-core, pydantic-settings | 2.14.0, 2.50.0, 2.15.0 | MIT |
| argon2-cffi, argon2-cffi-bindings | 25.1.0, 26.1.0 | MIT |
| anyio, annotated-types, annotated-doc, typing-inspection, mako | various | MIT |
| click, h11, idna, markupsafe, pycparser, python-dotenv | various | BSD-3-Clause / MIT |
| cffi | 2.1.1 | MIT-0 |
| typing-extensions | 4.16.0 | PSF-2.0 |
| opentelemetry-api (pulled in by FastAPI) | 1.45.1 | Apache-2.0 |

Development-only tools (test runners, linters, type checkers, build tools) are not distributed with the application and are not listed here.
