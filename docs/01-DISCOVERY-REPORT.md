# Discovery Report: Legacy OmniSync 1.1.0

> **Status:** Accepted · **Owner:** TecHaust Technologies (@techaust) · **Last reviewed:** 2026-10-11 · **Audience:** owner, engineers

Milestone 1 deliverable. It describes how the legacy tool at `../OmniSync` works, based on its source, data and test execution. The legacy repository was analysed **read-only**; nothing in it was modified.

**Evidence labels:** **[V]** verified against source, data or execution · **[A]** assumption · **[P]** proposal. Source citations use the `legacy:<path>:<lines>` form, which `scripts/check_docs.py` verifies.

## 1. What OmniSync does

OmniSync is a **release-publishing pipeline for a GPL WordPress product business** that runs two WooCommerce storefronts, **GPLFamily.com** and **PluginWay.com**. Both sell the same catalog of about 2,385 plugins and themes. **[V]**

You drop vendor ZIPs into `inbox/`. For each archive, OmniSync does the following ("process once, sync twice"):

1. **Identifies the installable** inside the ZIP: slug and version, covering plugins, themes, Envato wrappers and nested bundles. **[V]** `legacy:omnisync/extractor.py:352-408`
2. **Matches it to a catalog product** through `stores/*/mapping.json` (slug → `{product_id, base, name, primary}`), which is built from the live WooCommerce API. **[V]** `legacy:omnisync/mappings.py:310-356`, `legacy:omnisync/engine.py:257-310`
3. **Uploads once** to S3 bucket `productrepo` (`ap-south-1`) as `<slug>-<version>.zip`. It skips the upload if version, MD5 and an S3 HEAD show the object is already there. **[V]** `legacy:omnisync/s3.py:84-102`, `legacy:omnisync/engine.py:585-645`
4. **Updates both stores.** For each one it does a GET-modify-PUT of the matching download entry, setting it to `[amazon_s3 object="<key>"]` (the WooCommerce Amazon S3 Storage shortcode), plus the button name and the `latest_version` / `latest_version_<slug>` meta. It then verifies that the response contains the new key. **[V]** `legacy:omnisync/wc.py:216-297`
5. **Deletes the superseded S3 object**, but only when both stores succeeded and no other mapping row references it. **[V]** `legacy:omnisync/engine.py:753-775`
6. **Moves the archive** to `inbox/processed/<slug>-<version>.zip`. **[V]** `legacy:omnisync/engine.py:777-793`

Supporting operations:

| Operation | What it does | Source |
|---|---|---|
| `sync-mapping` | Rebuilds the catalog from WooCommerce (24 pages × 100 per store at present) | `legacy:omnisync/mappings.py:400-416` |
| `cleanup-s3` | Lists S3 objects no live download references and deletes them after a prompt or `--yes` | `legacy:omnisync/cli.py:219-289` |
| `check` | Health and credential diagnostics | `legacy:omnisync/cli.py:292-363` |
| `secrets` | OS-keyring credential management | `legacy:omnisync/cli.py:366-415` |
| backup | Gitignore-aware source ZIP | `legacy:omnisync/backup.py` |

### 1.1 Live footprint (from legacy `data/` on 2026-10-11) **[V]**

- **Catalog:** 2,387 mapping keys per store, 2,385 unique products. The key sets of the two stores are identical.
- **State:** 110 slug records plus the `_meta` key (`legacy:data/state.json`). 103 are COMPLETED on both stores and 7 are UNMAPPED.
- **Run sizes:** 1 to 30 archives per batch. Measured durations were 23.5–357.4 s per batch (`legacy:.opencode/omnisync-runtime.json`).
- **Last live run (2026-10-10):** 1 file, 1 S3 PUT, 2 WooCommerce PUTs, 1 S3 delete (`legacy:data/omnisync.log`).
- **Secrets:** all six are in the Windows Credential Manager. `config.env` holds only URLs, bucket and region (values were not displayed during the analysis).
- **Recurring manual work:** `legacy:data/unmatched.log` shows the same products (latepoint, modula, gravity-perks, gutenkit, one-accessibility and others) unmapped run after run. Each was fixed by hand-editing `slug-aliases.json` (14 entries) or `product-slug-overrides.json` (27 entries), even though `legacy:AGENTS.md:104` forbids hand-editing engine-owned data, and no CLI exists for it.

## 2. Code structure **[V]**

