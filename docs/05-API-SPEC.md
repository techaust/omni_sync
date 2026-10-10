# API Specification

> **Status:** Proposed · **Owner:** TecHaust Technologies (@techaust) · **Last reviewed:** 2026-10-11 · **Audience:** backend and front-end engineers, testers

This is the HTTP contract between the SPA and the backend. Once implemented, the generated OpenAPI document at `/api/v1/openapi.json` is authoritative and the TypeScript client is generated from it. This document must change in the same PR as any contract change.

## 1. Conventions

| Topic | Rule |
|---|---|
| Base path | `/api/v1` |
| Format | JSON (`application/json; charset=utf-8`); field names in `snake_case`; timestamps ISO-8601 UTC with `Z` |
| Methods | `GET` is safe; `POST` creates or triggers; `PATCH` partially updates; `PUT` replaces; `DELETE` removes |
| Idempotency | `POST /runs` and `POST /cleanup/execute` accept an `Idempotency-Key` header (UUID). A repeat within 24 h returns the original response. |
| Pagination | Cursor-based: `?limit=50&cursor=<opaque>`. Responses look like `{ "items": [...], "next_cursor": "..." \| null }`. Maximum `limit` is 200. |
| Filtering and sorting | Explicit query parameters per endpoint; `sort=field` or `sort=-field` from a per-endpoint allow-list |
| Versioning | Breaking changes need `/api/v2`. Additive changes are allowed in v1. |
| Request IDs | Every response carries `X-Request-ID`, which is also logged. A client-supplied value is accepted if it is a UUID. |

## 2. Authentication and request protection

