# ADR-0005: React and TypeScript single-page application

> **Status:** Accepted · **Owner:** TecHaust Technologies (@techaust) · **Last reviewed:** 2026-10-11 · **Audience:** engineers

**Date:** 2026-10-11 · **Deciders:** Engineering

## Context

The UI is an interactive operations console: live-updating tables, drag-and-drop uploads, dialogs, a command palette and a streaming chat panel. It must meet WCAG 2.2 AA (NFR-A11Y-01) and stay maintainable.

## Decision

Build an SPA with React and TypeScript (`strict`), bundled by Vite. Use TanStack Query for server state, React Router for routing, Tailwind CSS with design tokens for styling, and Radix UI primitives for accessible components. The API client is generated from OpenAPI. The production build is served as static files by the backend.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
|---|---|---|---|
| Server-rendered templates with htmx | Less JavaScript, simple | Chat streaming, complex dialogs and a command palette are harder; weaker component ecosystem for a11y | Interaction-heavy UI |
| Vue or Svelte | Smaller bundles, pleasant APIs | Smaller pool of headless accessible primitives | React + Radix offers the best-tested accessible building blocks |
| Next.js | Conventions, SSR | SSR adds a Node server; unnecessary for a localhost console | Extra runtime with no benefit |

## Consequences

- Positive: rich, accessible interactions and type-safe API calls.
- Negative: a Node toolchain is needed to build from source (release builds ship the compiled assets).
- A bundle budget is enforced (NFR-PERF-02).
