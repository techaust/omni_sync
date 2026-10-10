# Documentation Index

> **Status:** Accepted · **Owner:** TecHaust Technologies (@techaust) · **Last reviewed:** 2026-10-11 · **Audience:** everyone

OmniSync 2.0's design was approved on 2026-10-11 and implementation is in progress (see the [roadmap](11-ROADMAP.md)). The documents below specify the system being built. A document says *Implemented* only once the code it describes exists and matches it.

## Read in this order

| # | Document | Purpose | Primary audience | Status |
|---|---|---|---|---|
| 01 | [Discovery report](01-DISCOVERY-REPORT.md) | How the legacy tool works, its strengths, defects and risks | Owner, engineers | Accepted |
| 02 | [Product requirements](02-PRODUCT-REQUIREMENTS.md) | What 2.0 must do; acceptance criteria | Owner, engineers, testers | Accepted |
| 03 | [Architecture](03-ARCHITECTURE.md) | Structure, pipeline, coordination, technology | Engineers | Accepted |
| 04 | [Data model](04-DATA-MODEL.md) | Database schema, shared S3 documents, file layout, legacy import | Backend engineers | Accepted |
| 05 | [API specification](05-API-SPEC.md) | HTTP and SSE contract | Engineers | Accepted |
| 06 | [Security](06-SECURITY.md) | Threat model, controls, IAM policies | Engineers, owner | Accepted |
| 07 | [AI design](07-AI-DESIGN.md) | AI capabilities, tools, safety, budgets, evaluation | Engineers, owner | Accepted |
| 08 | [UX design](08-UX-DESIGN.md) | Screens, interaction patterns, accessibility | Front-end engineers | Accepted |
| 09 | [Testing strategy](09-TESTING-STRATEGY.md) | How every requirement is verified | Engineers | Accepted |
| 10 | [Operations guide](10-OPERATIONS.md) | Install, configure, operate, back up, upgrade, troubleshoot | Owner, colleague | Accepted |
| 11 | [Roadmap and traceability](11-ROADMAP.md) | Milestones, decisions, risks, requirement traceability, parity checklist | Everyone | Accepted (living) |

## Reference

| Document | Purpose |
|---|---|
| [Architecture decision records](adr/README.md) | Why each significant choice was made |
| [Glossary](GLOSSARY.md) | Canonical terms |
| [Documentation standard](DOCUMENTATION-STANDARD.md) | Measurable quality criteria and conventions |
| [Documentation audit](DOCUMENTATION-AUDIT.md) | Latest evidence that the standard is met |

## By role

- **Owner:** 01 → 02 (journeys and goals) → 06 §5 (credentials for the colleague) → 07 §8 (AI privacy) → 10 → 11 (open questions).
- **Engineer joining the project:** README → 02 → 03 → ADRs → 04 → 05 → 09 → [CONTRIBUTING](../CONTRIBUTING.md).
- **Colleague (operator):** 10 §5 (day-to-day) and §6 (two-instance setup).
