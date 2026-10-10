# ADR-0006: Server-sent events for real-time updates

> **Status:** Accepted · **Owner:** TecHaust Technologies (@techaust) · **Last reviewed:** 2026-10-11 · **Audience:** engineers

**Date:** 2026-10-11 · **Deciders:** Engineering

## Context

The UI needs live run progress, needs-input notifications and streamed AI responses. All of these flow from server to browser. Commands from the browser are ordinary HTTP requests.

## Decision

Use server-sent events (`text/event-stream`). Event IDs come from the global `events` table so reconnecting clients replay missed events through `Last-Event-ID`. One `SharedWorker` per browser holds a single connection for all tabs. AI chat responses are streamed in SSE format as the body of the POST response and read with `fetch`; they are not resumable, so the client reloads the persisted message after a dropped connection ([05-API-SPEC §5](../05-API-SPEC.md#5-server-sent-events)).

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
|---|---|---|---|
| WebSockets | Bidirectional | Needs a custom reconnect and replay protocol and separate auth handling | Bidirectionality not needed |
| Polling | Simplest | Latency or load trade-off; misses NFR-PERF-05 at reasonable intervals | Worse UX |

## Consequences

- Positive: built-in browser reconnection, works with cookie auth, trivial to proxy and test.
- Negative: browsers limit HTTP/1.1 connections per origin to about 6, shared across all tabs. The SPA therefore uses one `SharedWorker`-owned stream per browser, carrying global events and the subscribed runs' events. Browsers without `SharedWorker` open one stream per tab.
- Negative: AI response streams are `fetch`-based and not resumable; the client reloads the persisted message after a dropped connection.
