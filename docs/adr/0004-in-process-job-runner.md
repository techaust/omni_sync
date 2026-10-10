# ADR-0004: In-process job runner without a message broker

> **Status:** Accepted · **Owner:** TecHaust Technologies (@techaust) · **Last reviewed:** 2026-10-11 · **Audience:** engineers

**Date:** 2026-10-11 · **Deciders:** Engineering

## Context

Runs take seconds to minutes. Legacy measured 24–357 s per batch of 1–30 archives (`legacy:.opencode/omnisync-runtime.json`). WooCommerce rate limiting (0.15 s spacing, `legacy:omnisync/wc.py:44`) is the throughput bottleneck, not CPU. Runs must survive browser disconnects and report progress.

## Decision

Run jobs inside the server process. Runs are persisted in the `runs` table and acted on as a queue. A supervisor thread executes them in two lanes: a mutating lane (concurrency 1) and a read-only lane (concurrency 2). Pipeline items within a run use a bounded thread pool. On restart, runs left in `running` are marked `interrupted` and can be resumed ([03-ARCHITECTURE §5](../03-ARCHITECTURE.md#5-pipeline)).

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
|---|---|---|---|
| Celery or RQ with Redis | Mature, distributed | Extra service to install on each PC; no throughput gain | Operational cost with no benefit |
| Separate worker process using the same DB queue | Isolation from the HTTP server | Two processes to supervise on Windows; IPC for events | May be revisited if isolation becomes necessary |
| asyncio-only runner | Single event loop | boto3 and the zipfile module are blocking; would need thread offloading anyway | Threads are simpler and honest about blocking I/O |

## Consequences

- Positive: one process to start, no broker, and progress events written straight to SQLite and SSE.
- Negative: a crash takes down both the API and the runner. This is mitigated by idempotent resume (NFR-REL-01).
- Uvicorn must run with exactly one worker, and a second server on the same data directory is refused through `instance.lock`.
