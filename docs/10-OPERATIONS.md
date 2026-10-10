# Operations Guide

> **Status:** Accepted (approved design) – procedures describe the designed system and will be validated on a clean Windows 11 machine in Milestone 7 · **Owner:** TecHaust Technologies (@techaust) · **Last reviewed:** 2026-10-11 · **Audience:** the owner, the colleague, whoever maintains an installation

**Important:** OmniSync 2.0 is under construction. **Available since Milestone 3:** installation from source (section 2), `serve`, `create-admin`, `reset-password`, first-run setup of the admin account (section 4, step 1), users and roles. Everything else in this guide (S3, stores, AI, runs, backups, import, cutover, `install-task`) arrives with the milestone named in [11-ROADMAP](11-ROADMAP.md). Until cutover, keep using the legacy tool.

Commands use `uv run python -m omnisync …` rather than `uv run omnisync …`. Both work, but on PCs where Windows Smart App Control blocks generated `.exe` launchers, only the `python -m` form works ([ADR-0014](adr/0014-milestone-3-implementation-adjustments.md)).

## 1. Requirements

| Item | Requirement |
|---|---|
| OS | Windows 11 (primary). Linux and macOS work for development. |
| Runtime | Python 3.12 and `uv`; Node.js 24 or newer only to build the front end from source (release builds ship the compiled front end) |
| Disk | ≥ 10 GB free in the data directory (archives plus processed copies plus backups) |
| Network | HTTPS to `s3.<region>.amazonaws.com`, every store URL, and the AI provider (optional). Upload bandwidth of at least 2 Mbit/s (each upload call must finish within 60 s, [03-ARCHITECTURE §5.2](03-ARCHITECTURE.md#52-item-stages-live-pipeline)). |
| Clock | Windows time sync enabled (accurate timestamps). Lease safety uses S3 time, not the PC clock ([03-ARCHITECTURE §6.4](03-ARCHITECTURE.md#64-clock-skew)). |
| Accounts | Per instance: an IAM user ([06-SECURITY §5](06-SECURITY.md#5-credential-scoping-for-multiple-instances)), a WooCommerce REST key pair per store, an optional AI API key |

## 2. Installation

1. Install Python 3.12, uv and (until release archives exist, Milestone 7) Node.js 24: `winget install Python.Python.3.12`, `winget install astral-sh.uv`, `winget install OpenJS.NodeJS`.
2. Get the code: `git clone https://github.com/techaust/omni_sync.git D:\Apps\OmniSync`.
3. Install the back end exactly as locked: in `backend\`, run `uv sync --frozen`.
4. Build the front end: in `frontend\`, run `npm ci` then `npm run build`. This creates `frontend\dist`, which the server serves.
5. Verify: in `backend\`, `uv run python -m omnisync --version` prints the version.
6. Start the server: `uv run python -m omnisync serve`. Expected console output includes `Listening on http://127.0.0.1:8765`. Until setup is complete it also prints `Setup token: XXXXX-XXXXX-XXXXX-XXXXX-XXXXX-X` (a new token at every start) and writes the same token to `<data dir>\setup-token.txt`.
7. Open `http://127.0.0.1:8765` and complete the setup wizard (section 4).

**Start automatically at log-on (optional, Milestone 7).** Run `uv run python -m omnisync install-task` once to create a Windows Task Scheduler entry that runs `omnisync serve` at log-on for the current user. The task sets its working directory to the program folder and passes the current `OMNISYNC_DATA_DIR`, so it uses the same data as a manual start. Because no console is visible for the task, read the setup token from `setup-token.txt` if setup is still pending. Remove it with `uv run python -m omnisync uninstall-task`.

## 3. Configuration reference

Precedence: environment variable → `<data dir>/omnisync.toml` → default. Runtime settings (stores, rules, run defaults, AI) are edited in the UI.

| Setting | Env var | Default | Notes |
|---|---|---|---|
| Data directory | `OMNISYNC_DATA_DIR` | `%LOCALAPPDATA%\OmniSync` | Everything instance-local lives here ([04-DATA-MODEL §4](04-DATA-MODEL.md#4-file-system-layout)) |
| Bind host | — | `127.0.0.1` | Fixed; not configurable, because remote access is a non-goal and the Host check (C-04) assumes it |
| Port | `OMNISYNC_PORT` | `8765` | |
| Instance name | `OMNISYNC_INSTANCE_NAME` | computer name | Shown in the lease holder and audit |
| Log level | `OMNISYNC_LOG_LEVEL` | `INFO` | |
| Max upload size | `OMNISYNC_MAX_UPLOAD_BYTES` | `2147483648` | FR-INBOX-03 |
| Legacy path (import only) | `OMNISYNC_LEGACY_ROOT` | — | |

### 3.1 Secrets

Secrets are entered in **Settings → Secrets**, which stores them in the Windows Credential Manager under service `omnisync`. Alternatively they can be set as environment variables, which take precedence and cannot be edited from the UI. The names are compatible with legacy:

| Secret | Environment variable |
|---|---|
| AWS access key ID / secret | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` |
| Store consumer key / secret | `<STOREKEY>_WC_CONSUMER_KEY`, `<STOREKEY>_WC_CONSUMER_SECRET`, where `<STOREKEY>` is the store key upper-cased with `-` replaced by `_` (e.g. `GPLFAMILY_WC_CONSUMER_KEY`; store `my-shop` gives `MY_SHOP_WC_CONSUMER_KEY`) |
| Anthropic API key | `ANTHROPIC_API_KEY` |
| OpenAI API key | `OPENAI_API_KEY` |

A legacy installation already stores the AWS and WooCommerce secrets in the keyring under service `omnisync` with these names, so on the same PC they are picked up without re-entry.

## 4. First-run setup

1. Enter the setup token (from the console or `<data dir>\setup-token.txt`), then create the admin account (password of at least 12 characters).
2. **S3:** bucket `productrepo`, region `ap-south-1`, control prefix `_omnisync/`. Click **Test**. All checks (list, read, write probe, conditional write, delete permission) should show their result. Delete shows "not permitted" on an operator instance.
3. **Stores:** if the shared config already exists (the second instance), stores appear automatically. Otherwise add GPLFamily (`https://www.gplfamily.com`) and PluginWay (`https://www.pluginway.com`). Enter each store's consumer key and secret and click **Test**.
4. **Catalog:** run **Refresh catalog** (about 24 pages per store) or import legacy data (section 9).
5. **AI (optional):** choose a provider, paste the API key, pick a model, review the privacy table and budgets, then enable.
6. Open **Health**. Every line should be green before the first live run.

## 5. Day-to-day operation

1. Drop vendor ZIPs onto the **Inbox** page, or copy them into `<data dir>\inbox`.
2. Wait for inspection (a few seconds each). Resolve anything shown in **Needs input**.
3. Select the archives (or all ready ones) and click **Plan run**. Review the plan.
4. Click **Run live** within 15 minutes. Watch progress on the run page.
5. Review the result. `partial` items show per-store errors; use **Explain** or retry them with a new run.
6. Weekly, as admin: **Cleanup → Preview**, then review the *Orphans* and *Pending deletes* tabs and delete (or keep) after review.
7. After adding products in WooCommerce: **Catalog → Refresh**. (Catalogs also refresh automatically once a day, FR-CAT-04.)

## 6. Two-instance setup (owner and colleague)

The colleague works as an **operator**. The owner performs the setup of the colleague's instance and keeps its admin password.

1. **Credentials (owner).** Create the colleague's IAM user with the operator policy ([06-SECURITY §5](06-SECURITY.md#5-credential-scoping-for-multiple-instances)) and a WooCommerce key pair per store for him.
2. **Install (on the colleague's PC).** Install OmniSync (section 2) with `OMNISYNC_INSTANCE_NAME=colleague-pc`.
3. **Setup (owner, on the colleague's PC).** Complete the setup wizard (section 4), creating an **admin** account whose password only the owner knows. Use the same bucket and control prefix; the stores appear from the shared config. Enter the colleague's IAM and WooCommerce credentials under **Settings → Secrets**. S3 **Test** shows delete as "not permitted", which is expected.
4. **Colleague account (owner).** Under **Settings → Users**, create an **operator** account for the colleague and sign out. The colleague signs in with it and changes his password on the Account page.
5. **Day-to-day.** The colleague uploads, plans, runs and resolves needs-input items. Superseded objects his runs cannot delete appear as pending deletes in the owner's **Cleanup** page.
6. **Coordination.** Both instances show the lease holder in the header. A live run on one instance makes the other wait (`waiting_for_lease`) rather than run concurrently.

The operator role limits what the UI offers. The enforceable limit is the colleague's IAM policy, because whoever controls a PC can ultimately control the software on it.

## 7. Backup and restore

**What is backed up:** the SQLite database (online backup API, consistent while running) and `omnisync.toml`. Secrets are *not* included; record them in your password manager. Archives in `processed/` are not included; their published copies are in S3.

- **Automatic:** at 03:00 local time while the server is running, and at start-up if the newest backup is older than 24 h; keeps the 14 newest (FR-OPS-02).
- **On demand:** **Settings → Backups → Create backup**, or `uv run python -m omnisync backup`.

**Restore** (target RTO ≤ 15 min, NFR-DR-01):

1. Stop the server (`Ctrl+C`, or end the scheduled task).
2. Run `uv run python -m omnisync restore <path-to-backup.zip>`. It verifies the archive, moves the current database to `omnisync.db.pre-restore-<UTC>`, restores the database, and runs migrations if the backup is older than the installed version.
3. Start the server and open **Health**. Expect green checks and the run history up to the backup time.
4. The shared config is pulled from S3 automatically, so rules made after the backup are not lost.

**Disaster recovery (PC lost):** install on a new PC (section 2), restore the newest backup, and re-enter the secrets. If no backup exists, set up a fresh instance: the shared config and catalog refresh restore the operational state, and only local run history is lost.

## 8. Upgrade

1. Read the release notes in [CHANGELOG](../CHANGELOG.md), especially *Breaking* and *Migration* sections.
2. Create a backup (section 7).
3. Stop the server, replace the program files (`git pull`, or unzip the release), run `uv sync --frozen` in `backend\` and `npm ci` plus `npm run build` in `frontend\`.
4. Start the server. Database migrations run automatically. If they fail, the server refuses to start and logs `db.migration_failed`; restore the backup and report the issue.
5. Upgrade the other instance promptly. The shared config carries a `schema` number, and an instance whose schema is older than the document refuses to push changes until it is upgraded.

## 9. Cutover from legacy

1. Make sure the legacy tool is idle (no `app.lock` in the legacy folder).
2. Install and set up OmniSync 2.0 on the owner's PC (sections 2 and 4, without a catalog refresh).
3. Go to **Settings → Import**, set the legacy path (e.g. `D:\…\SOFTWARE\OmniSync`), and run **Dry-run import**. Review the counts.
4. Run **Import**. It copies data; the legacy folder is never modified.
5. Run a **dry run** on a few known archives and compare the plan with a legacy `--dry-run` for the same files. The plans must agree.
6. **Retire the legacy tool:** rename the legacy `run_omnisync.py` to `run_omnisync.py.retired` and delete the legacy `.opencode\commands` folder so the slash commands disappear. **Do not delete the `omnisync` entries in Windows Credential Manager**, because OmniSync 2.0 uses the same entries. **Never run legacy `cleanup-s3` again.** It does not know the control prefix and would treat the `_omnisync/` objects as orphans.
7. Set up the colleague's instance (section 6).

## 10. Monitoring

- The **Health** page is the operational overview (FR-OBS-01).
- The log is `<data dir>\logs\omnisync.jsonl` (JSON lines). Example filter (default data directory): `Select-String '"level":"ERROR"' "$env:LOCALAPPDATA\OmniSync\logs\omnisync.jsonl"`.
- Every error shown in the UI includes a request ID that can be searched for in the log.

## 11. Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| Browser shows "Misdirected request" (421) | Opened through a hostname other than `127.0.0.1` or `localhost` | Use `http://127.0.0.1:8765` |
| `serve` exits with "data directory in use" | Another `serve` already uses the data directory | Close it, or check Task Manager for `omnisync` |
| Run stuck in `waiting_for_lease` | The other instance holds the lease | Check the header for holder and expiry. If its PC is off or the server crashed, the lease is taken over automatically after expiry (at most about 6 min). |
| Health: "PC clock differs from S3 by 45 s" | Windows time not synced (affects displayed times only) | `w32tm /resync`, then re-check |
| Run waits with reason `clock_uncertain`, or fails with `lease.lost` | Network too slow to estimate S3 time (round trip > 3 s), or the lease could not be renewed | Check the internet connection. A waiting run starts by itself once the network recovers; a failed run can be resumed (**Resume** on the run page). |
| Item `failed: s3.access_denied` | IAM policy missing `s3:ListBucket` or `GetObject` | Fix the policy ([06-SECURITY §5](06-SECURITY.md#5-credential-scoping-for-multiple-instances)) |
| Item `failed: wc.auth_failed` | Wrong or revoked WooCommerce key | Replace the secret; **Test** the store |
| Store shows "breaker open" | 5 consecutive failures | Wait 60 s; check the store site is up |
| Many `unmapped` items after adding products | Catalog out of date | **Catalog → Refresh** |
| `Failed to spawn` or `An Application Control policy has blocked this file` when running a command | Windows Smart App Control blocks an unsigned launcher or native module | Use the `uv run python -m …` form of the command. If a Python package's native module is blocked, report it: the fix is a pure-Python build, as for SQLAlchemy (ADR-0014). |
| `config.conflict` when saving a rule | The other instance changed the same rule | Choose "Keep theirs" or "Apply mine" |
| AI unavailable | Provider down, key invalid, or budget exceeded | Check **AI status**. Work continues with deterministic candidates. |

## 12. Uninstall

1. Optionally create a final backup.
2. Run `uv run python -m omnisync uninstall-task` if auto-start was installed.
3. Delete the program folder and, if no longer needed, the data directory.
4. Remove the secrets: Windows **Credential Manager → Generic credentials**, entries starting with `omnisync`.
5. Revoke this instance's IAM access keys and WooCommerce API keys.
