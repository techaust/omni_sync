# Documentation Audit

> **Status:** Accepted · **Owner:** TecHaust Technologies (@techaust) · **Last reviewed:** 2026-10-11 · **Audience:** owner, reviewers

This document records the evidence that the documentation meets the [documentation standard](DOCUMENTATION-STANDARD.md). It is repeated at the end of every milestone and whenever a document's status changes.

## 0a. Audit of 2026-10-11 (end of Milestone 4a)

| Item | Result |
|---|---|
| Scope | 40 Markdown files (adds [ADR-0016](adr/0016-milestone-4a-domain-port.md)) |
| Checker and markdownlint | 0 errors, 0 warnings; 0 issues |
| Docs-versus-code check | FR-MATCH-05 amended for the ratio-limit scope; [11-ROADMAP §2.5](11-ROADMAP.md#25-implementation-status-by-requirement) updated for the domain requirements; CHANGELOG and README status updated. No API or schema changes in this milestone. |
| Legacy parity evidence (risk R1, R7) | Differential inspection run locally against the legacy checkout and its venv: 91 archives compared, 0 unexpected differences, 4 allow-listed (ADR-0016). The legacy tree was verified unchanged before and after the run. CI cannot run this test because the legacy repository is not available there. |

## 0. Audit of 2026-10-11 (end of Milestone 3)

| Item | Result |
|---|---|
| Scope | 39 Markdown files (adds THIRD-PARTY-NOTICES, the backend and front-end READMEs, ADR-0014 and ADR-0015), about 41,100 words |
| ``python scripts/check_docs.py`` | 0 errors, 0 warnings (39 files, 324 anchors, 110 legacy citations verified) |
| markdownlint | 0 issues |
| Docs CI job | Green on every push |
| Docs-versus-code check | The documents were compared with the implementation, and drift was corrected. 05-API-SPEC was missing 4 error codes, the ``permissions`` field of ``/auth/me`` and the sign-in behaviour of ``POST /setup``; the CHANGELOG test count was outdated. Implementation-status sections were added to [05-API-SPEC](05-API-SPEC.md#implementation-status), [04-DATA-MODEL](04-DATA-MODEL.md#implementation-status) and [11-ROADMAP §2.5](11-ROADMAP.md#25-implementation-status-by-requirement), so built and designed-only behaviour are distinguishable. |
| Decisions made during implementation | Recorded as ADRs: [ADR-0014](adr/0014-milestone-3-implementation-adjustments.md), [ADR-0015](adr/0015-csp-style-attributes.md) |

The Milestone 2 audit below remains the record for the design documents.

## 1. Audit of 2026-10-11 (end of Milestone 2 drafting)

### 1.1 Scope

34 Markdown files: 4 root files, 15 documents in `docs/` (including this audit), the ADR index and template, and 13 ADRs. In total, about 35,500 words.

| Content | Count |
|---|---|
| Functional requirements (`FR-*`) | 71 |
| Non-functional requirements (`NFR-*`) | 24 |
| Requirements traced in the matrix | 95 of 95 |
| Threats / controls | 20 / 28 (every threat has at least one control) |
| ADRs | 13 |
| Legacy source citations, each verified to exist with valid line ranges | 110 |
| Mermaid diagrams, all rendered successfully, each with a text description | 7 |

### 1.2 Automated checks

| Check | Command | Result |
|---|---|---|
| Links, anchors, orphans, metadata, IDs, traceability, threat→control coverage, legacy citations, diagram descriptions, heading levels, ADR sections | `python scripts/check_docs.py` | 0 errors, 0 warnings |
| The checker itself detects each defect class it claims to (one test per check, plus a clean-tree test) | `python -m unittest scripts/test_check_docs.py` | 16 tests, all pass |
| Markdown style and structure (including MD001 heading increments and MD045 image alt text) | `npx --yes markdownlint-cli2 "**/*.md"` (v0.23.3, markdownlint v0.41.1) | 0 issues in 34 files |
| Mermaid syntax | each diagram rendered with `@mermaid-js/mermaid-cli` (`mmdc`) | 7 of 7 rendered |

### 1.3 Independent semantic review

The automated checks cannot detect contradictions or technical errors, so an independent reviewer (a separate agent session with no authoring context) read all files in full.

| Round | Findings | Outcome |
|---|---|---|
| 1 | 40: 7 high, 20 medium, 13 low. Examples: the lease safety gap depended on unsynchronised local clocks; a long upload could outlive the lease; a disabled store's references were ignored, so objects it still served could be deleted; a `pending_delete` resolution path bypassed cleanup safeguards; the colleague's role was described inconsistently; a dry run with any needs-input item blocked every live run. | All 40 addressed |
| 2 (verification) | 35 of 39 confirmed resolved (the 40th was this audit file); 3 not fully resolved and 11 new issues found. The most important: the lease argument assumed a 60 s per-call bound that HTTP client timeouts do not provide; the 24 h catalog-age rule had no automatic refresh behind it; a viewer-level AI tool could create suggestions. | All 14 addressed (total per-call deadline with 8 MiB parts and a minimum bandwidth; new requirement FR-CAT-04 for daily refresh; tool permission corrected; plus the 11 smaller items) |

The round-2 fixes were verified by the automated checks and by the author's targeted search for every affected term. A third independent review was performed at the start of Milestone 3 (section 1.4).

### 1.4 Third review round (start of Milestone 3)

The same reviewer confirmed all 14 round-2 items as resolved. It independently recomputed every contrast ratio in [08-UX-DESIGN §5.1](08-UX-DESIGN.md#51-colour-tokens) (all correct) and found 10 new issues: 2 medium and 8 low.

The two medium issues were:

- **Pre-paint theming:** no mechanism compatible with the CSP had been specified. It is now resolved by rendering the theme class on the server plus CSS media queries.
- **Status labels:** design documents were still marked *Proposed* after owner approval.

Among the low issues was a real contrast failure: stock `muted-foreground` on the `muted` surface measured 4.40:1. The colour table now covers every foreground/surface pair the components use. All 10 issues were addressed.

### 1.5 Rubric result

| # | Dimension | Result | Evidence and notes |
|---|---|---|---|
| Q1 | Accuracy | **Pass** | 110 legacy citations verified mechanically. Legacy figures (194 tests, 74.05% coverage, 2,387 keys, 110 slug records) come from execution and data inspection on 2026-10-11. The S3 conditional-write capability was confirmed in the AWS S3 User Guide. |
| Q2 | Honesty | **Pass** | Root README and the operations guide carry "not implemented" banners. Design documents are marked *Accepted (approved design, not yet implemented)*. Response-time targets in SECURITY.md are marked pending (Q4). |
| Q3 | Completeness | **Pass** | 95 of 95 requirements traced; 20 of 20 threats mitigated; 13 of 13 ADRs complete; parity checklist covers every legacy command and OpenCode artifact. |
| Q4 | Consistency | **Pass** | Zero undefined or duplicate IDs. Two review rounds targeted cross-document contradictions. Glossary updated for every changed concept (lease, catalog, orphan, pending delete, needs-input item). |
| Q5 | Navigability | **Pass** | Zero broken links or anchors; zero orphans; index with reading order and per-role paths. |
| Q6 | Clarity | **Pass** | Audience and purpose stated per document; markdownlint clean. |
| Q7 | Actionability | **Pass for the design phase** | Acceptance criteria are Given/When/Then or numeric thresholds. Operational procedures have exact commands but **cannot be executed until the software exists**. They are scheduled for validation on a clean Windows 11 machine in Milestone 7. |
| Q8 | Accessibility | **Pass** | Heading structure and alt text are linted; all 7 diagrams have text descriptions; all tables have header rows. |
| Q9 | Maintainability | **Pass** | Every document has an owner and a review date. The Definition of Done requires doc updates. The checker and its tests are standard-library Python, ready for CI in Milestone 3. |

## 2. Known limitations of this audit

- The rubric results are the author's assessment, supported by the evidence above. The owner approved the design on 2026-10-11, completing Milestone 2 ([11-ROADMAP §1](11-ROADMAP.md#1-milestones)).
- Four owner questions remain open (Q1–Q4, [11-ROADMAP §2.2](11-ROADMAP.md#22-open-questions)). None blocks Milestone 3. Q5 was resolved on 2026-10-11.
- Design-level documents will be re-audited against the implementation in every later milestone. A document becomes *Implemented* only when its content matches working, tested code.

## 3. Recommended next audit actions

1. Add `check_docs.py`, its tests and markdownlint to CI in Milestone 3.
