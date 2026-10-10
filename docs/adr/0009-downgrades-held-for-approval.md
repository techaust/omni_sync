# ADR-0009: Downgrades are held for approval

> **Status:** Accepted · **Owner:** TecHaust Technologies (@techaust) · **Last reviewed:** 2026-10-11 · **Audience:** engineers

**Date:** 2026-10-11 · **Deciders:** Owner

## Context

Legacy logs a warning when an incoming version is lower than the last processed one and then applies it to both stores (`legacy:omnisync/engine.py:525-530`, `legacy:omnisync/wc.py:256-259`). Only the fallback-matching path holds downgrades for review (`legacy:omnisync/engine.py:451-472`). An old ZIP dropped into the inbox by mistake therefore silently replaces the current download on every store (D-05).

## Decision

Any archive whose version is lower than either the live WooCommerce meta (`latest_version_<slug>`) or the recorded slug state, compared with PEP 440, becomes a `downgrade` needs-input item. Nothing is uploaded or written for it until a user approves with a reason or rejects it. When neither a live meta nor a slug state exists (a first publish), there is nothing to compare and the archive proceeds.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
|---|---|---|---|
| Warn and apply (legacy) | No interruption | Silent customer-facing regressions | Owner chose to hold |
| Reject automatically | Safe | Legitimate rollbacks (a broken vendor release) become impossible | Too rigid |

## Consequences

- Positive: no accidental downgrades; intentional rollbacks are audited with a reason.
- Negative: one extra decision for intentional rollbacks.
- Unparseable versions compare as `0` in legacy (`legacy:omnisync/extractor.py:161-167`). In 2.0, when an existing version is present but either it or the incoming version cannot be parsed, a `downgrade` item is created with the note "versions not comparable" rather than proceeding.
