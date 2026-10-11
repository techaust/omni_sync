# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Milestone 3 foundation (backend):**
  - FastAPI server with Problem Details errors and a pure-ASGI security middleware: Host allow-list (421), CSP with per-request nonce, security headers, request IDs.
  - SQLite (WAL, `synchronous=FULL`) with an Alembic baseline and an append-only audit log enforced by triggers.
  - argon2id password hashing and policy; first-run setup token; server-side sessions (12 h idle, 7 d absolute); lockout; CSRF and Origin checks.
  - Role permissions with a permission-matrix test; users API; JSON logging with secret redaction.
  - CLI: `serve`, `create-admin`, `reset-password` (also `python -m omnisync`); OS instance lock.
- **Milestone 3 foundation (front end):**
  - React 19 + TypeScript app with shadcn/ui (Nova preset), the measured zinc + indigo tokens, Inter and JetBrains Mono, light/dark/system modes rendered before first paint.
  - Setup, sign-in, dashboard (health), account (password, appearance) and user-management screens.
  - Typed API client generated from OpenAPI.
- **Tests and CI:**
  - 91 backend tests (94% branch coverage), 97 front-end tests including 74 WCAG contrast checks, and 15 end-to-end test runs (5 scenarios in Chromium, Firefox and WebKit) with axe and CSP-violation detection.
  - GitHub Actions for docs, backend (Ubuntu and Windows), front end, end-to-end (Chromium, Firefox, WebKit) and gitleaks.
- `THIRD-PARTY-NOTICES.md` and bundled licence texts for fonts and the password list.
- ADR-0014 recording the implementation adjustments discovered while building Milestone 3, and ADR-0015 allowing inline style attributes in the CSP (WebKit).

### Fixed

- Request transactions are committed before the HTTP response is sent. FastAPI ran `yield`-dependency cleanup after the response by default, so a fast follow-up read could miss a write and a failed commit could be reported as success. Found by the WebKit end-to-end run; covered by `backend/tests/test_transactions.py`.

- Discovery report on the legacy OmniSync 1.1.0 (`docs/01-DISCOVERY-REPORT.md`).
- Design specification for OmniSync 2.0: product requirements, architecture, data model, API, security and threat model, AI design, UX design, testing strategy, operations guide, roadmap with traceability matrix, glossary, and ADRs 0001–0013 (including the shadcn/ui + indigo design system with measured contrast ratios).
- Documentation standard with measurable criteria, and `scripts/check_docs.py`, which verifies links, anchors, identifiers, traceability, threat-to-control coverage, legacy citations, diagram descriptions, heading structure and ADR completeness.
- Project governance files: `README.md`, `CONTRIBUTING.md`, `SECURITY.md`, `LICENSE` (GPL-3.0).