| Module | Lines | Responsibility | Assessment |
|---|---|---|---|
| `engine.py` | 914 | Stage orchestration, fallback resolution, thread pool, report | Sound logic; very long functions (`_process_locked` takes 15 parameters); untyped dicts passed between stages |
| `extractor.py` | 408 | Three-layer version detection, slug detection, nested-ZIP unrolling | Strong and fuzz-tested; the most valuable code |
| `mappings.py` | 416 | WooCommerce pagination, mapping build, version-suffix healing, URL and shortcode parsing | Strong but intricate |
| `wc.py` | 300 | WooCommerce client: rate gate, retry, circuit breaker, per-product lock, verify-after-PUT | Good |
| `cli.py` | 548 | argparse dispatch, `app.lock`, check, secrets, cleanup | Adequate |
| `config.py` | 314 | env → keyring → `config.env` precedence (`legacy:omnisync/config.py:188-202`); module-level global dicts | Global mutable state; two stores hard-coded |
| `state.py` | 140 | JSONL write-ahead journal plus atomic snapshot | Correct, but a hand-built database |
| `locking.py` | 152 | Cross-platform stale-lock detection | Good, but complex because it relies on files |
| `s3.py`, `retry.py`, `circuit_breaker.py`, `utils.py`, `backup.py`, `errors.py`, `statuses.py` | small | Infrastructure | Good |

**Tooling:** ruff, mypy strict, bandit, pip-audit, pre-commit, GitHub CI on Python 3.11 and 3.12 (`legacy:.github/workflows/ci.yml`).

**Tests:** re-run on 2026-10-11 with `venv\Scripts\python.exe -m pytest -q`, giving **194 passed, 3 deselected (live), 74.05% branch coverage**. **[V]**

