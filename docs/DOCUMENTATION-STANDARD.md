# Documentation Standard

> **Status:** Accepted · **Owner:** TecHaust Technologies (@techaust) · **Last reviewed:** 2026-10-11 · **Audience:** everyone who writes or reviews OmniSync documentation

This document defines what "excellent documentation" means for OmniSync in **measurable** terms. It also explains how each criterion is verified. "10/10" means every criterion below passes and the evidence is recorded in [the latest audit](#current-audit). It is never a self-assigned score.

## 1. Quality rubric

| # | Dimension | Pass criteria | Verification |
|---|---|---|---|
| Q1 | **Accuracy** | Every statement about the legacy system cites its source as `legacy:<path>:<line>` and the cited lines exist. Every statement about the new system says whether it is *Proposed*, *Accepted*, *Implemented* or *Verified*. | `scripts/check_docs.py` resolves every `legacy:` citation (file exists, line numbers in range). Manual review checks status labels. |
| Q2 | **Honesty** | No document claims that a feature works, a test passes, or a property holds unless it links to evidence (test name, CI run, audit record). Until code exists, design documents describe intended behaviour only. | Review checklist item; root README carries a status banner. |
| Q3 | **Completeness** | Every requirement (`FR-*`, `NFR-*`) is traced to a design section, a milestone and a verification method. Every threat (`T-*`) has at least one mitigation. Every ADR has context, decision, alternatives and consequences. | `check_docs.py` traceability and threat-mitigation checks. |
| Q4 | **Consistency** | IDs are unique and every referenced ID is defined. Domain terms follow the [Glossary](GLOSSARY.md). Names of endpoints, tables, statuses and settings match across documents. | `check_docs.py` ID checks; review against the glossary. |
| Q5 | **Navigability** | Every document is reachable from the [index](README.md). No broken relative links or anchors. Each document starts with a metadata line and has a clear heading hierarchy. | `check_docs.py` link, anchor, orphan and metadata checks. |
| Q6 | **Clarity** | Each document states its audience and purpose. Sentences are plain and active. Jargon is defined in the glossary. Examples are concrete, such as real slugs and real commands. | markdownlint plus peer review. |
| Q7 | **Actionability** | Procedures (setup, operations, recovery) are numbered steps with exact commands and the expected result. Acceptance criteria are testable (Given/When/Then or a numeric threshold). | Review; procedures are dry-run on a clean machine before release (Milestone 7). |
| Q8 | **Accessibility** | No skipped heading levels. Images have alt text. Every diagram (Mermaid or ASCII) is followed by a text description. Tables have header rows. | markdownlint (MD001, MD045) plus `check_docs.py` diagram-description check. |
| Q9 | **Maintainability** | Each document has an owner and a last-reviewed date. Documentation updates are part of the Definition of Done ([CONTRIBUTING](../CONTRIBUTING.md#definition-of-done)). CI runs the checks on every change. | `check_docs.py` metadata check; CI job (Milestone 3). |

## 2. Conventions

### 2.1 Metadata line

Every file in `docs/` (except ADRs, which carry their own status block) starts with a level-1 heading followed by a quoted metadata line:

```markdown
> **Status:** Draft | Proposed | Accepted | Implemented · **Owner:** … · **Last reviewed:** YYYY-MM-DD · **Audience:** …
```

| Status | Meaning |
|---|---|
| Draft | Work in progress; may be incomplete. |
| Proposed | Complete and ready for review; not yet approved. |
| Accepted | Approved; implementation must follow it. |
| Implemented | The described system exists and the document matches it. |

### 2.2 Identifiers

| Prefix | Defined in | Example |
|---|---|---|
| `FR-<AREA>-NN` | [02-PRODUCT-REQUIREMENTS](02-PRODUCT-REQUIREMENTS.md) | `FR-PIPE-03` |
| `NFR-<AREA>-NN` | [02-PRODUCT-REQUIREMENTS](02-PRODUCT-REQUIREMENTS.md) | `NFR-PERF-01` |
| `T-NN` (threat), `C-NN` (control) | [06-SECURITY](06-SECURITY.md) | `T-04`, `C-12` |
| `D-NN` (legacy defect) | [01-DISCOVERY-REPORT](01-DISCOVERY-REPORT.md) | `D1` is written as `D-01` |
| `ADR-NNNN` | [adr/](adr/README.md) | `ADR-0007` |

An ID is *defined* when it appears as the first cell of a table row in its home document. Every other occurrence is a *reference*.

### 2.3 Legacy citations

Use `` `legacy:<path relative to the legacy repo>:<line>[-<line>]` ``, for example `legacy:omnisync/wc.py:180-182`. The checker resolves these against `../OmniSync`, or against the path in the `OMNISYNC_LEGACY_ROOT` environment variable. When the legacy repository is absent (for example in CI, or after it is deleted at project end, risk R7), citations are reported as unverified warnings rather than errors. Their last full verification is recorded in the [audit](DOCUMENTATION-AUDIT.md).

### 2.4 Diagrams

Prefer Mermaid, because it is versioned as text and renders on GitHub. Follow every diagram with a paragraph that starts with `*Text description:*` so the content is available without rendering and to screen readers.

### 2.5 Style

- Use present tense for designed behaviour ("The runner acquires the lease"). Use "will" only in roadmap items.
- Write in second person in procedures ("Run `omnisync check`").
- Use one term per concept. The glossary wins any disagreement.
- Put code, paths, commands, settings and identifiers in backticks.
- Use ISO dates (`2026-10-11`) and UTC times.

## 3. Review checklist (copy into PRs that touch docs)

- [ ] Metadata line updated (status, last reviewed)
- [ ] New or changed requirements traced in [11-ROADMAP](11-ROADMAP.md#3-traceability-matrix)
- [ ] New terms added to [GLOSSARY](GLOSSARY.md)
- [ ] Diagrams have a text description
- [ ] No claim of working behaviour without linked evidence
- [ ] `python scripts/check_docs.py` and `npx markdownlint-cli2 "**/*.md"` pass

## Current audit

The latest audit result is recorded in [DOCUMENTATION-AUDIT](DOCUMENTATION-AUDIT.md).