- **Session cookie:** `omnisync_session`; `HttpOnly`; `SameSite=Strict`; `Path=/`; no `Secure` flag, because the app is served over plain HTTP on loopback ([06-SECURITY §7](06-SECURITY.md#7-security-controls)).
- **CSRF:** every non-GET request must send `X-CSRF-Token` equal to the session's token, which is returned by `GET /auth/me`. An `Origin` header, if present, must match the allowed origins.
- **Host check:** requests whose `Host` is not in `{127.0.0.1:<port>, localhost:<port>}` are rejected with 421 before routing (DNS-rebinding defence).
- **Unauthenticated endpoints:** `GET /health`, `GET /setup/status`, `POST /setup`, `POST /auth/login`, and the static SPA.

## 3. Errors

Errors use RFC 9457 Problem Details (`application/problem+json`):

```json
{
  "type": "https://omnisync.local/problems/rule.unknown_target",
  "title": "Unknown mapping key",
  "status": 422,
  "detail": "Mapping key 'modula-prro' does not exist in any enabled store catalog.",
  "code": "rule.unknown_target",
  "instance": "/api/v1/aliases",
  "request_id": "0b7f…",
  "errors": [{ "field": "mapping_key", "message": "not found" }]
}
```

| HTTP | Typical codes |
|---|---|
| 400 | `request.invalid` |
| 401 | `auth.required`, `auth.session_expired`, `auth.invalid_credentials` (also returned for locked accounts, so lockouts do not reveal usernames) |
| 403 | `auth.forbidden`, `auth.csrf_failed` |
| 404 | `resource.not_found` |
| 409 | `config.conflict`, `run.already_active` (same mutating kind already queued or running on this instance), `plan.stale`, `setup.already_done`, `secret.set_by_environment` |
| 413 | `archive.too_large` |
| 415 | `archive.not_zip` |
| 421 | `request.bad_host` |
| 422 | `validation.failed`, `rule.unknown_target`, `idempotency.mismatch`, `confirmation.mismatch` |
| 429 | `rate_limited`, `ai.budget_exceeded` |
| 500 | `internal` (detail is generic; the full error is only in the logs) |
| 502/503 | `upstream.s3`, `upstream.woocommerce`, `upstream.ai`, `circuit_open` |

Pipeline and domain codes stored on items (not HTTP statuses) include `archive.corrupt`, `archive.limits_exceeded`, `archive.no_installable`, `match.unmapped`, `match.ambiguous_entry`, `version.downgrade`, `s3.access_denied`, `s3.upload_failed`, `wc.auth_failed`, `wc.entry_not_found`, `wc.write_ignored`, `wc.http_error`, `circuit_open`, and `lease.lost` (with detail `renewal_failed`, `lease_taken` or `clock_uncertain`). Lease contention between instances is never an HTTP error: the run waits in `waiting_for_lease`. The full list is maintained in `backend/omnisync/errors.py` and rendered in the UI with help text.

## 4. Endpoints

Minimum role: **V** = viewer, **O** = operator, **A** = admin, **—** = unauthenticated. The complete permission matrix is in [06-SECURITY §4](06-SECURITY.md#4-authorization-model).

### 4.1 Setup, auth, users

| Method and path | Role | Purpose |
|---|---|---|
| `GET /setup/status` | — | `{ "setup_required": bool }` |
| `POST /setup` | — | `{setup_token, username, display_name, password}` creates the first admin. Returns 409 once any user exists. |
| `POST /auth/login` | — | `{username, password}` sets the cookie and returns `me` |
| `POST /auth/logout` | V | Revokes the session |
| `GET /auth/me` | V | `{id, username, display_name, role, preferences, csrf_token, instance: {id, name}}` |
| `POST /auth/password` | V | `{current_password, new_password}`; revokes other sessions |
| `PATCH /auth/preferences` | V | Theme, density, notifications |
| `GET /users` · `POST /users` · `PATCH /users/{id}` | A | Manage users (role, disable, reset password) |

### 4.2 Dashboard and health

| Method and path | Role | Purpose |
|---|---|---|
| `GET /dashboard` | V | Aggregated widget data (FR-UX-01) |
| `GET /health` | — | `{status: "ok"}` liveness only, no details |
| `GET /health/details` | V | Legacy `check` parity (FR-OBS-01). Credentials are reported as configured or missing only; secret sources are admin-only (`GET /secrets`) and values are never returned. |
| `GET /coordination` | V | Lease holder and shared-config version (FR-COORD-02) |

### 4.3 Archives

| Method and path | Role | Purpose |
|---|---|---|
| `POST /archives` | O | `multipart/form-data`, field `files` (1–50 files). Streamed. Returns the created archives (status `new`). |
| `GET /archives?location=inbox&status=&q=` | V | List |
| `GET /archives/{id}` | V | Detail, including the latest inspection |
| `POST /archives/{id}/inspect` | O | Re-inspect (for example after a rule change) |
| `POST /archives/{id}/trash` · `POST /archives/{id}/restore` | O | FR-INBOX-05 |

### 4.4 Runs

| Method and path | Role | Purpose |
|---|---|---|
| `POST /runs` | O | Create a pipeline run. Dry: `{kind: "pipeline", mode: "dry", archive_ids?: int[], options: {...}}`. Live: `{kind: "pipeline", mode: "live", plan_run_id: int}`. The live run takes its items (the plan's ready items) and options from the plan; sending `options` or `archive_ids` with `mode: "live"` returns 422. The plan must be a `succeeded` dry run created less than 15 min before this request (`plan.stale` otherwise, FR-RUN-04). Other run kinds have their own endpoints below. |
| `GET /runs?kind=&status=&mode=` | V | List |
| `GET /runs/{id}` | V | Detail with counters |
| `GET /runs/{id}/items` | V | Items with per-store results |
| `GET /runs/{id}/plan` | V | Plan (dry runs) |
| `POST /runs/{id}/cancel` | O | Request cancellation |
| `POST /runs/{id}/resume` | O | Resume an `interrupted` run, or a `failed` run whose error code is `lease.lost` |
| `GET /runs/{id}/events` | V | **SSE** stream (section 5) |
| `GET /runs/{id}/report?format=json\|csv` | V | FR-RUN-03 |

Example `options` object, with defaults:

```json
{ "force": false, "trust_recorded_hashes": false, "verify_s3": true,
  "keep_in_inbox": false, "concurrency": 4, "request_delay_s": 0.15 }
```

### 4.5 Needs input

| Method and path | Role | Purpose |
|---|---|---|
| `GET /needs-input?status=open&kind=` | V | Queue |
| `GET /needs-input/{id}` | V | Detail with deterministic candidates |
| `POST /needs-input/{id}/suggest` | O | Request an AI suggestion (FR-AI-02/03); returns an `ai_suggestion` |
| `POST /needs-input/{id}/resolve` | O | Kind-specific body. `unmapped`: `{mapping_key, rule: "alias" \| "override"}`. `secondary_name`: `{name}`. `downgrade`: `{decision: "approve" \| "reject", reason}`. `ambiguous_entry`: `{store_key, entry_id}`. |
| `POST /needs-input/{id}/dismiss` | O | `{reason}` |

### 4.6 Catalog and rules

| Method and path | Role | Purpose |
|---|---|---|
| `GET /catalog?store=&q=&primary=&conflicts=` | V | FR-CAT-02 |
| `GET /catalog/conflicts` | V | FR-CAT-03 |
| `POST /catalog/refresh` | O | `{stores: ["gplfamily", ...] \| "all"}` creates a `catalog_refresh` run (needs the lease) |
| `GET /aliases` · `POST /aliases` · `PUT /aliases/{detected_slug}` · `DELETE /aliases/{detected_slug}` | V / O / O / O | FR-RULE-01 |
| `GET /overrides` · `POST` · `PUT /overrides/{filename}` · `DELETE` | V / O / O / O | Same pattern |
| `GET /download-names` · `POST` · `PUT /download-names/{slug}` · `DELETE` | V / O / O / O | Same pattern |
| `GET /rules/history?type=&key=` | V | FR-RULE-03 |

Rule writes return `409 config.conflict` with `{"current": ..., "yours": ...}` when the shared config changed in an incompatible way.

### 4.7 Cleanup

| Method and path | Role | Purpose |
|---|---|---|
| `POST /cleanup/preview` | A | Creates a `cleanup_preview` run. It collects references live from every configured store and returns orphans plus pending deletes (FR-CLN-01) |
| `POST /cleanup/execute` | A | `{preview_run_id, object_keys: [...], confirmation: "DELETE <n>"}` creates a `cleanup_execute` run. `n` must equal `len(object_keys)` (`422 confirmation.mismatch` otherwise). After acquiring the lease, the run re-collects live references and deletes only keys still unreferenced (FR-CLN-02). |
| `GET /cleanup/pending` | A | Pending deletes from all instances |
| `POST /cleanup/pending/{object_key}/keep` | A | Discard a pending delete and keep the object (`{reason}`, audited) |

### 4.8 Stores, settings, secrets

| Method and path | Role | Purpose |
|---|---|---|
| `GET /stores` | V | List (no secrets) |
| `POST /stores` · `PATCH /stores/{key}` · `DELETE /stores/{key}` | A | Edits the shared config (FR-SET-01) |
| `POST /stores/{key}/test` | A | Connection test |
| `GET /settings` · `PATCH /settings` | V / A | Instance settings (run defaults, AI, retention) |
| `POST /settings/s3/test` | A | FR-SET-02 |
| `GET /secrets` | A | `[{name, source: "environment" \| "keyring" \| "unset", updated_at}]` |
| `PUT /secrets/{name}` | A | `{value}` stored in the keyring. The response never echoes the value. Returns 409 if the secret is set by an environment variable. |
| `DELETE /secrets/{name}` | A | Remove from the keyring |

### 4.9 Audit, backups, import

| Method and path | Role | Purpose |
|---|---|---|
| `GET /audit?actor=&action=&from=&to=&q=&scope=local\|shared` | V | FR-AUD-01 |
| `GET /audit/export?…` | A | CSV |
| `GET /backups` · `POST /backups` | A | FR-OPS-02 (restore is CLI-only) |
| `POST /import/legacy` | A | `{legacy_path, dry_run: bool, push_rules: bool = true}` creates a `legacy_import` run ([04-DATA-MODEL §5](04-DATA-MODEL.md#5-legacy-import-mapping)) |
| `GET /events?after=<id>&limit=100` | V | Recent global events for the notification centre (FR-UX-03) |

### 4.10 AI

| Method and path | Role | Purpose |
|---|---|---|
| `GET /ai/status` | V | Enabled, provider, model, budget remaining |
| `GET /ai/conversations` · `POST /ai/conversations` | V | A viewer gets read-only tools only |
| `GET /ai/conversations/{id}` · `DELETE /ai/conversations/{id}` | V (own) | |
| `POST /ai/conversations/{id}/messages` | V | `{content}`; the response is a `text/event-stream` body (section 5.3), read with `fetch` |
| `GET /ai/messages/{id}` | V (own) | The persisted message, including tool calls. Used after a dropped stream. |
| `POST /ai/tool-calls/{id}/approve` · `POST /ai/tool-calls/{id}/reject` | per tool | Approval executes with the approver's permissions (FR-AI-06) |
| `GET /ai/usage?from=&to=` | V | FR-AI-07 |
| `GET /ai/memory` · `POST` · `PATCH /ai/memory/{id}` · `DELETE /ai/memory/{id}` | V (own) | FR-AI-08 |

## 5. Server-sent events

### 5.1 Transport

- `Content-Type: text/event-stream`. A heartbeat comment (`: ping`) is sent every 15 s.
- Each event has `id:` equal to `events.id`, which is global and monotonic. On reconnect the browser sends `Last-Event-ID` and the server replays the missed events from the database (FR-RUN-02).
- **One stream per browser:** `GET /api/v1/events/stream?runs=<id>,<id>` (role V) carries global events (`run.status`, `needs_input.*`, `archive.*`, `lease.*`) plus detailed item events for the listed runs. A `SharedWorker` owns the connection and fans events out to all tabs. To change the subscription it opens a new connection with `?last_event_id=<id>` (the query parameter is needed because `EventSource` cannot set the `Last-Event-ID` header on a new connection). The server treats it like the header. ([ADR-0006](adr/0006-server-sent-events.md)). `GET /runs/{id}/events` remains for tools and tests.

### 5.2 Run event types

| `event:` | `data` fields |
|---|---|
| `run.status` | `run_id, status, counters` |
| `item.stage` | `item_id, archive_id, stage, status` |
| `item.store` | `item_id, store_key, status, error_code?` |
| `item.completed` / `item.failed` | `item_id, status, error_code?, error_detail?` |
| `log` | `level, message, item_id?` |
| `needs_input.created` | `id, kind, subject` |
| `lease.waiting` / `lease.acquired` / `lease.lost` | `holder?, expires_at?` |

### 5.3 AI stream event types

The AI response stream is the body of the `POST` request, so it is read with `fetch` and a stream reader, not `EventSource`. It has no event IDs and no automatic reconnection. The server always completes and persists the message even if the client disconnects. After a dropped connection, the client loads the final message with `GET /ai/messages/{id}` (the ID is sent in the first event).

| `event:` | `data` fields |
|---|---|
| `message.start` | `message_id` |
| `message.delta` | `text` |
| `tool.call` | `tool_call_id, tool_name, risk_class, arguments, status` |
| `tool.result` | `tool_call_id, summary` |
| `message.done` | `message_id, input_tokens, output_tokens, cost_usd` |
| `error` | Problem Details object |

## 6. Rate limits

| Scope | Limit |
|---|---|
| `POST /auth/login` | 10 per minute per username plus lockout (FR-AUTH-02) |
| `POST /setup` | 5 per minute |
| AI message posts | 20 per minute per user, plus budgets |
| Everything else | 600 per minute per session |

Exceeding a limit returns 429 with `Retry-After`.
