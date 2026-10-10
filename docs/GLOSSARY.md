# Glossary

> **Status:** Accepted · **Owner:** TecHaust Technologies (@techaust) · **Last reviewed:** 2026-10-11 · **Audience:** all readers

These are the canonical terms. When another document disagrees with this list, this list is correct and the other document must be fixed.

| Term | Definition |
|---|---|
| **Admin** | Role with full rights on an instance: settings, secrets, users, S3 cleanup, everything an operator can do. See [06-SECURITY §4](06-SECURITY.md#4-authorization-model). |
| **AI gateway** | Backend component that sends requests to the configured AI provider, enforces budgets and records usage. The browser never talks to a provider directly. |
| **Alias** | Shared rule mapping a *detected slug* to a *mapping key* when they differ, e.g. `elementor-pro` → `elementor-pro-package`. Successor of legacy `data/slug-aliases.json`. |
| **Approval** | Explicit human confirmation recorded in the audit log, required before a consequential action (live run, alias write proposed by AI, downgrade, deletion). |
| **Archive** | A ZIP file received through browser upload or the watched folder. One archive yields one *installable* to publish. |
| **Audit log** | Append-only record of consequential actions: who, what, when, from which instance, and the outcome. |
| **Bucket** | The S3 bucket holding published installables (default `productrepo`, region `ap-south-1`). |
| **Candidate** | A possible installable found inside an archive (slug + version + detection layer). Nested bundles can produce several. |
| **Catalog** | Per-store list of *catalog entries*, refreshed from the WooCommerce REST API (automatically once a day, and on demand). Successor of legacy `stores/*/mapping.json`. |
| **Catalog entry** | One row of the catalog: `mapping key → product ID, base, download name, is_primary` for one store. |
| **Circuit breaker** | Per-store guard that fails fast for 60 s after 5 consecutive server or network failures. |
| **Control prefix** | S3 key prefix (default `_omnisync/`) that holds coordination data: lease, shared config, shared audit, pending deletes. It is never treated as product content and never cleaned up. |
| **Detection layer** | How a version was found. `layer1` = file header `Version:`. `layer2` = readme `Stable tag:` or `package.json`/`composer.json`. `layer2-deep` = nested readme/changelog scan. `layer3` = filename. |
| **Downgrade** | An incoming version lower than the live version (PEP 440 comparison). It is held for approval ([ADR-0009](adr/0009-downgrades-held-for-approval.md)). |
| **Dry run** | A run that plans every operation and makes no external writes. With credentials present it can make read-only calls to improve accuracy. |
| **Instance** | One running OmniSync server on one PC, with its own local database. Several instances can share the same bucket and stores. |
| **Installable** | The WordPress plugin or theme ZIP that is published to S3 and linked from the stores. |
| **Lease** | Time-limited (300 s, renewed every 30 s) exclusive right to perform mutating operations against the shared bucket and stores. It is stored at `<control prefix>control/lease.json` and guarded by S3 conditional writes ([ADR-0007](adr/0007-multi-instance-coordination-via-s3.md)). |
| **Legacy** | The original CLI implementation at `../OmniSync` (v1.1.0). |
| **Live run** | A run that performs real S3 and WooCommerce writes. It requires the lease and a confirmed plan. |
| **Mapping key** | Normalised slug identifying a catalog entry: lowercase, `[a-z0-9-]`, version stripped. |
| **Needs-input item** | A decision the system cannot make safely on its own: unmapped archive, missing secondary name, downgrade, or ambiguous download entry. (Pending deletes are handled in Cleanup, not in this queue.) |
| **Object key** | Name of an S3 object. Published installables use `<slug>-<version>.zip` at the bucket root. |
| **Operator** | Role that can upload, plan, run, and resolve needs-input items. It cannot change settings, secrets or users, or delete S3 objects. |
| **Orphan** | An S3 object outside the control prefix that no download entry in any configured store (enabled or disabled) references, according to references collected live from WooCommerce. |
| **Override (filename override)** | Shared rule mapping an exact archive filename (case-insensitive) to a mapping key. Successor of legacy `data/product-slug-overrides.json`. |
| **Pending delete** | A superseded object key that was not deleted during a run: because of permissions, a remaining reference, a catalog older than 24 h, or a failed delete. An admin deletes or keeps it through Cleanup. |
| **Plan** | The ordered list of operations a run will perform per archive and store, produced by a dry evaluation. |
| **Primary download** | The download entry whose button is named `Download` and whose version also sets the product-level `latest_version` meta. |
| **Run** | A unit of background work with persisted progress. Kinds: `pipeline`, `catalog_refresh`, `cleanup_preview`, `cleanup_execute`, `legacy_import`. |
| **Run item** | One archive's processing within a pipeline run. |
| **Secondary download** | Any non-primary download entry on a product. Its button name comes from the *download names* rules. |
| **Shared config** | The versioned JSON document at `<control prefix>config/shared.json` holding store definitions (without secrets), aliases, overrides, download names and policies. |
| **Shortcode** | The download file value `[amazon_s3 object="<object key>"]` understood by the WooCommerce Amazon S3 Storage extension. |
| **Slug** | The WordPress installable folder name (e.g. `flatsome`). |
| **Store** | A WooCommerce site OmniSync keeps in sync (preconfigured: GPLFamily, PluginWay). |
| **Viewer** | Read-only role. |
| **Watched folder** | Local directory (`<data dir>/inbox`) polled for new archives as an alternative to browser upload. |
