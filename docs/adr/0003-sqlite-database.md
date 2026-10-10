# ADR-0003: SQLite (WAL) with SQLAlchemy and Alembic

> **Status:** Accepted · **Owner:** TecHaust Technologies (@techaust) · **Last reviewed:** 2026-10-11 · **Audience:** engineers

**Date:** 2026-10-11 · **Deciders:** Engineering

## Context

Legacy persists state in JSON files with a hand-written write-ahead journal and atomic snapshots (`legacy:omnisync/state.py:18-140`). There is no run history, and the data cannot be queried. Workload per instance: one user, at most about 100 archives per day, about 5,000 catalog rows, and audit rows that grow slowly.

## Decision

Use SQLite 3 in WAL mode with `synchronous=FULL` and `foreign_keys=ON`, accessed through SQLAlchemy 2. Schema changes are managed by Alembic migrations. One database file per instance lives in the data directory.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
|---|---|---|---|
| PostgreSQL | Concurrency, rich types | Installation and operations burden on each PC | Unjustified for one user per instance |
| Keep JSON files | No migration | No queries or history; hand-rolled durability | The problem being solved |
| DuckDB | Fast analytics | Not designed for transactional OLTP writes | Wrong workload |

## Consequences

- Positive: transactional state, queryable history, trivial backup through the online backup API, no extra service.
- Negative: a single writer. Fine with one process ([ADR-0004](0004-in-process-job-runner.md)), but the server must run with exactly one worker.
- SQLAlchemy keeps a future move to PostgreSQL feasible if hosting is ever required.
