# ADR-0013: Design system based on shadcn/ui with an indigo accent

> **Status:** Accepted · **Owner:** TecHaust Technologies (@techaust) · **Last reviewed:** 2026-10-11 · **Audience:** front-end engineers, the owner

**Date:** 2026-10-11 · **Deciders:** Owner

## Context

[ADR-0005](0005-react-typescript-spa.md) chose React, Tailwind CSS and Radix UI primitives. [08-UX-DESIGN §5](../08-UX-DESIGN.md#5-visual-design-system) described token groups but did not name a concrete theme, accent colour or typeface. The UI must meet WCAG 2.2 AA (NFR-A11Y-01), support light and dark modes (FR-UX-04), and work under a strict CSP with no third-party requests (C-22).

## Decision

- **Versions:** Tailwind CSS v4 with the shadcn/ui CLI generation that targets it. Our colour tokens are explicit hex values, so they do not depend on shadcn's version-specific defaults (HSL in the v3 generation, OKLCH in v4).
- **Base:** shadcn/ui components in the compact "new-york" style. They are copied into `frontend/src/components/ui` (MIT licence), built on Radix primitives and styled with Tailwind. The project owns and maintains the copied code.
- **Theme:** zinc neutrals with an **indigo accent** (`#4F46E5` in light mode, `#818CF8` in dark mode). All colours are CSS-variable tokens, so re-theming is a token change only.
- **Mode:** follow the operating-system setting by default (`prefers-color-scheme`), with light and dark overrides stored per user.
- **Typography:** Inter for UI text and JetBrains Mono for code, file names, slugs, versions and object keys. Both are SIL Open Font License 1.1 and **self-hosted** (no Google Fonts or CDN), which satisfies the CSP and offline use.
- **Icons:** Lucide (ISC licence), the shadcn/ui default.
- **Contrast-driven overrides** of shadcn/ui defaults:
  - Form-control borders use zinc-500, because the stock borders fail WCAG 1.4.11 (3:1). Stock zinc-200 measures 1.27:1 and zinc-800 measures 1.34:1.
  - Light-mode secondary text uses zinc-600, because stock zinc-500 is 4.40:1 on the muted surface.
  - Dark-mode primary and destructive buttons use near-black text, because white on indigo-500 is 4.47:1 and white on red-400 is 2.77:1.

The full token table, with measured contrast ratios, is in [08-UX-DESIGN §5](../08-UX-DESIGN.md#5-visual-design-system).

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
|---|---|---|---|
| Radix Themes | Ready-made styled components on the same primitives | Opinionated styling, harder to customise deeply | Less control over density and status styling |
| Material UI | Large component set | Different styling system (Emotion), heavier bundle, CSP needs extra work | Conflicts with ADR-0005 |
| Fluent UI | Native Windows look | Weaker Tailwind integration; departs from the chosen stack | Stack mismatch |
| Blue or emerald accent | Familiar | Too close to the blue "running" and green "completed" status colours | Indigo keeps the accent distinct from statuses |

## Consequences

- Positive: accessible primitives, full ownership of component code, one token file for theming, no runtime dependency on a UI library's release cycle.
- Negative: copied components do not receive upstream fixes automatically. Updates are pulled deliberately and reviewed (tracked as a quarterly maintenance task).
- Licence compliance: the MIT notice for the copied shadcn/ui code and the OFL 1.1 texts for Inter and JetBrains Mono ship with the application in `THIRD-PARTY-NOTICES.md`, together with every other bundled third-party licence.
- Follow-up: the token values are verified automatically. A contrast unit test computes WCAG ratios for every foreground/background token pair in both modes and fails the build below threshold (Milestone 3), and axe checks rendered pages (Milestone 4c).
