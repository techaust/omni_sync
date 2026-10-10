# ADR-0012: Session-cookie authentication hardened for localhost

> **Status:** Accepted · **Owner:** TecHaust Technologies (@techaust) · **Last reviewed:** 2026-10-11 · **Audience:** engineers

**Date:** 2026-10-11 · **Deciders:** Engineering

## Context

The server listens on `127.0.0.1`. Any web page open in the user's browser can send requests to it (CSRF). A malicious domain can rebind its DNS to `127.0.0.1` and then read responses as same-origin (DNS rebinding). Other local processes can reach the port. TLS is not practical on loopback without installing a local certificate authority.

## Decision

- Use server-side sessions with an opaque random token in an `HttpOnly`, `SameSite=Strict` cookie. Only the token's hash is stored.
- Require a CSRF token header on every state-changing request and validate the `Origin` header.
- Allow-list the `Host` header (`127.0.0.1:<port>`, `localhost:<port>`) and reject everything else with 421.
- Require a one-time setup token from the console to create the first admin.
- Set no CORS headers, and use a strict CSP.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
|---|---|---|---|
| No authentication on localhost | Simplest | Any local process or rebinding attack controls the stores | Unsafe |
| JWT in localStorage | Stateless | Exposed to XSS; revocation harder | Server-side sessions are simpler and revocable |
| OS-level auth (Windows SSPI) | No passwords | Complex; poor cross-platform development story | Overkill for two users |
| Local TLS with a custom CA | Encrypted loopback | CA installation burden; little gain on loopback | Loopback traffic does not leave the machine |

## Consequences

- Positive: defends the realistic localhost threats (T-01, T-02, T-08, T-09).
- Negative: users have a password to manage. Recovery is through the local CLI (`omnisync reset-password`).
