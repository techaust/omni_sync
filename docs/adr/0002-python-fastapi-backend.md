# ADR-0002: Python and FastAPI backend reusing the legacy domain logic

> **Status:** Accepted · **Owner:** TecHaust Technologies (@techaust) · **Last reviewed:** 2026-10-11 · **Audience:** engineers

**Date:** 2026-10-11 · **Deciders:** Engineering

## Context

The most valuable legacy code is the extractor, matching, mapping and WooCommerce logic, about 1,500 lines of strictly typed Python covered by 194 passing tests (re-run on 2026-10-11, 74% branch coverage). These modules encode many hard-won edge cases: Envato wrappers, nested bundles, bare-integer versions, fossil mapping keys (`legacy:omnisync/mappings.py:80-100`).

## Decision

Implement the backend in Python 3.12 with FastAPI, Pydantic v2 and httpx. Port the legacy domain code into a pure `core/` package and port the legacy tests as executable specifications.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
|---|---|---|---|
| TypeScript/Node backend (one language end-to-end) | Shared types with the front end | Full rewrite of tested logic; regression risk | Risk to correctness outweighs language uniformity |
| Django + DRF | Batteries included (admin, ORM) | Heavier; async/SSE less natural; admin not needed | FastAPI fits a typed JSON API plus SSE better |
| Flask | Small | Less built-in validation and typing; no OpenAPI generation | Weaker contracts |

## Consequences

- Positive: legacy behaviour can be compared directly (differential tests), and the OpenAPI schema generates the TypeScript client.
- Negative: two languages in the repository; contributors need Python and TypeScript skills.
- `requests` is replaced by `httpx` in the port; the WooCommerce tests are adapted to `respx`.
