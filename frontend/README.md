# OmniSync front end

React 19 + TypeScript single-page app served by the OmniSync server. Design: [08-UX-DESIGN](../docs/08-UX-DESIGN.md), [ADR-0005](../docs/adr/0005-react-typescript-spa.md), [ADR-0013](../docs/adr/0013-design-system-shadcn-indigo.md), [ADR-0014](../docs/adr/0014-milestone-3-implementation-adjustments.md).

## Commands

| Command | What it does |
|---|---|
| `npm ci` | Install exactly the locked dependencies |
| `npm run dev` | Vite dev server on <http://localhost:5173>, proxying `/api` to `omnisync serve` on port 8765 |
| `npm run build` | Type-check and build to `dist/` (served by the backend) |
| `npm run lint` / `npm run format:check` / `npm run typecheck` | oxlint, Prettier, `tsc --strict` |
| `npm test` / `npm run test:coverage` | Vitest unit and component tests (coverage gate: 70% of lines) |
| `npm run api:generate` | Regenerate `src/api/schema.d.ts` from the backend OpenAPI schema |
| `npm run api:check` | Fail if the committed API types differ from the backend (used in CI) |
| `npm run check:bundle` | Fail if initial JavaScript exceeds 300 KB gzip (NFR-PERF-02) |
| `npm run e2e` | Playwright end-to-end tests against the real server and production build (run `npm run build` first) |

## Layout

```text
src/
  api/          typed client (client.ts) and generated types (schema.d.ts - do not edit)
  auth/         session provider: current user, permissions, CSRF token
  components/   app shell, shared components; ui/ holds shadcn/ui components
  lib/          theme (colour mode, density) and CSP nonce helpers
  pages/        one module per route
  styles/       tokens.css (measured colour tokens) and shadcn.css (ejected utilities)
e2e/            Playwright tests
```

## Rules

- Colours come only from `src/styles/tokens.css`. `src/styles/tokens.test.ts` recomputes every WCAG contrast ratio and fails if one drops below AA.
- No inline scripts or styles. Components that render `<style>` elements must receive the CSP nonce (`src/lib/csp.ts`). The end-to-end tests fail on any CSP violation.
- Never use `dangerouslySetInnerHTML` (enforced by oxlint).
