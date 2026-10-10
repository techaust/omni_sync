# ADR-0008: Server-side AI gateway with typed tools and human approval

> **Status:** Accepted · **Owner:** TecHaust Technologies (@techaust) · **Last reviewed:** 2026-10-11 · **Audience:** engineers

**Date:** 2026-10-11 · **Deciders:** Owner, engineering

## Context

The owner wants AI to be a meaningful part of the product, with a configurable provider and Anthropic as the default. The biggest recurring manual task in legacy is resolving unmapped archives by hand-editing JSON. Legacy AI rules exist only as prose instructions to an agent (`legacy:AGENTS.md:100-108`), and nothing enforces them.

## Decision

- Every AI call goes through a backend **gateway** with provider adapters (Anthropic default, OpenAI), budgets and usage accounting. The browser never receives provider keys.
- The model acts only through **typed tools** in three risk classes (`read`, `propose`, `approve`), filtered by the user's role. `approve`-class tools run only after explicit user approval, through the same service functions and permission checks as the UI.
- Some operations are **never** exposed to AI: cleanup execution, settings, secrets, users, backups and imports.
- AI is **optional**. Every workflow works without it.

Details: [07-AI-DESIGN](../07-AI-DESIGN.md).

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
|---|---|---|---|
| Autonomous agent with broad tools | Maximum automation | Unacceptable risk for live stores; hard to audit | Violates "AI proposes, people decide" |
| Chat-only assistant without tools | Simple and safe | Little real value; cannot use project data | Too shallow |
| AI in the deterministic pipeline (e.g. AI version detection) | Might handle odd archives | Non-deterministic publishing; hard to test | Deterministic code is sufficient and verifiable |

## Consequences

- Positive: measurable value (resolver eval), contained risk, provider portability.
- Negative: more components to test (tool permission matrix, approval flow); provider price tables must be maintained by the admin.
