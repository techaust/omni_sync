# Roadmap, Living Plan and Traceability

> **Status:** Accepted (living document; updated at the end of every milestone) · **Owner:** TecHaust Technologies (@techaust) · **Last reviewed:** 2026-10-11 · **Audience:** everyone

## 1. Milestones

| M | Name | Deliverables | Exit criteria | Status |
|---|---|---|---|---|
| 1 | Discovery | [01-DISCOVERY-REPORT](01-DISCOVERY-REPORT.md) | Report reviewed; decisions recorded | **Done** (2026-10-11) |
| 2 | Product and architecture design | Documents 02–10, ADRs 0001–0013 (0014 and 0015 added in M3), glossary, documentation standard and checker | `scripts/check_docs.py` and markdownlint clean; owner approval | **Done** (approved by the owner 2026-10-11) |
| 3 | Foundation | Repo layout (`backend/`, `frontend/`), uv and npm lockfiles, lint/type/test tooling, CI workflow, SQLite + Alembic baseline, settings, auth (setup, login, sessions, CSRF, Host check), SPA shell with navigation and design tokens, docs check in CI | CI green; login E2E passes; permission-matrix test skeleton runs | **Done** (2026-10-11): [CI run 38120836246](https://github.com/techaust/omni_sync/actions/runs/38120836246) green. Backend 91 tests, 94.3% branch coverage on Ubuntu and Windows; front end 97 tests including 74 WCAG contrast checks; end-to-end 15 passed (5 scenarios × Chromium, Firefox, WebKit) with zero serious axe and zero CSP violations; full permission matrix (12 endpoints × 3 roles + anonymous) |
| 4a | Domain port | `core/` archive, versions, slugs, matching, catalog, shortcodes; legacy tests ported; parity harness | Ported tests pass; differential inspection matches on the corpus | Not started |
| 4b | Services and runner | S3, WooCommerce, secrets, lease, shared config, job runner, pipeline, catalog refresh, cleanup, legacy import; D-01…D-05 fixed | Integration suite green; differential planning matches; chaos tests pass | Not started |
| 4c | User interface | All screens in [08-UX-DESIGN](08-UX-DESIGN.md); SSE; command palette; notifications | Playwright journeys pass; axe clean | Not started |
| 5 | AI | Gateway, providers, budgets, tools, approval flow, resolver, triage, assistant, memory, eval harness | NFR-AI targets met (or AI-1 auto-suggest stays off with the gap documented); tool-permission tests pass | Not started |
| 6 | Quality and hardening | Security tests, performance benchmarks, a11y manual pass, ASVS checklist, fixes | All gates in [09-TESTING §10](09-TESTING-STRATEGY.md#10-coverage-and-quality-gates-ci) pass; results recorded | Not started |
| 7 | Release readiness | Operations guide validated on a clean PC, restore drill, cutover rehearsal, final report, `v2.0.0` tag | Fresh-install and restore drills succeed; owner sign-off | Not started |

## 2. Living plan

### 2.1 Decisions log

| Date | Decision | Record |
|---|---|---|
| 2026-10-11 | Local web app per PC; legacy OpenCode front end retired | [ADR-0001](adr/0001-local-web-application.md) |
| 2026-10-11 | Two independent instances coordinated through S3. The owner is admin; the colleague uses an operator account on his own instance, whose setup and admin password the owner holds. | [ADR-0007](adr/0007-multi-instance-coordination-via-s3.md), [10-OPERATIONS §6](10-OPERATIONS.md#6-two-instance-setup-owner-and-colleague) |
| 2026-10-11 | Configurable AI provider, Anthropic default | [ADR-0008](adr/0008-ai-gateway-and-approval.md) |
| 2026-10-11 | N stores, configurable | [ADR-0010](adr/0010-n-store-model.md) |
| 2026-10-11 | Design system: shadcn/ui, zinc neutrals, indigo accent `#4F46E5`, follow OS light/dark by default | [ADR-0013](adr/0013-design-system-shadcn-indigo.md) |
| 2026-10-11 | Downgrades held for approval | [ADR-0009](adr/0009-downgrades-held-for-approval.md) |
| 2026-10-11 | Import legacy data; retire legacy CLI after cutover | [10-OPERATIONS §9](10-OPERATIONS.md#9-cutover-from-legacy) |
| 2026-10-11 | Port from the legacy working tree as found, including the uncommitted mapping-healing change (`legacy:omnisync/mappings.py:53-116`). The legacy repository will be deleted after the project is complete. (Owner confirmation; resolves Q5.) | [01-DISCOVERY-REPORT §12](01-DISCOVERY-REPORT.md#12-constraints-and-risks) |
| 2026-10-11 | Design (Milestone 2) approved by the owner | This roadmap, section 1 |
| 2026-10-11 | Milestone 3 implementation adjustments: SQLAlchemy without compiled extensions, `python -m` invocation, httpx2, oxlint, shadcn Nova preset, CSP and focus-ring fixes to generated components | [ADR-0014](adr/0014-milestone-3-implementation-adjustments.md) |
| 2026-10-11 | CSP allows inline style attributes (`style-src-attr 'unsafe-inline'`) after WebKit end-to-end runs found violations; style elements and scripts stay strict | [ADR-0015](adr/0015-csp-style-attributes.md) |
| 2026-10-11 | Request transactions commit before the response is sent (bug found by the WebKit end-to-end run; regression tests added) | `backend/tests/test_transactions.py` |
| 2026-10-11 | Source repository `https://github.com/techaust/omni_sync` (public, default branch `main`); a new repository built up from an empty tree; the domain core and its tests are ported from legacy ([ADR-0002](adr/0002-python-fastapi-backend.md)) | [README](../README.md) |

### 2.2 Open questions

| # | Question | Needed by | Owner |
|---|---|---|---|
| Q1 | Can dedicated unpublished test products be created on both stores for live smoke tests? | Milestone 4b | Owner |
| Q2 | Enable S3 Versioning with 30-day noncurrent expiry (C-26)? | Milestone 7 | Owner |
| Q3 | Which Anthropic model is the default? (Chosen at Milestone 5 based on eval results and price.) | Milestone 5 | Owner + engineering |
| Q4 | Confirm the vulnerability-response targets in [SECURITY.md](../SECURITY.md) (acknowledge in 3 working days, assess in 10). | Milestone 7 | Owner |
| Q5 | *Resolved 2026-10-11:* the legacy working tree as found is the baseline (decisions log). | — | — |

### 2.3 Risk register

| # | Risk | Likelihood | Impact | Mitigation | Owner |
|---|---|---|---|---|---|
| R1 | Parity gaps in the extraction port cause mis-published versions | M | H | Ported tests + differential inspection on a corpus (Milestone 4a gate) | Engineering |
| R2 | Lease misbehaviour lets instances collide | L | H | Property test (NFR-REL-03), live lease test under a test prefix, write-deadline checks before every mutating call | Engineering |
| R3 | Legacy CLI run after cutover deletes control-prefix objects | L | H | Cutover step renames the legacy entry point; documented warning ([10-OPERATIONS §9](10-OPERATIONS.md#9-cutover-from-legacy)) | Owner |
| R4 | AI resolver misses targets with available models | M | L | AI optional; deterministic candidates; auto-suggest gated on eval | Engineering |
| R5 | Scope growth delays the release | M | M | MoSCoW priorities; *Could* items deferrable without blocking release | Owner |
| R6 | WooCommerce or S3 extension behaviour differs from legacy assumptions on a new store | L | M | Store connection test; contract tests with recorded responses | Engineering |
| R7 | The legacy repository is deleted before its data is imported or before the parity tests have run | L | H | Delete legacy only after cutover (`10-OPERATIONS` §9) and after Milestone 4a parity tests pass; once deleted, `check_docs.py` reports legacy citations as unverified warnings instead of failing | Owner |
| R8 | The repository is public: operational details (bucket name, store URLs, workflow) are visible | M | L | No secrets in the repository (gitleaks in CI); the bucket is private and needs credentials; owner accepted public visibility on 2026-10-11 | Owner |
| R9 | Windows Smart App Control on the owner's (and possibly the colleague's) PC blocks unsigned native binaries of future dependencies | M | M | Prefer pure-Python packages or builds; run tools via `python -m`; every new dependency is smoke-tested on the owner's PC before adoption (ADR-0014); CI also runs on Windows | Engineering |

### 2.4 Known defects

Open limitations in the 2.0 code (Milestone 3):

| # | Limitation | Plan |
|---|---|---|
| L1 | The bundled common-password list (SecLists top 10,000) contains only 10 entries with 12 or more characters, so with the 12-character minimum it adds little protection beyond the length rule. | Evaluate a larger breached-password list (offline, licence-compatible) in Milestone 6. |
| L2 | Firefox and WebKit end-to-end runs happen only in CI (Linux). On the owner's PC the Playwright Firefox build is blocked and WebKit lacks Windows dependencies. | Accepted; CI is authoritative for cross-browser results. |
| L3 | `/health/details` reports database, schema and data-folder checks only. Full legacy `check` parity (credentials, stores, S3, catalog) arrives with those services. | Milestone 4b (FR-OBS-01). |

Legacy defects D-01…D-10 are tracked in [01-DISCOVERY-REPORT §5](01-DISCOVERY-REPORT.md#5-weaknesses-defects-and-risks) and addressed by FR-CLN-02, FR-PIPE-05, FR-MATCH-05, FR-PIPE-03, FR-PIPE-06, FR-RUN-02, FR-SET-01, FR-RULE-01 and FR-CAT-03.

### 2.5 Implementation status by requirement

Only requirements with code and passing tests are listed. Everything not listed is designed but not yet implemented. Status as of Milestone 3 (CI run 38120836246).

| Requirement | Status | Evidence / what is missing |
|---|---|---|
| FR-AUTH-01 | Implemented | Setup token in console and file, regenerated at every start, old tokens refused, refused once a user exists (`backend/tests/test_setup.py`, e2e) |
| FR-AUTH-02 | Implemented | 12 h idle and 7 d absolute expiry, lockout with responses identical to a wrong password (`test_auth.py`) |
| FR-AUTH-03 | Implemented for all existing endpoints | Permission matrix: every route × every role, plus anonymous, cross-checked against OpenAPI (`test_permissions.py`) |
| FR-AUTH-04 | Implemented | Create, disable, reset password, change own password; disabling revokes sessions; last-admin guard (`test_users.py`, e2e) |
| FR-AUD-01 | Partial | Append-only audit log with triggers (`test_database.py`); filter, search and export arrive in Milestone 4b/4c |
| FR-OBS-01 | Partial | JSON logs with request IDs; health page covers database, schema and data folder; credential, store and S3 checks arrive in Milestone 4b |
| FR-OPS-03 | Partial | `serve`, `create-admin`, `reset-password` (`test_cli.py`); other commands arrive with their milestones |
| FR-UX-04 | Partial | Colour mode (pre-paint) and density saved per user (unit and e2e tests); notification settings arrive with notifications (Milestone 4c) |
| NFR-SEC-01 | Implemented | `pip-audit` and `npm audit` gates in CI; 0 known vulnerabilities |
| NFR-SEC-02 | Implemented | ruff (including bandit rules), oxlint (`react/no-danger`), gitleaks in CI |
| NFR-SEC-03 | Partial | Log redaction filter (`test_units.py`); the canary-secret test arrives with secrets (Milestone 4b) |
| NFR-SEC-04 | Partial | Host allow-list, CSRF, Origin, cookie attributes, CSP and headers tested (`test_http_security.py`, e2e); ASVS review in Milestone 6 |
| NFR-A11Y-01 | Partial | axe on every existing screen and dialog in 3 browsers, zero serious or critical violations; WCAG contrast test on all tokens; keyboard skip-link test; manual NVDA pass in Milestone 6 |
| NFR-MAINT-01 | Implemented | ruff, mypy `--strict`, import-linter, oxlint, Prettier, `tsc --strict` gates in CI |
| NFR-MAINT-02 | Implemented (current code) | Backend 94.3% branch, front end 87.5% lines; the `core/` 90% gate applies once `core/` exists (Milestone 4a) |
| NFR-PERF-02 | Partial | Initial JS 169 KB gzip, gated in CI; Lighthouse LCP measurement in Milestone 6 |
| NFR-PORT-01 | Partial | CI on Ubuntu and Windows; macOS is not in CI |
| NFR-REL-02 | Partial | WAL and `synchronous=FULL` configured and tested; transactions commit before responses (`test_transactions.py`); chaos tests in Milestone 4b |

## 3. Traceability matrix

Verification abbreviations: **U** unit, **P** property, **I** integration, **E** end-to-end, **A** accessibility, **B** benchmark, **C** chaos, **S** security, **V** AI evaluation, **R** review/checklist, **D** drill (manual procedure).

| Requirement | Design reference | Milestone | Verification |
|---|---|---|---|
| FR-INBOX-01 | [05 §4.3](05-API-SPEC.md#43-archives), [08 §3.2](08-UX-DESIGN.md#32-inbox-and-archive-detail) | 4b, 4c | I, E, B |
| FR-INBOX-02 | [04 §4](04-DATA-MODEL.md#4-file-system-layout) | 4b | I |
| FR-INBOX-03 | [06 §7](06-SECURITY.md#7-security-controls) C-08, C-09 | 4b | I, S |
| FR-INBOX-04 | [03 §5.2](03-ARCHITECTURE.md#52-item-stages-live-pipeline) | 4b, 4c | I, E, B |
| FR-INBOX-05 | [05 §4.3](05-API-SPEC.md#43-archives) | 4b, 4c | I, E |
| FR-MATCH-01 | [03 §4](03-ARCHITECTURE.md#4-components-c4-level-3-backend) `core/versions.py` | 4a | U, P, parity |
| FR-MATCH-02 | [03 §4](03-ARCHITECTURE.md#4-components-c4-level-3-backend) `core/slugs.py` | 4a | U, P, parity |
| FR-MATCH-03 | [03 §4](03-ARCHITECTURE.md#4-components-c4-level-3-backend) `core/matching.py`, [ADR-0010](adr/0010-n-store-model.md) | 4a | U, parity |
| FR-MATCH-04 | [03 §4](03-ARCHITECTURE.md#4-components-c4-level-3-backend) `core/matching.py` | 4a | U, parity |
| FR-MATCH-05 | [03 §5.2](03-ARCHITECTURE.md#52-item-stages-live-pipeline), [06 §7](06-SECURITY.md#7-security-controls) C-08 | 4a | U, S, B |
| FR-PIPE-01 | [03 §5](03-ARCHITECTURE.md#5-pipeline) | 4b | I |
| FR-PIPE-02 | [03 §5.2](03-ARCHITECTURE.md#52-item-stages-live-pipeline), [04 §3](04-DATA-MODEL.md#3-shared-s3-documents) | 4b | I |
| FR-PIPE-03 | [03 §5.2](03-ARCHITECTURE.md#52-item-stages-live-pipeline) | 4b | U, I |
| FR-PIPE-04 | [03 §5.2–5.3](03-ARCHITECTURE.md#53-concurrency-inside-one-instance) | 4b | U, I |
| FR-PIPE-05 | [03 §5.2](03-ARCHITECTURE.md#52-item-stages-live-pipeline) | 4b | U, I |
| FR-PIPE-06 | [ADR-0009](adr/0009-downgrades-held-for-approval.md) | 4b | I |
| FR-PIPE-07 | [03 §5.2](03-ARCHITECTURE.md#52-item-stages-live-pipeline), [06 §5](06-SECURITY.md#5-credential-scoping-for-multiple-instances) | 4b | I |
| FR-PIPE-08 | [03 §5.2](03-ARCHITECTURE.md#52-item-stages-live-pipeline) | 4b | I |
| FR-PIPE-09 | [03 §5.4](03-ARCHITECTURE.md#54-idempotency-and-resume) | 4b | I, C |
| FR-PIPE-10 | [03 §5.3](03-ARCHITECTURE.md#53-concurrency-inside-one-instance) | 4b | U, I |
| FR-PIPE-11 | [03 §5.1](03-ARCHITECTURE.md#51-run-lifecycle), [03 §5.4](03-ARCHITECTURE.md#54-idempotency-and-resume) | 4b | I, C |
| FR-PIPE-12 | [05 §4.4](05-API-SPEC.md#44-runs) | 4b | I |
| FR-RUN-01 | [04 §2.4](04-DATA-MODEL.md#24-intake-and-processing) | 4b, 4c | I, E |
| FR-RUN-02 | [05 §5](05-API-SPEC.md#5-server-sent-events), [ADR-0006](adr/0006-server-sent-events.md) | 4b, 4c | I, E, B |
| FR-RUN-03 | [05 §4.4](05-API-SPEC.md#44-runs) | 4b | I |
| FR-RUN-04 | [08 §3.3](08-UX-DESIGN.md#33-plan-and-confirm-live-run), [03 §5.2](03-ARCHITECTURE.md#52-item-stages-live-pipeline) | 4b, 4c | I, E |
| FR-NI-01 | [04 §2.4](04-DATA-MODEL.md#24-intake-and-processing) | 4b, 4c | I, E |
| FR-NI-02 | [08 §3.5](08-UX-DESIGN.md#35-needs-input-item) | 4b, 4c | I, E |
| FR-NI-03 | [05 §4.5](05-API-SPEC.md#45-needs-input) | 4b, 4c | I, E |
| FR-NI-04 | [05 §4.5](05-API-SPEC.md#45-needs-input) | 4b, 4c | I, E |
| FR-CAT-01 | [03 §4](03-ARCHITECTURE.md#4-components-c4-level-3-backend) `core/catalog.py` | 4a, 4b | U, I, parity |
| FR-CAT-02 | [05 §4.6](05-API-SPEC.md#46-catalog-and-rules) | 4b, 4c | I, E, B |
| FR-CAT-03 | [04 §2.2](04-DATA-MODEL.md#22-stores-and-catalog) | 4b, 4c | I |
| FR-CAT-04 | [03 §5.2](03-ARCHITECTURE.md#52-item-stages-live-pipeline), [04 §2.2](04-DATA-MODEL.md#22-stores-and-catalog) | 4b | I |
| FR-RULE-01 | [05 §4.6](05-API-SPEC.md#46-catalog-and-rules) | 4b, 4c | I, E |
| FR-RULE-02 | [03 §6.3](03-ARCHITECTURE.md#63-shared-config) | 4b | I, P |
| FR-RULE-03 | [04 §2.3](04-DATA-MODEL.md#23-rules-local-cache-of-shared-config) | 4b, 4c | I |
| FR-CLN-01 | [03 §6.1](03-ARCHITECTURE.md#61-control-prefix-layout) | 4b | I |
| FR-CLN-02 | [06 §7](06-SECURITY.md#7-security-controls) C-16, C-25 | 4b, 4c | I, E, S |
| FR-CLN-03 | [04 §2.4](04-DATA-MODEL.md#24-intake-and-processing) | 4b | I |
| FR-SET-01 | [05 §4.8](05-API-SPEC.md#48-stores-settings-secrets), [ADR-0010](adr/0010-n-store-model.md) | 4b, 4c | I, S |
| FR-SET-02 | [05 §4.8](05-API-SPEC.md#48-stores-settings-secrets) | 4b, 4c | I |
| FR-SET-03 | [06 §7](06-SECURITY.md#7-security-controls) C-05–C-07 | 3, 4b | S |
| FR-SET-04 | [05 §4.8](05-API-SPEC.md#48-stores-settings-secrets) | 4c | I, E |
| FR-SET-05 | [07 §3](07-AI-DESIGN.md#3-gateway-and-providers) | 5 | I, E |
| FR-AUTH-01 | [06 §3](06-SECURITY.md#3-authentication) | 3 | I, S |
| FR-AUTH-02 | [06 §3](06-SECURITY.md#3-authentication) | 3 | I, S |
| FR-AUTH-03 | [06 §4](06-SECURITY.md#4-authorization-model) | 3 | S |
| FR-AUTH-04 | [05 §4.1](05-API-SPEC.md#41-setup-auth-users) | 3 | I, E |
| FR-COORD-01 | [03 §6.2](03-ARCHITECTURE.md#62-lease-protocol), [ADR-0007](adr/0007-multi-instance-coordination-via-s3.md) | 4b | P, I |
| FR-COORD-02 | [03 §6.2](03-ARCHITECTURE.md#62-lease-protocol) | 4b, 4c | I, E |
| FR-COORD-03 | [03 §6.3](03-ARCHITECTURE.md#63-shared-config) | 4b | I, P |
| FR-COORD-04 | [04 §3](04-DATA-MODEL.md#3-shared-s3-documents) | 4b | I |
| FR-AUD-01 | [04 §2.5](04-DATA-MODEL.md#25-audit) | 3, 4b | I |
| FR-OBS-01 | [03 §8](03-ARCHITECTURE.md#8-cross-cutting-concerns) | 4b, 4c | I, R |
| FR-OPS-01 | [04 §5](04-DATA-MODEL.md#5-legacy-import-mapping) | 4b | I, parity |
| FR-OPS-02 | [10 §7](10-OPERATIONS.md#7-backup-and-restore) | 4b | I, D |
| FR-OPS-03 | [03 §4](03-ARCHITECTURE.md#4-components-c4-level-3-backend) `cli.py` | 3, 4b | I |
| FR-AI-01 | [07 §3](07-AI-DESIGN.md#3-gateway-and-providers) | 5 | U, I, contract |
| FR-AI-02 | [07 §5](07-AI-DESIGN.md#5-ai-1-resolver-design) | 5 | U, V |
| FR-AI-03 | [07 §2](07-AI-DESIGN.md#2-capabilities) | 5 | U, V |
| FR-AI-04 | [07 §2](07-AI-DESIGN.md#2-capabilities) | 5 | U, I |
| FR-AI-05 | [07 §4](07-AI-DESIGN.md#4-tool-catalogue) | 5 | I, E, S |
| FR-AI-06 | [07 §4](07-AI-DESIGN.md#4-tool-catalogue), [ADR-0008](adr/0008-ai-gateway-and-approval.md) | 5 | I, S |
| FR-AI-07 | [07 §7](07-AI-DESIGN.md#7-budgets-and-cost-control) | 5 | U, I |
| FR-AI-08 | [07 §12](07-AI-DESIGN.md#12-memory) | 5 | I |
| FR-AI-09 | [07 §10](07-AI-DESIGN.md#10-evaluation) | 5 | V |
| FR-UX-01 | [08 §3.1](08-UX-DESIGN.md#31-dashboard) | 4c | E, A |
| FR-UX-02 | [08 §2](08-UX-DESIGN.md#2-information-architecture) | 4c | E, A |
| FR-UX-03 | [08 §4](08-UX-DESIGN.md#4-interaction-patterns) | 4c | E |
| FR-UX-04 | [04 §2.1](04-DATA-MODEL.md#21-identity-and-access) | 4c | I, E |
| NFR-PERF-01 | [09 §6](09-TESTING-STRATEGY.md#6-performance-testing) | 6 | B |
| NFR-PERF-02 | [09 §6](09-TESTING-STRATEGY.md#6-performance-testing) | 4c, 6 | B |
| NFR-PERF-03 | [03 §5.2](03-ARCHITECTURE.md#52-item-stages-live-pipeline) | 4a, 6 | B |
| NFR-PERF-04 | [09 §6](09-TESTING-STRATEGY.md#6-performance-testing) | 4a, 6 | B |
| NFR-PERF-05 | [05 §5](05-API-SPEC.md#5-server-sent-events) | 4c, 6 | B |
| NFR-REL-01 | [03 §5.4](03-ARCHITECTURE.md#54-idempotency-and-resume) | 4b, 6 | C |
| NFR-REL-02 | [03 §3](03-ARCHITECTURE.md#3-containers-c4-level-2) | 3 | I, C |
| NFR-REL-03 | [03 §6.2](03-ARCHITECTURE.md#62-lease-protocol) | 4b | P |
| NFR-REL-04 | [03 §5.2](03-ARCHITECTURE.md#52-item-stages-live-pipeline) | 4b | I |
| NFR-SEC-01 | [06 §7](06-SECURITY.md#7-security-controls) C-20 | 3 | S |
| NFR-SEC-02 | [06 §7](06-SECURITY.md#7-security-controls) C-20 | 3 | S |
| NFR-SEC-03 | [06 §7](06-SECURITY.md#7-security-controls) C-05–C-07 | 3, 5 | S |
| NFR-SEC-04 | [06 §7](06-SECURITY.md#7-security-controls) C-01–C-04, C-21–C-23 | 3, 6 | S, R |
| NFR-A11Y-01 | [08 §7](08-UX-DESIGN.md#7-accessibility) | 4c, 6 | A, R |
| NFR-MAINT-01 | [09 §10](09-TESTING-STRATEGY.md#10-coverage-and-quality-gates-ci) | 3 | CI gate |
| NFR-MAINT-02 | [09 §10](09-TESTING-STRATEGY.md#10-coverage-and-quality-gates-ci) | 3–6 | CI gate |
| NFR-OBS-01 | [03 §8](03-ARCHITECTURE.md#8-cross-cutting-concerns) | 3 | U |
| NFR-PORT-01 | [03 §9](03-ARCHITECTURE.md#9-deployment-view) | 3 | CI matrix |
| NFR-DR-01 | [10 §7](10-OPERATIONS.md#7-backup-and-restore) | 7 | D |
| NFR-AI-01 | [07 §10](07-AI-DESIGN.md#10-evaluation) | 5 | V |
| NFR-AI-02 | [07 §7](07-AI-DESIGN.md#7-budgets-and-cost-control) | 5 | U, I |
| NFR-AI-03 | [07 §10](07-AI-DESIGN.md#10-evaluation) | 5 | V |
| NFR-PRIV-01 | [07 §8](07-AI-DESIGN.md#8-privacy) | 5 | S, R |
| NFR-UX-01 | [08 §6](08-UX-DESIGN.md#6-responsive-behaviour) | 4c | E |

## 4. Feature parity checklist

Every legacy capability, and where it lives in 2.0.

| Legacy capability | Legacy source | 2.0 equivalent | Change |
|---|---|---|---|
| Pipeline run | `legacy:omnisync/cli.py:78-216` | Plan + live run (FR-PIPE-*, FR-RUN-04) | Plan confirmation added |
| `--dry-run` | `legacy:omnisync/cli.py:124-139` | Dry run (FR-PIPE-01) | Optional read-only verification |
| `--force-upload` | `legacy:omnisync/cli.py:442-446` | Option *Force* (FR-PIPE-12) | — |
| `--no-verify-s3`, `--trust-state` | `legacy:omnisync/cli.py:447-456` | Options *Verify S3*, *Trust recorded hashes* | — |
| `--reset-state`, `--force-file` | `legacy:omnisync/cli.py:99-122` | *Force* option on a run for the selected archives | Global state reset not carried over: deduplication checks live S3 and store state, so stale local state cannot block work |
| `--no-move` | `legacy:omnisync/cli.py:481-485` | Option *Keep in inbox* | — |
| `--delay`, `--max-workers` | `legacy:omnisync/cli.py:469-480` | Options *Request spacing*, *Concurrency* | Default concurrency 8 → 4 |
| `sync-mapping` | `legacy:omnisync/mappings.py:400-416` | Catalog refresh (FR-CAT-01) | Progress, conflicts report |
| `cleanup-s3` | `legacy:omnisync/cli.py:219-289` | Cleanup (FR-CLN-*) | Lease + re-verification (D-01) |
| `check` | `legacy:omnisync/cli.py:292-363` | Health page (FR-OBS-01) | — |
| `secrets set/list/delete/purge-env` | `legacy:omnisync/cli.py:366-415` | Settings → Secrets (FR-SET-03) | `purge-env` not needed (no `config.env`) |
| Backup | `legacy:omnisync/backup.py` | Backups (FR-OPS-02) | Database snapshot + restore |
| Secondary-name prompt | `legacy:omnisync/engine.py:313-380` | Needs input `secondary_name` (FR-NI-03) | No terminal; AI suggestion |
| Product-URL-slug fallback (tier 1/2) | `legacy:omnisync/engine.py:149-195` | Matching cascade (FR-MATCH-03) | All enabled stores |
| Fallback audit ledger | `legacy:omnisync/engine.py:198-221` | Audit log (FR-AUD-01) | — |
| Unmatched log | `legacy:omnisync/engine.py:84-99` | Needs input + audit | Actionable |
| Downgrade warning | `legacy:omnisync/engine.py:525-530` | Downgrade hold (FR-PIPE-06) | Behaviour change (ADR-0009) |
| Old-object safe delete | `legacy:omnisync/engine.py:753-775` | Finalize stage (FR-PIPE-07) | Pending deletes |
| Terminal report | `legacy:omnisync/engine.py:805-823` | Run detail + export (FR-RUN-03) | — |
| `/omnisync-status` | `legacy:.opencode/commands/omnisync-status.md` | Dashboard (FR-UX-01) | Native |
| `/omnisync` background run and polling | `legacy:.opencode/scripts/omnisync_run.py` | Job runner + SSE (FR-RUN-02) | Structured events instead of log scraping |
| `/omnisync-bootstrap` | `legacy:.opencode/commands/omnisync-bootstrap.md` | Setup wizard ([10 §4](10-OPERATIONS.md#4-first-run-setup)) | — |
| `app.lock` | `legacy:omnisync/locking.py` | Instance lock + S3 lease (FR-COORD-01) | Cross-machine |