**Working tree:** the legacy repository has uncommitted changes. They add version-suffix healing to the mapping build (`legacy:omnisync/mappings.py:53-116`), regenerate both `mapping.json` files, and add matching tests. The 194 passing tests include them. The owner confirmed this working tree as the baseline on 2026-10-11 ([11-ROADMAP §2.1](11-ROADMAP.md#21-decisions-log)).

## 3. OpenCode dependencies **[V]**

The engine is plain Python and does **not** depend on OpenCode. OpenCode serves only as a UI shell and job launcher, built from Markdown prompts and helper scripts:

| OpenCode artifact | What it really does | 2.0 replacement |
|---|---|---|
| `/omnisync` with `omnisync_estimate.py`, `omnisync_run.py`, `omnisync_poll.py` | Starts a detached Windows process and tracks progress by **counting log phrases** such as `"exactly 1 S3 PUT"` (`legacy:.opencode/scripts/omnisync_poll.py:72-84`), working around the agent's shell timeout | Job runner with structured events over SSE (FR-RUN-02) |
| `/omnisync-status` | The LLM reads `state.json` and tallies statuses by hand; no status CLI exists (`legacy:.opencode/commands/omnisync-status.md:18`) | Dashboard (FR-UX-01) |
| `/omnisync-dryrun`, `/omnisync-check`, `/omnisync-refresh` | Fixed CLI invocations | Buttons and endpoints |
| `/omnisync-cleanup` | Dry run, then the **LLM** asks for confirmation, then runs `--yes` (`legacy:.opencode/commands/omnisync-cleanup.md:7-13`) | Preview → typed confirmation → execute (FR-CLN-02) |
| `/omnisync-bootstrap` | LLM-guided venv setup and credential prompts | Setup wizard (FR-AUTH-01, FR-SET-*) |
| `/omnisync-backup` | Runs `backup.py` | Backups (FR-OPS-02) |
| `AGENTS.md` "AI agent rules" | Prose rules telling an LLM not to edit state during runs (`legacy:AGENTS.md:100-108`) | Enforced in code by permissions and the lease |

**Interactive-terminal dependency.** Secondary download names are requested with `input()` on a TTY (`legacy:omnisync/engine.py:349-366`). Non-interactive runs, which includes every OpenCode run, defer those files to a later terminal session.

**Conclusion:** every OpenCode dependency can be replaced. OpenCode adds no capability of its own; it compensates for the lack of a long-running server and a UI.

## 4. Strengths to preserve

1. **Publishing guarantees:** one S3 PUT per new archive; reference-checked deletes; idempotent re-runs that skip finished work and retry failed stores; verify-after-PUT (`legacy:omnisync/wc.py:287-297`).
2. **Extraction engine:** header → readme/`package.json` → filename layers; deep changelog scan; nested bundles with a 768 MiB cap (`legacy:omnisync/extractor.py:51`); Envato prefix and item-ID stripping (`legacy:omnisync/extractor.py:212-228`).
3. **Matching cascade:** direct slug → alias → exact-file override → generic filename slug present on both stores → downgrade guard → audit ledger (`legacy:omnisync/engine.py:420-494`).
4. **Resilience:** retries at 2, 4 and 8 s (`legacy:omnisync/retry.py`); circuit breaker at 5 failures for 60 s (`legacy:omnisync/circuit_breaker.py`); shared rate gate; per-slug and per-product locks (`legacy:omnisync/engine.py:133-138`, `legacy:omnisync/wc.py:53-57`).
5. **Mapping healing:** strips fossil version suffixes such as `konstruktion-37-39`, guarded by a check that the result relates to the product (`legacy:omnisync/mappings.py:80-116`).
6. **Secrets hygiene:** keyring first; values never printed.
7. **Test corpus:** 194 tests that encode the edge cases. These are ported as executable specifications ([09-TESTING-STRATEGY §4](09-TESTING-STRATEGY.md#4-legacy-parity)).

## 5. Weaknesses, defects and risks

### 5.1 Defects and risks

| ID | Severity | Finding | Evidence | 2.0 resolution |
|---|---|---|---|---|
| D-01 | **High** | `cleanup-s3` does not take `app.lock`. Run during a pipeline run, it can list an object that was just uploaded but not yet linked in WooCommerce, and delete it, which breaks a live download. | `legacy:omnisync/cli.py:219-289` never calls `_acquire_app_lock` | FR-CLN-02, FR-COORD-01 |
| D-02 | Medium | Download-entry selection falls back to the button name (`Download`). On a multi-file product whose slug doesn't match, it can overwrite the wrong entry. | `legacy:omnisync/wc.py:180-182` | FR-PIPE-05 |
| D-03 | Medium | Preflight caches nested-ZIP payload bytes for every inbox archive before processing. The 768 MiB cap applies per archive, so a large batch can exhaust memory. | `legacy:omnisync/engine.py:335`, `legacy:omnisync/extractor.py:381` | FR-MATCH-05 |
| D-04 | Medium | `S3Uploader.exists()` treats any error (403, expired credentials) as "missing" and re-uploads, masking configuration errors as work. | `legacy:omnisync/s3.py:74-79` | FR-PIPE-03 |
| D-05 | Medium | Downgrades are only warned about on the main path and then applied to both stores. | `legacy:omnisync/engine.py:525-530`, `legacy:omnisync/wc.py:256-259` | FR-PIPE-06 |
| D-06 | Low | Progress monitoring depends on scraping log wording. | `legacy:.opencode/scripts/omnisync_poll.py:72-84` | FR-RUN-02 |
| D-07 | Low | Two stores hard-coded; tier-2 fallback assumes every product exists in both. | `legacy:omnisync/config.py:28-30`, `legacy:omnisync/engine.py:190-195` | FR-SET-01, FR-MATCH-03 |
| D-08 | Low | Unmapped resolution requires hand-editing JSON that the documentation forbids editing, and leaves no audit trail. | `legacy:AGENTS.md:104`, section 1.1 | FR-NI-02, FR-RULE-01 |
| D-09 | Low | Documentation drift: the docs claim 2,318 products and 190 tests; actual figures are 2,387 and 194. | `legacy:AGENTS.md:26`, `legacy:README.md:7` | [Documentation standard](DOCUMENTATION-STANDARD.md) (accuracy and maintainability criteria) |
| D-10 | Info | Duplicate mapping keys are resolved silently ("keeping first"), e.g. `layerslider` and `woocommerce-bulk-variations` each map to two product IDs. | `legacy:omnisync/mappings.py:344-349`, `legacy:data/omnisync.log` | FR-CAT-03 |

### 5.2 Architectural limitations

- JSON files, a journal and a lock file act as a database: no queries, no per-run history, and no transactional link between state and audit.
- No run history: `_meta.last_run` is overwritten each time (`legacy:omnisync/engine.py:893-911`), so earlier results survive only in a rotating 1 MB text log.
- No authentication or authorization: whoever has a shell has full power.
- One process tied to one folder layout on one machine.

### 5.3 Security posture

- **Good:** keyring storage; no secrets in git; HTTPS to the stores; bandit and pip-audit in CI.
- **Gaps:** no least-privilege guidance for the IAM key; store keys are read/write store-wide (unavoidable, but undocumented); log-injection sanitising covers `unmatched.log` only (`legacy:omnisync/engine.py:79-81`).
- **New surface in 2.0:** authentication, CSRF, DNS rebinding, uploads (zip bombs, path traversal), SSRF through store URLs, and prompt injection from archive text. All are addressed in [06-SECURITY](06-SECURITY.md#6-threat-model).

## 6. Where AI adds measurable value

| Capability | Why | Control | Spec |
|---|---|---|---|
| Unmapped-archive resolver | The main recurring manual task (section 1.1) | Proposal → human approval → audited shared rule | [07-AI-DESIGN §5](07-AI-DESIGN.md#5-ai-1-resolver-design) |
| Secondary button-name suggestion | Removes the terminal prompt | Pre-filled, human confirms | FR-AI-03 |
| Run triage | Faster recovery | Read-only, suggests actions | FR-AI-04 |
| Operator assistant with tools | Natural-language access to history and actions | Role-filtered tools; approval for every change | FR-AI-05, FR-AI-06 |

Deterministic work stays deterministic: extraction, hashing, matching rules, uploads, store writes, deletes and locking.

## 7. Disposition of legacy components

| Component | Decision |
|---|---|
| Extractor, version and slug logic | **Preserve and port** into pure functions; port tests 1:1 |
| Matching cascade, mapping build and heal, shortcode parsing | **Preserve and port**; generalise to N stores |
| WooCommerce client (retry, breaker, rate gate, verify) | **Preserve**; fix D-02 |
| S3 uploader | **Preserve**; fix D-04; stream from disk |
| JSON state, journal, lock file | **Replace** with SQLite ([ADR-0003](adr/0003-sqlite-database.md)) |
| `app.lock` and `locking.py` | **Replace** with an instance lock plus the S3 lease, which also covers cleanup (fixes D-01) |
| CLI | **Reduce** to `serve`, `check`, `import-legacy`, `backup`, `restore` and admin helpers |
| `.opencode/*` | **Remove** ([ADR-0001](adr/0001-local-web-application.md)) |
| Interactive prompts | **Replace** with the needs-input queue |
| `backup.py` | **Redesign** as database snapshot and restore |
| Legacy data | **Import** once ([04-DATA-MODEL §5](04-DATA-MODEL.md#5-legacy-import-mapping)) |

## 8. Outcome

The recommended architecture is specified in [03-ARCHITECTURE](03-ARCHITECTURE.md). The owner's decisions of 2026-10-11 are recorded in the [decisions log](11-ROADMAP.md#21-decisions-log):

- a local web app on each PC
- the colleague runs his own instance: the owner sets it up as admin, and the colleague works with an operator account and his own scoped credentials
- a configurable AI provider with Anthropic as the default
- N stores
- downgrades held for approval
- legacy data imported and the legacy tool retired

## 9. Unresolved questions

Open questions are maintained in [11-ROADMAP §2.2](11-ROADMAP.md#22-open-questions).

## 10. Coverage of this analysis

| Area | Covered |
|---|---|
| Source modules | All 16 files under `legacy:omnisync`, the CLI entry point and the OpenCode commands and scripts |
| Configuration | `pyproject.toml`, `config.env` (keys only), CI workflow, pre-commit |
| Runtime data | `state.json`, mappings, aliases, overrides, download names, fallback audit, unmatched log, run log, runtime history |
| Execution | Full offline test suite run; no live S3 or store calls were made |
| Not covered | Live store behaviour (read-only API checks were not performed during discovery); the sibling OmniSEO project |

## 11. Method

The analysis was performed on 2026-10-11 by reading the source and documentation, inspecting the data files with a read-only Python script, running the legacy test suite, and checking the working-tree diff (`git diff --stat`). The S3 conditional-write capability that the coordination design depends on was confirmed in the AWS S3 User Guide ("How to prevent object overwrites with conditional writes").

## 12. Constraints and risks

- **No live testing without consent.** Development uses fakes. Live smoke tests need explicit owner approval and dedicated test products ([09-TESTING-STRATEGY §12](09-TESTING-STRATEGY.md#12-live-testing-policy)).
- **Baseline:** the port uses the legacy working tree, including the uncommitted mapping-healing change (section 2), as confirmed by the owner. The legacy repository will be deleted after the project is complete, so its data must be imported first (risk R7).
- **Throughput:** WooCommerce rate limits, not CPU, set the throughput ceiling.
- **Risk register:** maintained in [11-ROADMAP §2.3](11-ROADMAP.md#23-risk-register).
