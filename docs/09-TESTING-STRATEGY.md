# Testing Strategy

> **Status:** Accepted (approved design, not yet implemented) · **Owner:** TecHaust Technologies (@techaust) · **Last reviewed:** 2026-10-11 · **Audience:** engineers, reviewers

This document defines how every requirement is verified. Which test covers which requirement is recorded in the [traceability matrix](11-ROADMAP.md#3-traceability-matrix).

## 1. Objectives

1. Prove behavioural parity with the legacy engine where parity is intended.
2. Prove the fixes for legacy defects D-01…D-05 with regression tests that fail on the legacy behaviour.
3. Make external effects observable: every test of a mutating flow asserts the exact S3 and WooCommerce calls made.
4. Keep the suite fast: the offline backend suite should finish in under 3 minutes on the reference machine.

## 2. Test levels

| Level | Scope | Tools | Runs |
|---|---|---|---|
| Unit | `core/` pure functions, services with fakes | pytest, Hypothesis | every commit |
| Property | Extractor, slugify, version comparison, lease protocol | Hypothesis | every commit (CI profile: 200 examples) |
| Integration | API + DB + job runner + fake S3/WC/AI | pytest, FastAPI TestClient, httpx, respx, moto | every commit |
| Contract | AI provider adapters vs recorded fixtures; OpenAPI ↔ generated TS client | pytest, openapi-typescript diff | every commit |
| Front-end unit/component | Components, hooks, forms | Vitest, Testing Library | every commit |
| End-to-end | Real server (fake externals) + built SPA in Chromium, Firefox, WebKit | Playwright | every PR |
| Accessibility | Every route, light and dark theme | `@axe-core/playwright`, manual keyboard and NVDA pass | every PR + release |
| Performance | NFR-PERF-01…05 | pytest-benchmark, memory tracer, Lighthouse CI | nightly + release |
| Chaos | Process kill at each stage; network faults | pytest harness that spawns `omnisync serve` | nightly + release |
| Security | Section 7 | pytest, pip-audit, npm audit, gitleaks | every PR + weekly |
| AI evaluation | NFR-AI-01…03 | `omnisync eval resolver` | release + on prompt or model change |
| Live smoke | Real S3/WC on dedicated test products | pytest `-m live` | manual, owner-approved only (section 12) |

## 3. Fakes and fixtures

| External | Strategy |
|---|---|
| S3 | **moto** for standard operations. A purpose-built `FakeConditionalS3` implements `If-None-Match`/`If-Match` semantics, 412/409 responses and `Date` headers for lease and shared-config tests, because emulator support for conditional writes cannot be assumed. The same lease tests also run against real S3 in the live smoke suite under a test prefix. |
| WooCommerce | `respx`-mocked httpx transport backed by an in-memory product store that implements GET/PUT `products/{id}`, paginated listing with `X-WP-TotalPages`, configurable faults (status codes, latency, ignored writes, `date_modified` rejection). |
| AI providers | Recorded response fixtures per provider; a scripted fake provider for tool-loop tests. |
| Archives | Generated at test time by builders (`make_plugin_zip(slug, version, header=…)`, nested bundles, Envato wrappers), plus a small committed corpus of anonymised real structures. Zip bombs are generated, never committed. |
| Time | Injected clock for lease, session and plan-expiry tests. |

## 4. Legacy parity

1. **Spec port.** The 194 legacy tests (`legacy:tests/`) are ported to the new module boundaries. Each ported test keeps a docstring reference to its legacy test name. Tests that encode behaviour intentionally changed (D-02, D-03, D-04, D-05) are rewritten to assert the new behaviour and tagged `@pytest.mark.behaviour_change("D-0x")`.
2. **Differential inspection.** For each archive in the parity corpus, the legacy `extract()` + `choose_candidate()` output and the new inspection output must match on slug, version, layer and chosen mapping key. The test runs only when `OMNISYNC_LEGACY_ROOT` points to a legacy checkout with its venv, and is mandatory in Milestone 4a.
3. **Differential planning.** The legacy dry-run report counters (`legacy:omnisync/engine.py:805-823`) and the new plan counters must match for the parity corpus, except for documented behaviour changes.
4. **Import fidelity.** Importing a copy of the legacy `data/` and `stores/` yields counts equal to the sources (FR-OPS-01).

## 5. Defect regression tests

| Defect | Regression test (must fail against the legacy behaviour) |
|---|---|
| D-01 | A cleanup created during a live run (same or other instance) waits in `waiting_for_lease` and deletes nothing until the live run releases the lease. A key that becomes referenced between preview and execute is not deleted. |
| D-02 | A product with two download entries and no slug match → `ambiguous_entry`, zero PUTs |
| D-03 | A batch of 10 archives with 700 MB nested payloads stays within NFR-PERF-03 |
| D-04 | HEAD returning 403 → item `failed` with `s3.access_denied`, zero PUTs |
| D-05 | Incoming version lower than live meta → `downgrade` needs-input, zero writes |

## 6. Performance testing

| Requirement | Method | Data |
|---|---|---|
| NFR-PERF-01 | pytest-benchmark on `GET /catalog`, `/runs` and `/audit` with 10,000 catalog entries per store, 5,000 runs and 100,000 audit rows; assert p95 | Synthetic generator |
| NFR-PERF-02 | Lighthouse CI against the production build; bundle-size check on `dist/assets/*.js` (gzip) | — |
| NFR-PERF-03 | `tracemalloc` plus RSS sampling while uploading and inspecting a 1.5 GB archive with a 700 MB nested bundle | Generated fixture |
| NFR-PERF-04 | Timed inspection of a 500 MB archive | Generated fixture |
| NFR-PERF-05 | Playwright measures the time from server event emission (timestamp in the event) to DOM update | Fake run |

Results are written to `reports/perf/<date>.json`. Nightly runs compare against the last release and fail if the result regresses by more than 20%.

## 7. Security testing

Security testing implements [06-SECURITY §8](06-SECURITY.md#8-verification):

- Permission matrix test generated from the route table: every route × {anonymous, viewer, operator, admin}.
- Header tests: Host allow-list (421), Origin mismatch, missing CSRF token (403), cookie attributes, CSP, `nosniff`, `Referrer-Policy`.
- Secret canary test (C-07).
- Upload and archive abuse corpus: non-ZIP, oversized, zip bomb (ratio and entry count), deep nesting, `../` and absolute entry names, symlink entries, Unicode filenames, names with reserved Windows characters.
- Prompt-injection red-team fixtures (07-AI-DESIGN §9).
- Store URL SSRF cases: `http://`, `https://127.0.0.1`, `https://10.0.0.5`, `https://[::1]`, and a hostname resolving to a private address.

## 8. Accessibility testing

- Automated: axe on every route and every dialog state in both themes; zero serious or critical violations (NFR-A11Y-01).
- Keyboard: a scripted Playwright journey completes the main user journeys ([02-PRODUCT-REQUIREMENTS §3](02-PRODUCT-REQUIREMENTS.md#3-key-user-journeys)) without a pointer.
- Manual before release: NVDA + Firefox pass over the same journeys, plus 200% zoom and 400% reflow checks. Findings are recorded in the release report.

## 9. Chaos and recovery testing

For each stage boundary in [03-ARCHITECTURE §5.2](03-ARCHITECTURE.md#52-item-stages-live-pipeline), the harness kills the server with SIGKILL (or `TerminateProcess` on Windows), restarts it and resumes the run. It then asserts:

- no duplicate S3 PUTs for completed items
- store entries point at the final key
- `slug_state` is consistent
- the audit log contains no partial records

Network faults (timeouts, connection resets, 5xx bursts) are injected through the fakes.

**Lease property test (NFR-REL-03).** 1,000 randomised schedules of two simulated instances run against `FakeConditionalS3` with a simulated S3 clock. Schedules cover acquire, renew, release, crash, process freeze, network delay up to the 3 s RTT limit, and local wall-clock offsets of up to ±10 minutes, which must not matter. Invariants:

1. No instance sends a mutating call after its write deadline.
2. In schedules without freezes, at most one instance holds the lease at any S3-time instant, and no mutating calls from two instances overlap.
3. In freeze schedules, at most one stale call is sent after a takeover, and none follows it.

## 10. Coverage and quality gates (CI)

| Gate | Threshold |
|---|---|
| ruff (lint + format), mypy `--strict`, import-linter | clean |
| eslint, `tsc --strict`, prettier check | clean |
| Backend coverage | `core/` ≥ 90% branch; overall ≥ 80% |
| Front-end coverage | ≥ 70% lines |
| pip-audit, `npm audit --audit-level=high`, gitleaks | no findings |
| Docs: `scripts/check_docs.py`, markdownlint | clean |
| Playwright E2E + axe | all pass |

## 11. Test data management

- No production data in the repository. The parity corpus contains structural fixtures only (headers and readmes with placeholder content).
- Legacy-data import tests run against a copy that the test creates in a temporary directory. The legacy repository is only ever read.

## 12. Live testing policy

Live tests touch real S3 and stores, so all of the following apply:

1. They run only with `OMNISYNC_LIVE=1` and the owner's explicit approval for that session.
2. They use dedicated, unpublished test products on each store, and set the S3 object prefix (FR-SET-02) to `omnisync-test/`. This keeps it outside the control prefix, so test objects are subject to normal cleanup. Live lease tests use a separate control prefix (`omnisync-test-control/`). Real catalog products are never touched. At the end of each session the harness deletes every object under both test prefixes and restores the test products' download entries.
3. They are read-only by default. Write tests are opt-in individually with `-m live_write`.
4. Every live session is recorded in the audit log and in the release report.
