# Product Requirements

> **Status:** Proposed · **Owner:** TecHaust Technologies (@techaust) · **Last reviewed:** 2026-10-11 · **Audience:** product owner, engineers, testers

This document defines **what** OmniSync 2.0 must do and how each requirement is accepted. **How** it is built is covered in [03-ARCHITECTURE](03-ARCHITECTURE.md). Every requirement is traced to design, milestone and verification in [11-ROADMAP](11-ROADMAP.md#3-traceability-matrix).

## 1. Product summary

OmniSync publishes new versions of WordPress plugins and themes to every configured WooCommerce store from a single upload. An operator drops vendor ZIPs into the browser. OmniSync identifies each installable, uploads it once to S3, and points every store's download at that object. Uncertain cases (unknown product, downgrade, missing button name) become clear decisions instead of silent errors or hand-edited JSON. AI helps with those decisions, but deterministic code performs all the actual changes.

### 1.1 Goals

| Goal | Measure |
|---|---|
| G1 Near-zero-touch publishing for known products | ≥ 95% of archives for already-mapped products complete with no manual step other than confirming the plan |
| G2 No terminal or IDE required | Every legacy capability is reachable in the browser (parity checklist in [11-ROADMAP §4](11-ROADMAP.md#4-feature-parity-checklist)) |
| G3 Safe by construction | No legacy defect D-01…D-05 can recur (regression tests); no two instances mutate concurrently, except the single residual stale call of T-11 (NFR-REL-03) |
| G4 Faster resolution of unknown archives | Median time to resolve an unmapped archive ≤ 1 minute, versus hand-editing JSON in legacy |
| G5 Transparent operations | Every consequential action appears in the audit log with actor and outcome |

### 1.2 Non-goals (version 2.0)

- Editing product content (titles, descriptions, SEO). That belongs to the sibling OmniSEO project.
- Selling, licensing or customer management.
- Hosting OmniSync on the public internet. It binds to localhost only ([ADR-0001](adr/0001-local-web-application.md)).
- Fully autonomous AI actions. Consequential actions always need approval ([ADR-0008](adr/0008-ai-gateway-and-approval.md)).

## 2. Users and roles

| Persona | Description | Role |
|---|---|---|
| **Owner** | Runs the business; configures stores, S3 and AI; resolves edge cases; performs cleanup. | Admin on own instance |
| **Colleague** | Publishes vendor updates from his own PC and his own instance. | Operator account on his own instance. The owner performs that instance's setup with an admin account and keeps its password ([10-OPERATIONS §6](10-OPERATIONS.md#6-two-instance-setup-owner-and-colleague)). Real limits come from his own credentials ([06-SECURITY §5](06-SECURITY.md#5-credential-scoping-for-multiple-instances)). |
| **Auditor** (occasional) | Reviews what changed and when. | Viewer |

## 3. Key user journeys

1. **Publish updates (happy path).** Drag 20 ZIPs onto the Inbox. Each archive is inspected within seconds and shows `slug v1.2.3 → matched on 2/2 stores`. Click **Plan live run** and review the operations (20 S3 uploads, 40 store updates, 18 old objects to delete). Confirm, watch live progress, and read the report.
2. **Resolve an unknown archive.** An archive shows *Needs input: unmapped*. Open it and see the top candidates, for example `modula-pro` (92%, "plugin header name 'Modula Pro' matches product title"). Accept, and an alias is created and shared. The archive is re-planned automatically.
3. **Review a downgrade.** An archive contains `v3.0.4` while the store has `v3.0.11`. It is held. Choose *Reject* (move to rejected) or *Approve downgrade* (with a reason that is recorded).
4. **Refresh the catalog.** Start **Refresh catalog**. Progress shows page N/24 per store, and the result lists conflicts such as `layerslider` mapped to two product IDs.
5. **Clean up S3.** Preview the orphans, select them, type `DELETE 12`, and get a deletion report.
6. **Ask the assistant.** "Why did gutenkit fail twice this week?" The assistant reads run history with tools and explains. It offers a button to propose the alias, which you then approve.

## 4. Functional requirements

Priority: **M** = Must (release-blocking), **S** = Should, **C** = Could.

### 4.1 Inbox and intake

| ID | Requirement | Pri | Acceptance criteria |
|---|---|---|---|
| FR-INBOX-01 | Upload one or many ZIP archives through drag-and-drop or a file picker, streamed to disk with per-file progress. | M | Given a 1.5 GB ZIP, when uploaded, then server memory stays within NFR-PERF-03 and the UI shows progress and completion. |
| FR-INBOX-02 | Ingest archives placed in the watched folder once (a) size and modification time are unchanged across two consecutive polls (interval 5 s), (b) the file can be opened with exclusive (no-share) access, and (c) the ZIP central directory reads successfully. | M | Given a 1 GB file copied into the folder over 30 s, when polling runs, then it is ingested exactly once and only after the copy completes. A file still locked by the copying process is never ingested. |
| FR-INBOX-03 | Validate each upload: ZIP signature, configurable size limit (default 2 GiB), sanitised filename, duplicate detection by SHA-256. | M | A renamed `.exe` is rejected with code `archive.not_zip`. A second identical file is flagged `duplicate_of=<archive id>`. |
| FR-INBOX-04 | Inspect each archive automatically on arrival and show detected slug, version, detection layer, candidates, match result per store and planned operations. | M | Inspection results appear within NFR-PERF-04. |
| FR-INBOX-05 | Remove an archive from the inbox to a trash area, recoverable for 7 days. | S | A removed archive can be restored within 7 days. It is purged afterwards. |

### 4.2 Detection and matching

| ID | Requirement | Pri | Acceptance criteria |
|---|---|---|---|
| FR-MATCH-01 | Detect the version with legacy parity: header `Version:` → readme `Stable tag:` / `package.json` / `composer.json` → filename, with a deep readme/changelog scan for bundles (`legacy:omnisync/extractor.py:185-199`). | M | All ported legacy extractor tests pass. The parity corpus produces identical slug and version results ([09-TESTING §4](09-TESTING-STRATEGY.md#4-legacy-parity)). |
| FR-MATCH-02 | Detect the slug with legacy parity: parent of `style.css`, then a PHP file with `Plugin Name:`, then readme name, then a cleaned filename with Envato prefix and item ID stripped (`legacy:omnisync/extractor.py:212-261`). | M | As FR-MATCH-01. |
| FR-MATCH-03 | Match with this cascade: direct slug → alias → filename override → generic filename slug present on **all enabled stores** ([ADR-0010](adr/0010-n-store-model.md)). Record which method matched. | M | Each method is covered by tests, and `match_method` is stored on the run item. |
| FR-MATCH-04 | Choose among multiple candidates with the legacy priority (full-store match first, then slug contained in the filename, header present, highest version) (`legacy:omnisync/engine.py:257-310`). | M | Ported `choose_candidate` tests pass. |
| FR-MATCH-05 | Bound resource use while inspecting: stream entries; nested payload cap 768 MiB per archive; at most 10,000 entries; nesting depth ≤ 6; compression ratio ≤ 200:1 per entry; chosen nested payload spooled to a temp file rather than memory (fixes D-03). | M | Zip-bomb fixtures are rejected with `archive.limits_exceeded`. Peak memory meets NFR-PERF-03. |

### 4.3 Pipeline

| ID | Requirement | Pri | Acceptance criteria |
|---|---|---|---|
| FR-PIPE-01 | **Dry run** produces the full plan with no external writes. If credentials exist it may perform read-only calls (S3 HEAD, WooCommerce GET); otherwise it plans offline and says so. | M | During a dry run, the S3 and WooCommerce fakes record zero write calls. |
| FR-PIPE-02 | **Live run** uploads each new installable exactly once to `<object prefix><slug>-<version>.zip` with metadata `latest-version` and `content-md5-hex`. The object prefix is empty (bucket root, as in legacy) except for live tests ([09-TESTING-STRATEGY §12](09-TESTING-STRATEGY.md#12-live-testing-policy)). Payloads above 8 MiB use multipart upload with 8 MiB parts, so that every call fits the 60 s lease-safe call deadline ([03-ARCHITECTURE §5.2](03-ARCHITECTURE.md#52-item-stages-live-pipeline)). | M | Each new archive produces exactly one completed upload. A re-run produces zero. |
| FR-PIPE-03 | S3 deduplication: skip the upload when the object exists with the same MD5 metadata. Only HTTP 404 means "missing"; any other error fails the item with a clear code (fixes D-04). | M | A HEAD returning 403 leads to item status `failed` with code `s3.access_denied` and no upload. |
| FR-PIPE-04 | For each enabled store: GET product → modify the matching download entry (shortcode, button name) and meta (`latest_version_<slug>`, plus `latest_version` for the primary) → PUT → verify that the response contains the new object key. Serialise per product; rate-limit per store host. | M | Ported `wc` tests pass. A PUT whose response lacks the key results in `failed` with code `wc.write_ignored`. |
| FR-PIPE-05 | Select the download entry in this order: stored entry ID → exact base match → unique prefix match. Fall back to the button name **only when the product has exactly one download entry**; otherwise create a needs-input item `ambiguous_entry` (fixes D-02). | M | A two-entry product with no slug match produces a needs-input item, not a write. |
| FR-PIPE-06 | Hold downgrades: if the incoming version is lower than the live WooCommerce meta (`latest_version_<slug>`) or the recorded slug state, or if either existing version cannot be compared with the incoming one, create a needs-input item `downgrade` and do nothing else for that archive. If there is no live meta and no slug state (first publish), proceed ([ADR-0009](adr/0009-downgrades-held-for-approval.md)) (fixes D-05). | M | Given live `3.0.11`, when `3.0.4` is processed, then zero writes and one `downgrade` item. Given no live meta and no slug state, when `1.0.0` is processed, then it proceeds. Given live `trunk`, when `2.0` is processed, then one `downgrade` item noting "versions not comparable". |
| FR-PIPE-07 | Delete the superseded object only when all of these hold: all enabled stores completed; no other entry in the catalog of **any configured store (enabled or disabled)** references it; every configured store's catalog was refreshed within the last 24 h; and the instance credentials allow deletes. Otherwise record a pending delete for admin cleanup (FR-CLN-03). | M | Each of the four failing conditions (shared object, disabled-store reference, catalog older than 24 h, no delete permission) produces one pending delete and zero delete calls. |
| FR-PIPE-08 | When all stores complete, move the archive to `processed/<slug>-<version>.zip` unless *Keep in inbox* is set. | M | Given all stores completed, the archive is in `processed/` under the canonical name. Given *Keep in inbox* or any store not completed, it stays in `inbox/`. |
| FR-PIPE-09 | Re-runs are idempotent: completed work is skipped (verified against S3 metadata and the live store entry), failed stores are retried, and *Force* re-uploads and re-syncs. | M | Running the same inbox twice gives zero writes on the second run. |
| FR-PIPE-10 | Resilience parity: 3 retries with 2/4/8 s backoff on 408/429/500/502/503/504/520/521/522 and network errors; per-store circuit breaker (5 failures → 60 s); 60 s request timeout (`legacy:omnisync/retry.py`, `legacy:omnisync/circuit_breaker.py`). | M | Given a store returning 503 twice then 200, the write succeeds after 2 retries. Given 503 four times, the store result is `failed` with `wc.http_error`. Given 5 consecutive failures, further calls to that store fail immediately with `circuit_open` for 60 s, while other stores continue. |
| FR-PIPE-11 | Cancel a run; it stops at the next safe point (between stages). Runs interrupted by a crash are marked `interrupted` and can be resumed idempotently. | M | Chaos test: kill the process mid-run, restart, resume; no duplicate PUT and the final state is consistent (NFR-REL-01). |
| FR-PIPE-12 | Run options: force, trust recorded hashes, verify S3, keep in inbox, item concurrency (1–8, default 4), store request spacing (default 0.15 s). | M | Options are validated and persisted on the run. |

### 4.4 Runs and reporting

| ID | Requirement | Pri | Acceptance criteria |
|---|---|---|---|
| FR-RUN-01 | Persist every run with options, actor, instance, timings, counters, per-item and per-store results. | M | A run created 90 days ago is still viewable (retention configurable, default unlimited). |
| FR-RUN-02 | Stream live progress (status, current stage per item, counters, log lines) to all open browser tabs. | M | Events arrive within NFR-PERF-05, and a reconnecting tab resumes without gaps. |
| FR-RUN-03 | Export a run report as JSON and CSV. The terminal-style summary matches the legacy report fields (`legacy:omnisync/engine.py:805-823`). | S | The export includes every counter in the legacy report. |
| FR-RUN-04 | A live run is created only from a completed dry run (its plan) that is less than 15 minutes old **when the live run is created**, and the user confirms it. The live run processes only the plan's ready items with the plan's options; options cannot be changed at confirmation. Every item is re-validated against live state when it executes, so waiting for the lease cannot make a stale plan unsafe. Items whose situation changed go to needs-input instead of proceeding. | M | Creating a live run from a 16-minute-old plan is refused with `plan.stale`. An item whose live version changed after planning is diverted to needs-input with zero writes. |

### 4.5 Needs input

| ID | Requirement | Pri | Acceptance criteria |
|---|---|---|---|
| FR-NI-01 | Maintain a queue of decisions with kinds `unmapped`, `secondary_name`, `downgrade` and `ambiguous_entry`. Show a count in the navigation. (Pending deletes are not needs-input items; they are handled only in Cleanup, FR-CLN-03.) | M | Each kind can be created, resolved and dismissed in tests, and the navigation count equals the number of open items. |
| FR-NI-02 | Resolve `unmapped` by choosing a mapping key from ranked deterministic candidates (with optional AI ranking, FR-AI-02). This creates an alias or a filename override, and the archive is re-inspected automatically. | M | After resolution the archive's inspection shows `match_method=alias` or `match_method=override`, matching the rule type chosen. |
| FR-NI-03 | Resolve `secondary_name` by entering a button name, pre-filled with a suggestion (FR-AI-03, or a title-cased slug when AI is off). | M | The name is saved to the shared download names. |
| FR-NI-04 | Resolve `downgrade` with Approve (reason required) or Reject (archive moved to `rejected/`). | M | An approval is audited with its reason. |

### 4.6 Catalog

| ID | Requirement | Pri | Acceptance criteria |
|---|---|---|---|
| FR-CAT-01 | Refresh the catalog per store from WooCommerce (100 per page, `X-WP-TotalPages` with short-page fallback, 1,000-page cap, version-suffix healing with guard) as a run with progress (`legacy:omnisync/mappings.py:196-307`). | M | Ported mapping tests pass. Progress shows page/total. |
| FR-CAT-02 | Browse, search (key, product ID, title, base) and filter (store, primary/secondary, conflicts) the catalog. | M | Search returns within NFR-PERF-01. |
| FR-CAT-03 | Report conflicts: keys mapped to several products, and keys missing on some enabled stores. | S | Legacy-known conflicts (`layerslider`, `woocommerce-bulk-variations`) appear after import. |
| FR-CAT-04 | Refresh the catalogs of **all configured stores, including disabled ones**, automatically once a day (02:30 local time while running, and at start-up if any catalog is older than 24 h) as a normal catalog_refresh run that needs the lease. A disabled store keeps its credentials for this purpose. | M | Given a server running for 48 h, every catalog is always younger than 24 h. Given a failed refresh of one store, pipeline finalisation records pending deletes instead of deleting (FR-PIPE-07). |

### 4.7 Rules (aliases, overrides, download names)

| ID | Requirement | Pri | Acceptance criteria |
|---|---|---|---|
| FR-RULE-01 | Create, read, update and delete aliases, filename overrides and download names. The target mapping key must exist in the catalog of at least one enabled store. | M | An invalid target is rejected with `rule.unknown_target`. |
| FR-RULE-02 | Rules are shared across instances through the shared config with optimistic concurrency (FR-COORD-03). | M | Two instances editing at once: the second gets a conflict dialog and no change is lost. |
| FR-RULE-03 | Keep history per rule (previous value, actor, instance, source `manual`, `ai_accepted` or `legacy_import`, timestamp). | S | Given an alias created and then edited, `GET /rules/history?type=alias&key=<slug>` returns two entries with old and new values, actor and instance, and the rule's detail view lists them. |

### 4.8 S3 cleanup

| ID | Requirement | Pri | Acceptance criteria |
|---|---|---|---|
| FR-CLN-01 | Preview orphans: the bucket listing minus every key referenced by a download entry in **any configured store (enabled or disabled)**, always excluding the control prefix. References are collected **live** from the WooCommerce API during the preview, not from the local catalog, as legacy did (`legacy:omnisync/cli.py:230-252`). | M | The control prefix is never listed as orphaned. A key linked by hand in a store after the last catalog refresh is not listed. |
| FR-CLN-02 | Delete selected orphans (admin only) after typed confirmation `DELETE <n>`. Requires the lease. After acquiring the lease, the run collects references live again and deletes only keys that are still unreferenced (fixes D-01). | M | A key that becomes referenced between preview and execute is skipped and reported. A cleanup created while a live run holds the lease waits and executes afterwards with fresh references. |
| FR-CLN-03 | Pending deletes recorded by any instance appear in the cleanup preview and are deleted only through FR-CLN-02 with the same safeguards. An admin may also discard a pending delete (keep the object). | S | A pending delete recorded by instance B appears in instance A's preview. Deleting it requires the typed confirmation and live re-verification. |

### 4.9 Settings and secrets

| ID | Requirement | Pri | Acceptance criteria |
|---|---|---|---|
| FR-SET-01 | Configure N stores (key, label, base URL, enabled) and test the connection (authenticated read of one product page). | M | Adding a third store needs no code change. |
| FR-SET-02 | Configure S3 (bucket, region, optional endpoint, control prefix, object prefix defaulting to empty) and test it (HEAD bucket, conditional-write probe on the control prefix). | M | The test reports each capability separately. |
| FR-SET-03 | Secrets are write-only in the UI. Status shows only the source (`environment`, `keyring`, `unset`), never the value. | M | No API response contains a secret value (automated scan, NFR-SEC-03). |
| FR-SET-04 | Configure run defaults (FR-PIPE-12 options). | S | Defaults pre-fill the run dialog. |
| FR-SET-05 | Configure AI: enabled, provider, model, daily and monthly budgets, automatic resolver suggestions on/off (`ai.auto_suggest`, default off), memory on/off. | M | Disabling AI hides AI controls, and every other feature keeps working. |

### 4.10 Authentication and users

| ID | Requirement | Pri | Acceptance criteria |
|---|---|---|---|
| FR-AUTH-01 | First-run setup creates the first admin and requires a one-time setup token (128-bit, base32). While setup is required, a new token is generated at every start, printed in the console, and written to `<data dir>/setup-token.txt` (user-only permissions; deleted when setup completes), so it can be retrieved even when the server was started by Task Scheduler. | M | Setup without the token, or with an old token, is refused. Setup is impossible once a user exists, and the token file no longer exists. |
| FR-AUTH-02 | Log in and out with server-side sessions: idle timeout 12 h, absolute lifetime 7 days, lockout for 15 min after 10 consecutive failed attempts. A locked account receives the same `401 auth.invalid_credentials` as a wrong password, so lockouts do not reveal which usernames exist. | M | Given 10 failed attempts, a correct password is refused for 15 min with a response identical to an unknown username. Sessions expire after 12 h idle and after 7 days. |
| FR-AUTH-03 | Enforce roles `admin`, `operator` and `viewer` on every API endpoint according to the permission matrix ([06-SECURITY §4](06-SECURITY.md#4-authorization-model)). | M | An automated matrix test calls every endpoint as every role. |
| FR-AUTH-04 | Admins manage users (create, disable, reset password). Users change their own password. | M | A disabled user's sessions are revoked immediately. |

### 4.11 Multi-instance coordination

| ID | Requirement | Pri | Acceptance criteria |
|---|---|---|---|
| FR-COORD-01 | Mutating operations (live run, catalog refresh, cleanup execute, and legacy import when it pushes rules to the shared config) require the shared lease. A mutating run created while the lease is held elsewhere waits in `waiting_for_lease`; it is not rejected ([03-ARCHITECTURE §6](03-ARCHITECTURE.md#6-multi-instance-coordination)). | M | NFR-REL-03 test; a cleanup created during another instance's live run starts only after that run releases the lease. |
| FR-COORD-02 | Show the lease holder (instance, user, operation, expiry). A run waits in `waiting_for_lease` with an option to cancel. Expired leases are taken over automatically, and live leases are never broken. | M | UI and API show the holder. A takeover only happens after expiry. |
| FR-COORD-03 | Sync the shared config: pull on start, every 60 s and before each run; push with `If-Match`; on conflict, re-pull and re-apply or ask the user. | M | Conflict test with two simulated instances. |
| FR-COORD-04 | Publish consequential audit events to the shared audit area so that every instance can show the combined history. | S | An event from instance B appears in instance A within 2 minutes. |

### 4.12 Audit and observability

| ID | Requirement | Pri | Acceptance criteria |
|---|---|---|---|
| FR-AUD-01 | Keep an append-only audit log (actor, instance, action, target, outcome, details) with filter, search and CSV export. | M | A DELETE on `audit_log` fails. An UPDATE fails unless it only sets `shared_published_at` from NULL to a timestamp ([04-DATA-MODEL §2.5](04-DATA-MODEL.md#25-audit)). |
| FR-OBS-01 | Write structured logs and provide a health page with legacy `check` parity: credentials present per store and S3, directories, catalog sizes, keyring, lease, shared config version, AI status (`legacy:omnisync/cli.py:292-363`). | M | Every legacy `check` line has an equivalent on the health page. Credentials are shown as configured or missing; where each secret is stored (environment or keyring) is shown only to admins, under Settings → Secrets (FR-SET-03). |

### 4.13 Operations

| ID | Requirement | Pri | Acceptance criteria |
|---|---|---|---|
| FR-OPS-01 | Import legacy data idempotently (state, aliases, overrides, download names, fallback audit, mappings, unmatched log), with a dry-run report first. Option `push_rules` (default on) merges imported rules into the shared config ([04-DATA-MODEL §5](04-DATA-MODEL.md#5-legacy-import-mapping)). | M | Importing twice yields the same database. Counts match the source files. |
| FR-OPS-02 | Back up automatically and on demand: the SQLite online backup plus `omnisync.toml`, no secrets; keep the 14 newest. The automatic backup runs at 03:00 local time if the server is running, and at start-up if the newest backup is older than 24 h. Restore through the CLI. | M | Given a server started at 10:00 with no existing backups and running for 72 h, exactly 4 automatic backups exist (one at start-up plus three at 03:00). The restore drill in [10-OPERATIONS](10-OPERATIONS.md#7-backup-and-restore) succeeds. |
| FR-OPS-03 | Provide CLI commands `serve`, `check`, `import-legacy`, `backup`, `restore`, `create-admin`, `reset-password`, `eval`, `install-task`, `uninstall-task` (Windows log-on auto-start). | M | `omnisync --help` lists every command, and each command has at least one integration test covering success and one failure exit code. |

### 4.14 AI capabilities

| ID | Requirement | Pri | Acceptance criteria |
|---|---|---|---|
| FR-AI-01 | AI gateway with Anthropic (default) and OpenAI adapters; provider and model configurable; AI fully optional. | M | The whole test suite passes with AI disabled. Provider contract tests pass. |
| FR-AI-02 | Rank unmapped-archive candidates with structured output (choice from the candidate set only, confidence, rationale, evidence). | M | Meets NFR-AI-01. Output outside the candidate set is rejected. |
| FR-AI-03 | Suggest secondary button names from plugin header and readme. | S | The suggestion is shown pre-filled and is editable. |
| FR-AI-04 | Explain failed or partial run items and suggest next actions. | S | Explanations cite the error codes and events they are based on. |
| FR-AI-05 | Assistant chat with streaming responses and tool calling over the permitted tools ([07-AI-DESIGN §4](07-AI-DESIGN.md#4-tool-catalogue)). | M | The tool-permission matrix test passes. |
| FR-AI-06 | Tool calls classed `approve` are executed only after explicit user approval in the UI, under the approving user's identity and permissions. | M | No code path executes an `approve` tool without an approval record (test). |
| FR-AI-07 | Enforce per-request token limits and daily and monthly USD budgets; show a usage dashboard; refuse cleanly when over budget. | M | A request over budget returns `ai.budget_exceeded` and makes no provider call. |
| FR-AI-08 | Memory: per-user notes the assistant may use, which the user can view, edit and delete, with a global off switch. | C | Deleted memory is never sent again (test). |
| FR-AI-09 | Evaluation harness (`omnisync eval resolver`) over the labelled legacy dataset, producing a metrics report. | M | The report contains the NFR-AI metrics. |

### 4.15 User experience

| ID | Requirement | Pri | Acceptance criteria |
|---|---|---|---|
| FR-UX-01 | Dashboard: inbox count, needs-input count, active run, last runs, store/S3/AI health, catalog freshness, lease status. | M | Every widget has loading, empty and error states. |
| FR-UX-02 | Command palette (`Ctrl+K`) for navigation and search across archives, runs and catalog. | S | Given the palette opened with `Ctrl+K`, when the user types "R-41" and presses Enter, then the run opens; arrow keys move the selection, `Esc` closes and returns focus, and axe reports no violations. |
| FR-UX-03 | Notifications: in-app toasts plus a notification centre (header popover listing the last 100 global events); optional browser notifications when a run completes. | S | Browser notifications are off until the user opts in and the browser grants permission. The notification centre lists events received while the tab was closed (replayed from the events table). |
| FR-UX-04 | Preferences: colour mode (system by default, light, dark), density (compact by default, comfortable), notification settings, stored per user ([08-UX-DESIGN §5](08-UX-DESIGN.md#5-visual-design-system)). | S | A new user sees the OS colour mode. A chosen mode persists across sessions and is applied before first paint (no flash of the wrong theme). |

## 5. Non-functional requirements

Reference machine for performance targets: 4-core x86-64, 16 GB RAM, SSD, Windows 11. Network latency to the stores is excluded unless stated.

| ID | Requirement | Target | Verification |
|---|---|---|---|
| NFR-PERF-01 | API latency for list and search endpoints | p95 < 200 ms with a catalog of 10,000 entries per store | Benchmark test in CI ([09-TESTING §6](09-TESTING-STRATEGY.md#6-performance-testing)) |
| NFR-PERF-02 | Front-end load | Initial JS ≤ 300 KB gzip; LCP < 1.5 s on localhost | Bundle-size check; Lighthouse CI |
| NFR-PERF-03 | Memory during inspection and upload | Peak RSS growth < 300 MB for a 1.5 GB archive containing a 700 MB nested bundle | Memory benchmark |
| NFR-PERF-04 | Inspection time | < 10 s for a 500 MB archive | Benchmark |
| NFR-PERF-05 | Progress event latency | Server event → browser render < 1 s p95 | E2E timing test |
| NFR-REL-01 | Crash consistency | After a forced kill at any stage: no duplicate PUT for completed items; resumed run reaches a consistent final state | Chaos test suite |
| NFR-REL-02 | Durability | No committed run or audit record is lost on power loss (SQLite WAL, `synchronous=FULL`) | Configuration test plus chaos test |
| NFR-REL-03 | Mutual exclusion across instances | (a) No instance starts a mutating external call after its lease deadline. (b) In schedules without process freezes, at most one instance holds the lease at any S3-time instant. (c) In freeze schedules, at most one stale in-flight call completes after a takeover (residual risk T-11). Verified over 1,000 randomised trials. | Lease property test |
| NFR-REL-04 | Graceful degradation | A store outage affects only that store's items (circuit breaker); AI outage affects only AI features | Fault-injection tests |
| NFR-SEC-01 | Dependency hygiene | Zero known high or critical vulnerabilities (pip-audit, npm audit) at release | CI gate |
| NFR-SEC-02 | Static analysis | ruff (including bandit rules) and eslint security rules clean; no secrets in the repository (gitleaks) | CI gate |
| NFR-SEC-03 | Secret confidentiality | No secret value in any API response, log line, audit record or AI prompt | Automated canary-secret scan test |
| NFR-SEC-04 | Web security | Host-header allow-list, CSRF protection, strict CSP, SameSite=Strict cookies; checks from OWASP ASVS 4.0.3 Level 2 chapters V2 (authentication), V3 (sessions), V4 (access control), V5 (validation), V12 (files), V13 (API) | Security test suite plus checklist ([06-SECURITY §8](06-SECURITY.md#8-verification)) |
| NFR-A11Y-01 | Accessibility | WCAG 2.2 AA; zero serious or critical axe violations on every screen; every function operable by keyboard; visible focus | axe in Playwright plus manual keyboard and screen-reader pass |
| NFR-MAINT-01 | Code quality gates | ruff, mypy `--strict`, eslint, `tsc --strict` clean | CI gate |
| NFR-MAINT-02 | Test coverage | Backend domain core ≥ 90% branch; backend overall ≥ 80%; front end ≥ 70% lines | CI gate |
| NFR-OBS-01 | Diagnosability | Every log line is JSON with `request_id` and, where applicable, `run_id` and `item_id`; every failure has a stable error code | Log schema test |
| NFR-PORT-01 | Platforms | Windows 11 (primary); Linux and macOS for development | CI matrix |
| NFR-DR-01 | Backup and recovery | RPO ≤ 24 h; RTO ≤ 15 min | Restore drill |
| NFR-AI-01 | Resolver quality | Precision ≥ 90% on confident answers; false-confident rate ≤ 2%; correct key in the deterministic top-10 for ≥ 95% of cases | `omnisync eval resolver` report |
| NFR-AI-02 | AI cost control | Default budgets USD 2/day and USD 20/month per instance; never exceeded by more than one in-flight request | Budget tests |
| NFR-AI-03 | AI latency | Resolver suggestion p95 < 8 s; assistant first token p95 < 3 s | Eval report |
| NFR-PRIV-01 | Data minimisation | Only filenames, slugs, versions, plugin and readme excerpts (≤ 4 KB per field), catalog keys, product titles and IDs, run and error metadata, the user's own chat messages, and the user's own memory notes are sent to AI providers | Prompt-content test plus a review of the [07-AI-DESIGN §8](07-AI-DESIGN.md#8-privacy) data list |
| NFR-UX-01 | Responsiveness | Fully usable from 320 CSS px (WCAG 1.4.10 reflow) to 2560 px wide; no horizontal scrolling except inside data tables | Playwright viewport tests |

## 6. Assumptions and constraints

| # | Item |
|---|---|
| A1 | The stores use the WooCommerce REST API v3 and the WooCommerce Amazon S3 Storage shortcode format, as in legacy. |
| A2 | Both instances can reach the S3 API. S3 conditional writes are available (standard for general-purpose buckets). |
| A3 | Lease timing uses S3 server time (estimated from S3 `Date` headers), not local clocks, so correctness does not depend on PC clocks. Local clocks should still be NTP-synchronised; skew above 10 s is reported ([03-ARCHITECTURE §6.4](03-ARCHITECTURE.md#64-clock-skew)). |
| A4 | Archive volume: up to 100 archives per day, single files up to 2 GiB. |
| C1 | The licence must remain GPL-3.0 because legacy code is ported ([ADR-0011](adr/0011-license-gpl-3.md)). |
| C2 | No live store or S3 writes during development without explicit owner approval. |
