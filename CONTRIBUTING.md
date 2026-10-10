# Contributing to OmniSync

Thank you for contributing. This guide covers the workflow, the quality gates and the Definition of Done. The project is in the design phase, so the code-related gates apply from Milestone 3 ([roadmap](docs/11-ROADMAP.md)). The documentation gates apply now.

## Workflow

1. **Open or pick an issue** that references the requirement IDs it addresses (e.g. `FR-PIPE-05`).
2. **Branch** from `main` as `feat/<short-name>`, `fix/<short-name>`, `docs/<short-name>` or `chore/<short-name>`.
3. **Commit** in small, atomic commits using [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/), e.g. `feat(pipeline): hold downgrades for approval (FR-PIPE-06)`.
4. **Open a pull request** with a summary, the linked requirements, test evidence (commands and results), screenshots for UI changes, and the docs checklist from the [documentation standard](docs/DOCUMENTATION-STANDARD.md#3-review-checklist-copy-into-prs-that-touch-docs).
5. **Review:** at least one approving review. Changes to security controls, the lease protocol or AI tool permissions need the owner's approval as well.
6. **Merge** with squash after CI is green.

## Quality gates

| Gate | Command | From |
|---|---|---|
| Documentation | `python scripts/check_docs.py` and `npx --yes markdownlint-cli2 "**/*.md"` | now |
| Backend lint, format, types | `uv run ruff check`, `uv run ruff format --check`, `uv run mypy` | Milestone 3 |
| Backend tests and coverage | `uv run pytest` | Milestone 3 |
| Front-end lint, types, tests | `npm run lint`, `npm run typecheck`, `npm test` | Milestone 3 |
| End-to-end and accessibility | `npm run e2e` | Milestone 4c |
| Dependencies and secrets | `uv run pip-audit`, `npm audit --audit-level=high`, `gitleaks detect` | Milestone 3 |

Thresholds are defined in [09-TESTING-STRATEGY §10](docs/09-TESTING-STRATEGY.md#10-coverage-and-quality-gates-ci).

## Definition of Done

A change is done when:

- [ ] it implements the referenced requirements and their acceptance criteria;
- [ ] tests cover the change at the levels given in the [traceability matrix](docs/11-ROADMAP.md#3-traceability-matrix), and all gates pass;
- [ ] no claim of working behaviour is made without evidence (test name or CI link);
- [ ] affected documents are updated in the same PR (requirements, architecture, data model, API, operations, changelog), with their *Last reviewed* date and status;
- [ ] any significant decision is captured as an ADR;
- [ ] security-relevant changes are reflected in [06-SECURITY](docs/06-SECURITY.md);
- [ ] user-visible changes are listed under *Unreleased* in [CHANGELOG.md](CHANGELOG.md).

## Dependencies

Before adding a dependency, record in the PR:

| Check | Requirement |
|---|---|
| Necessity | What it replaces, and why the standard library or an existing dependency is not enough |
| Licence | Compatible with GPL-3.0 ([ADR-0011](docs/adr/0011-license-gpl-3.md)) |
| Maintenance | A release within the last 12 months; active maintainers |
| Security | No open high or critical advisories; reasonable install footprint |

All dependencies are pinned through `uv.lock` and `package-lock.json`.

## Coding standards (from Milestone 3)

- Python: ruff (line length 100), mypy `--strict`, no bare `except`, typed domain errors with stable codes, and no I/O in `core/`.
- TypeScript: `strict`, no `any` without a justifying comment, no `dangerouslySetInnerHTML`, and accessible components only (Radix-based).
- Names describe intent; no abbreviations beyond the [glossary](docs/GLOSSARY.md).

## Safety rules for contributors

- Never commit secrets, `.env` files, databases or customer archives.
- Never run tests that write to live S3 or stores without the owner's approval ([09-TESTING-STRATEGY §12](docs/09-TESTING-STRATEGY.md#12-live-testing-policy)).
- Never modify the legacy repository (`../OmniSync`). It is read-only reference material.
