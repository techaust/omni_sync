# ADR-0016: Domain-port decisions from Milestone 4a

> **Status:** Accepted · amends [02-PRODUCT-REQUIREMENTS](../02-PRODUCT-REQUIREMENTS.md) FR-MATCH-05 · **Owner:** TecHaust Technologies (@techaust) · **Last reviewed:** 2026-10-11 · **Audience:** engineers

**Date:** 2026-10-11 · **Deciders:** Engineering

## Context

Milestone 4a ported the legacy archive inspection, version and slug detection, matching and catalog logic into `backend/src/omnisync/core/`. The port is checked by the ported legacy tests and by a differential test that runs the legacy code (`legacy:omnisync/extractor.py`, `legacy:omnisync/engine.py:257-310`) and the new code on the same 91-archive corpus. The port and the differential test surfaced points where exact legacy parity is either unsafe or wrong:

1. FR-MATCH-05 caps the compression ratio at 200:1 per entry. Applied to every entry, this rejects small, highly repetitive text files (licence files, minified JSON) that real plugins contain.
2. Legacy inspected ZIP archives nested 7 levels deep without limit on what it read there, and had no entry-count or ratio limits.
3. Legacy labelled a fallback match `exact-file` whenever the file name had an override entry, even when that override's key was on no store and the generic file-name tier actually matched (`legacy:omnisync/engine.py:427-431`).
4. The newest `hypothesis` release (6.169) ships an unsigned compiled module, which Windows Smart App Control on the owner's PC blocks (risk R9).

## Decision

1. The 200:1 ratio limit applies to entries of at least 1 MiB uncompressed (`ArchiveLimits.ratio_min_size`). Smaller entries cannot form a meaningful bomb within the other limits (768 MiB total, 10,000 entries).
2. Nesting deeper than 6 levels, more than 10,000 entries, and ratio violations fail inspection with `archive.limits_exceeded`, on the outer archive and on every nested archive.
3. `match_method` records the tier that actually matched (FR-MATCH-03). The legacy label is not reproduced.
4. Nested payloads are spooled to temporary files and exposed as paths, not bytes (D-03).
5. `hypothesis` is pinned below 6.156, the last pure-Python release, until its compiled module is signed or Smart App Control is reconsidered.

Every other behaviour matches legacy. The differential test allow-lists exactly these differences, keyed by fixture, and fails if an allow-listed fixture stops differing.

## Alternatives

- **Apply the ratio limit to every entry.** Rejected: it fails real plugins that contain small repetitive files, and adds no protection beyond the total-size and entry limits.
- **Reproduce legacy depth handling and labels exactly.** Rejected: unbounded nesting is a resource risk (FR-MATCH-05), and a wrong `match_method` misleads the audit trail.
- **Use the newest `hypothesis` and ask the owner to turn off Smart App Control.** Rejected for the same reasons as in [ADR-0014](0014-milestone-3-implementation-adjustments.md).

## Consequences

- An archive that legacy accepted can now be rejected only if it exceeds a safety limit. Such an archive is reported with a clear code instead of being published.
- Audit records of how an archive matched are accurate, so they can differ from legacy reports for the same file.
- The `hypothesis` pin must be revisited when a signed release appears (tracked by R9).
