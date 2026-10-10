# ADR-0011: Licence remains GPL-3.0

> **Status:** Accepted · **Owner:** TecHaust Technologies (@techaust) · **Last reviewed:** 2026-10-11 · **Audience:** engineers, the owner

**Date:** 2026-10-11 · **Deciders:** Owner, engineering

## Context

Legacy is licensed GPL-3.0-only (`legacy:pyproject.toml:11`, `legacy:LICENSE`). OmniSync 2.0 ports substantial legacy code (ADR-0002).

## Decision

OmniSync 2.0 is licensed **GPL-3.0-only**. Every dependency must be compatible with GPL-3.0 (for example MIT, BSD, Apache-2.0, LGPL, MPL-2.0, GPL-3.0). Licence compatibility is checked for each new dependency ([CONTRIBUTING](../../CONTRIBUTING.md#dependencies)).

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
|---|---|---|---|
| Relicense (e.g. proprietary or MIT) | Flexibility | Requires that all copyright holders of the ported code agree | Not needed; GPL-3.0 fits a business-internal tool |
| Clean-room rewrite under another licence | Licence freedom | Loses the tested logic | Contradicts ADR-0002 |

## Consequences

- Distributing OmniSync to third parties obliges providing the source under GPL-3.0. Internal use within the business imposes no distribution obligation.
- Apache-2.0 dependencies are compatible with GPL-3.0 (but not GPL-2.0-only), so the AI SDKs, boto3 and others are acceptable.
