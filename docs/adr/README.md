# Architecture Decision Records

> **Status:** Accepted · **Owner:** TecHaust Technologies (@techaust) · **Last reviewed:** 2026-10-11 · **Audience:** engineers, reviewers

An ADR records one significant decision: its context, the options considered and the consequences. ADRs are immutable once accepted. To change a decision, write a new ADR that supersedes the old one and update the old one's status to `Superseded by ADR-NNNN`.

**Format:** copy [template.md](template.md), number sequentially, and use a kebab-case title.

| ADR | Title | Status |
|---|---|---|
| [ADR-0001](0001-local-web-application.md) | Rebuild as a local web application; retire the OpenCode front end | Accepted |
| [ADR-0002](0002-python-fastapi-backend.md) | Python and FastAPI backend reusing the legacy domain logic | Accepted (amended by ADR-0014) |
| [ADR-0003](0003-sqlite-database.md) | SQLite (WAL) with SQLAlchemy and Alembic | Accepted |
| [ADR-0004](0004-in-process-job-runner.md) | In-process job runner without a message broker | Accepted |
| [ADR-0005](0005-react-typescript-spa.md) | React and TypeScript single-page application | Accepted |
| [ADR-0006](0006-server-sent-events.md) | Server-sent events for real-time updates | Accepted |
| [ADR-0007](0007-multi-instance-coordination-via-s3.md) | Multi-instance coordination through S3 conditional writes | Accepted |
| [ADR-0008](0008-ai-gateway-and-approval.md) | Server-side AI gateway with typed tools and human approval | Accepted |
| [ADR-0009](0009-downgrades-held-for-approval.md) | Downgrades are held for approval | Accepted |
| [ADR-0010](0010-n-store-model.md) | Configurable N-store model | Accepted |
| [ADR-0011](0011-license-gpl-3.md) | Licence remains GPL-3.0 | Accepted |
| [ADR-0012](0012-session-auth-for-localhost.md) | Session-cookie authentication hardened for localhost | Accepted |
| [ADR-0013](0013-design-system-shadcn-indigo.md) | Design system based on shadcn/ui with an indigo accent | Accepted (amended by ADR-0014) |
| [ADR-0014](0014-milestone-3-implementation-adjustments.md) | Implementation adjustments from Milestone 3 | Accepted |
| [ADR-0015](0015-csp-style-attributes.md) | Allow inline style attributes in the Content Security Policy | Accepted |
| [ADR-0016](0016-milestone-4a-domain-port.md) | Domain-port decisions from Milestone 4a | Accepted |
