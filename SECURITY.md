# Security Policy

## Supported versions

OmniSync 2.0 has not been released. Once it is, only the latest minor release receives security fixes. The legacy tool (1.1.x) receives no further changes after cutover.

## Reporting a vulnerability

**Do not open a public issue for security problems.**

Report privately through **GitHub private vulnerability reporting** at <https://github.com/techaust/omni_sync/security/advisories/new>, or contact the maintainer **@techaust** directly. Please include:

- the affected version or commit;
- steps to reproduce, or a proof of concept;
- the impact you expect (for example "a web page can trigger a live run").

Our target is to acknowledge a report within 3 working days and to give an assessment within 10 working days. (These targets are pending owner confirmation; see open question Q4 in the [roadmap](docs/11-ROADMAP.md#22-open-questions).) We will agree on a disclosure date with you once a fix is available.

## Scope

In scope: the OmniSync server, the web front end, the coordination protocol, and the handling of credentials, archives and AI output.

Out of scope: vulnerabilities in WooCommerce, WordPress, AWS or AI providers themselves (report those to their vendors), and attacks that require prior administrative control of the PC running OmniSync.

## Design

The threat model, security controls and verification plan are documented in [docs/06-SECURITY.md](docs/06-SECURITY.md).
