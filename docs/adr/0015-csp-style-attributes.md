# ADR-0015: Allow inline style attributes in the Content Security Policy

> **Status:** Accepted · amends control C-22 in [06-SECURITY](../06-SECURITY.md#7-security-controls) · **Owner:** TecHaust Technologies (@techaust) · **Last reviewed:** 2026-10-11 · **Audience:** engineers, reviewers

**Date:** 2026-10-11 · **Deciders:** Engineering

## Context

Control C-22 specified `style-src 'self' 'nonce-…'`, which blocks every inline style, both `<style>` elements and `style="…"` attributes. The end-to-end suite (Chromium, Firefox, WebKit) fails on any CSP violation. In CI, WebKit reported `style-src-attr` violations when a Radix Select rendered. The violating element was the visually hidden native `<select>` that Radix keeps for form autofill, styled `position: absolute; width: 1px; …`. Chromium and Firefox reported nothing for the same page.

If those styles are blocked, the hidden element can become visible and break the layout. Inline style attributes are pervasive in React component libraries (positioning popovers, hidden form controls, scroll locks), so removing them is not practical.

## Decision

Add the CSP Level 3 directive `style-src-attr 'unsafe-inline'`. It permits inline style **attributes** only.

`style-src` (which governs `<style>` elements and stylesheets) stays `'self' 'nonce-<per-response>'`. `script-src` stays `'self'`.

`backend/tests/test_http_security.py` asserts this exact shape, and the end-to-end suite still fails on any other CSP violation.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
|---|---|---|---|
| `'unsafe-hashes'` plus a hash for every style attribute | Narrowest | Attribute values vary at runtime (positions, sizes); impossible to enumerate | Not workable |
| `style-src 'unsafe-inline'` | Simple | Also allows `<style>` elements, whose selectors can exfiltrate data | Far broader than needed |
| Skip WebKit in CI | No CSP change | Hides a real layout risk for Safari and WebKit users | Unacceptable |
| Replace Radix Select with a native `<select>` | No hidden element | Loses consistent styling; other components still set style attributes | Treats one symptom only |

## Consequences

- Residual risk: an attacker who could already inject HTML attributes could also restyle elements (UI redressing). Injection itself is prevented by React's escaping and the `dangerouslySetInnerHTML` ban (C-21). Style attributes cannot use selectors, so they cannot exfiltrate data.
- Threat T-14 (stored XSS) is unchanged: scripts remain blocked.
- If a future WebKit treats CSSOM writes like Chromium does, this directive can be removed. The end-to-end suite would then pass without it.
