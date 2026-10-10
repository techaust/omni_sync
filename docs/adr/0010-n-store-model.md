# ADR-0010: Configurable N-store model

> **Status:** Accepted · **Owner:** TecHaust Technologies (@techaust) · **Last reviewed:** 2026-10-11 · **Audience:** engineers

**Date:** 2026-10-11 · **Deciders:** Owner

## Context

Legacy hard-codes two stores (`legacy:omnisync/config.py:28-30`). Its tier-2 fallback accepts a filename-derived key only if it exists on both stores, a "dual-store proof" (`legacy:omnisync/engine.py:190-195`).

## Decision

Stores are data, defined in the shared config (key, label, base URL, enabled, order). The pipeline processes every **enabled** store. The tier-2 proof becomes "the key exists in the catalog of **every enabled store**". An archive is complete when every enabled store has completed. GPLFamily and PluginWay are preconfigured during setup or legacy import.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
|---|---|---|---|
| Keep exactly two stores | Matches today | A code change for any new store | Owner chose N stores |
| Per-store product subsets (a store sells only some products) | Flexible catalogs | The tier-2 proof and the completion rule become ambiguous | Not a current need; can be added later with an explicit per-store "optional" flag |

## Consequences

- Positive: adding a store is a settings change plus credentials.
- Negative: the tier-2 proof gets stricter as stores are added. Products missing from one store fall back to explicit aliases or overrides, which is the safer behaviour.
- Disabling a store excludes it from runs, but **not** from reference checks. A disabled store may still serve customers from S3, so its references keep objects alive in both finalisation and cleanup (FR-PIPE-07, FR-CLN-01). A store stops protecting objects only when it is removed from the configuration.
