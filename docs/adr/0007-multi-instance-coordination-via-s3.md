# ADR-0007: Multi-instance coordination through S3 conditional writes

> **Status:** Accepted · **Owner:** TecHaust Technologies (@techaust) · **Last reviewed:** 2026-10-11 · **Audience:** engineers

**Date:** 2026-10-11 · **Deciders:** Owner, engineering

## Context

The owner and a colleague each run their own instance on their own PC (decision of 2026-10-11, after a single shared instance was offered as the recommended option). Both instances write to the same S3 bucket and the same WooCommerce products. Legacy guarded only against concurrent runs on one machine (`app.lock`, `legacy:omnisync/cli.py:58-75`), and even there cleanup was unguarded (D-01). The instances must also resolve archives identically, so aliases, overrides and download names must be shared.

S3 supports conditional writes: `If-None-Match: *` prevents creating an object that already exists, and `If-Match: <ETag>` makes a write succeed only if the object is unchanged (AWS S3 User Guide, "How to prevent object overwrites with conditional writes").

## Decision

1. Use a **lease object** at `<control prefix>control/lease.json`, acquired, renewed and released only through conditional writes. Timing is measured in S3 server time: TTL 300 s, renewal every 30 s, a write deadline 120 s before expiry checked before every mutating call, and a 30 s takeover grace. All mutating operations require the lease. The protocol is in [03-ARCHITECTURE §6.2](../03-ARCHITECTURE.md#62-lease-protocol).
2. Use a **shared config document** at `<control prefix>config/shared.json`, updated with optimistic concurrency (`If-Match`).
3. Store **shared audit events and pending deletes** as write-once objects (`If-None-Match: *`).
4. Treat **live state as truth.** Deduplication and downgrade checks consult S3 metadata and live WooCommerce meta rather than only local state, which can diverge between instances.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
|---|---|---|---|
| One shared instance reached over LAN or VPN | Simplest correctness; one database | Needs network setup and remote-access security | Owner preferred independent instances |
| DynamoDB table for locks and config | Purpose-built conditional writes, TTL | Additional AWS service, IAM and cost | S3 already offers the needed primitive in the existing bucket |
| Shared network drive with lock files | No cloud dependency | Unreliable locking semantics on SMB; requires both PCs on the same network | Fragile |
| No coordination (operational discipline) | No work | Silent corruption when both run at once | Unacceptable |

## Consequences

- Positive: no new infrastructure; both instances see the same rules; mutual exclusion is testable (NFR-REL-03).
- Negative: writes to products are not fenced, so one stale write after a long freeze is a residual risk (T-11, accepted, [06-SECURITY §9](../06-SECURITY.md#9-residual-risks-accepted)).
- Lease safety depends on S3 time estimates, not PC clocks. A poor network that makes the estimate uncertain blocks lease operations until it recovers ([03-ARCHITECTURE §6.4](../03-ARCHITECTURE.md#64-clock-skew)).
- After a holder crashes, the other instance may wait up to about 6 minutes before taking over.
- Legacy `cleanup-s3` must never run again after cutover, because it would treat control objects as orphans ([10-OPERATIONS §9](../10-OPERATIONS.md#9-cutover-from-legacy)).
