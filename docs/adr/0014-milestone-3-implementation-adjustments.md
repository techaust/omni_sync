# ADR-0014: Implementation adjustments from Milestone 3

> **Status:** Accepted · amends [ADR-0002](0002-python-fastapi-backend.md) and [ADR-0013](0013-design-system-shadcn-indigo.md) · **Owner:** TecHaust Technologies (@techaust) · **Last reviewed:** 2026-10-11 · **Audience:** engineers

**Date:** 2026-10-11 · **Deciders:** Engineering

## Context

Building the Milestone 3 foundation against the current releases of the chosen tools, and on the owner's PC, surfaced facts the design documents did not anticipate:

1. **Windows Smart App Control on the owner's PC** blocks unsigned native binaries that have no reputation yet. It blocked `mypy.exe` and SQLAlchemy 2.1's compiled Cython modules (`_row_cy`: "An Application Control policy has blocked this file"). Other native modules (pydantic-core, argon2, Node tooling, Chromium) load normally. The Playwright Firefox build does not launch.
2. **Starlette 1.7** deprecates `httpx` in its test client in favour of **`httpx2`**, the Pydantic-maintained continuation of httpx (BSD-3-Clause, release 2.13.1 on 2026-09-23, no known vulnerabilities).
3. **FastAPI 0.143** no longer flattens included routers into `app.routes`, so route discovery for the permission-matrix test needs an explicit registry.
4. The current **Vite template** uses Vite 8, TypeScript 6 and **oxlint** (ESLint-compatible rules, including `jsx-a11y` and `react/no-danger`) rather than ESLint. `openapi-typescript` 7.13 declares a peer dependency on TypeScript 5.
5. The current **shadcn CLI** (4.x) replaces the old "new-york" style with named presets. Its generated components and libraries need adjusting for our CSP and WCAG rules:
   - The default focus ring at 50% opacity measures 2.31:1 (light) and 2.42:1 (dark), below the 3:1 minimum.
   - The primary hover at 80% measures 4.19:1, below the 4.5:1 minimum.
   - Sonner injects an inline `<style>` element without a nonce.
   - Radix Select renders its own `<style>` element and needs a `nonce` prop.
   - Vite inlines small fonts as `data:` URIs, which `font-src 'self'` blocks.

All of these were found by running the tests (unit, contrast and end-to-end with CSP-violation detection), not by inspection.

## Decision

| Area | Decision |
|---|---|
| SQLAlchemy | Built from source without the optional Cython speed-ups (`no-binary-package` plus `DISABLE_SQLALCHEMY_CEXT=1` in `backend/pyproject.toml`). The pure-Python modules are functionally identical, and the speed difference is negligible for this workload. |
| Running Python tools | Use `python -m <tool>` (for example `python -m mypy`, `python -m omnisync`) wherever a generated `.exe` launcher might be blocked. `python -m omnisync` is supported for running the server. |
| HTTP client | `httpx2` replaces `httpx` (ADR-0002). Compatibility of `respx`-style mocking with httpx2 is verified at the start of Milestone 4b; if it is not compatible, the WooCommerce fake uses an `httpx2.MockTransport`. |
| Route registry | `omnisync.api.app.API_ROUTERS` lists every router. The permission-matrix test walks it and cross-checks it against the generated OpenAPI document, so no endpoint can escape the test. |
| Front-end lint | oxlint (correctness and suspicious categories, plus `react`, `jsx-a11y`, `import`, `typescript`, `vitest` plugins) replaces ESLint. `react/no-danger` enforces C-21. |
| API types | `openapi-typescript` runs in an isolated `npx` environment with TypeScript 5 (`npm run api:generate`); CI fails if the committed types differ from the backend schema (`npm run api:check`). |
| shadcn preset | **Nova** (Radix primitives, Lucide icons), which replaces "new-york" in ADR-0013. Its fonts and colours are overridden by our tokens. |
| Accessibility fixes to generated components | All focus rings use the full `--ring` colour (global `:focus-visible` outline, 2 px, offset 2 px). Primary hover uses 90% opacity (5.16:1). Dark destructive hover uses 25% (above 4.5:1 with margin). |
| CSP fixes | Sonner's stylesheet is imported as a file and its runtime injector is disabled by a build plugin that fails the build if Sonner's code changes. Radix Select receives the per-request nonce. `get-nonce` is set at start-up for scroll-lock. Vite never inlines assets (`assetsInlineLimit: 0`). |
| End-to-end browsers | CI (Linux) runs Chromium, Firefox and WebKit. Local runs on the owner's PC default to Chromium. |

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
|---|---|---|---|
| Ask the owner to turn off Smart App Control | Native wheels work | Weakens the PC's security; Windows cannot turn it back on without a reinstall | Not justified for an optional speed-up |
| Add `'unsafe-inline'` to `style-src` | No workarounds needed | Weakens C-22 for the whole app | Each case could be fixed precisely |
| Replace Sonner with a custom Radix Toast component | No build plugin | More code to own and test | Revisit if the plugin becomes a maintenance burden |
| Keep `httpx` and silence the deprecation | No dependency change | Relies on a deprecated path in Starlette | `httpx2` is the maintained successor |

## Consequences

- Positive: every adjustment is enforced by a test or a build-time check rather than by convention.
- Negative: a build plugin patches one third-party module (Sonner). Its failure mode is a loud build error, not a silent CSP weakening.
- Risk R9 (local application control) is added to the [risk register](../11-ROADMAP.md#23-risk-register).
