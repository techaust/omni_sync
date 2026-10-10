# ADR-0001: Rebuild as a local web application; retire the OpenCode front end

> **Status:** Accepted · **Owner:** TecHaust Technologies (@techaust) · **Last reviewed:** 2026-10-11 · **Audience:** engineers

**Date:** 2026-10-11 · **Deciders:** Owner, engineering

## Context

Legacy OmniSync is a Python CLI. Its everyday interface is a set of OpenCode slash commands that ask an LLM agent to run shell commands, launch a detached process, and scrape log text for progress (`legacy:.opencode/scripts/omnisync_poll.py:72-84`). Secondary download names require an interactive terminal (`legacy:omnisync/engine.py:349-366`), so OpenCode-driven runs defer those files. The owner wants a browser-based product that needs no IDE. The owner chose to run it on each user's own PC.

## Decision

Build OmniSync 2.0 as a web application served by a local server process on each PC, bound to `127.0.0.1`, and used through a normal browser. The OpenCode commands and scripts are not carried over.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
|---|---|---|---|
| Keep the CLI and add a thin web wrapper | Least work | Keeps log scraping, JSON-file state and TTY prompts | Does not fix the core UX and state problems |
| Desktop app (Electron or Tauri) | Native feel | Packaging and update overhead; still needs the same backend | No user benefit over a local web app for this audience |
| Hosted server (VPS/cloud) | Single shared instance | Internet-exposed attack surface, TLS, larger archive transfer paths | Owner chose localhost-per-PC |

## Consequences

- Positive: the UX can surface plans, progress and decisions natively, and no AI agent is needed to operate the tool.
- Negative: each PC runs its own instance, so cross-instance coordination is required ([ADR-0007](0007-multi-instance-coordination-via-s3.md)).
- The security model must defend localhost-specific threats such as CSRF from other sites and DNS rebinding ([ADR-0012](0012-session-auth-for-localhost.md)).
