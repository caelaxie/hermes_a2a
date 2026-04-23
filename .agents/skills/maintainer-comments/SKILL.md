---
name: maintainer-comments
description: Add explanatory code comments for maintainers new to the Hermes A2A plugin. Use when asked to annotate code, add maintainer comments, improve codebase onboarding, explain non-obvious protocol mapping, or make this repository easier to understand without changing behavior.
---

# Maintainer Comments

## Overview

Use this skill to add high-signal comments that help new maintainers understand
the Hermes A2A plugin quickly. The goal is to explain non-obvious intent,
boundaries, protocol compatibility, and persistence behavior without narrating
self-evident code.

## Workflow

1. Inspect the current code path before editing.
   Read nearby tests and callers before adding comments. Preserve behavior; this
   is a comment-only workflow unless the user explicitly asks for code changes.
   Check the official A2A spec when commenting protocol behavior:
   https://a2a-protocol.org/latest/specification/

2. Identify comments worth adding.
   Add comments where a maintainer would otherwise need to trace multiple files,
   protocol docs, or runtime contracts to understand why the code is shaped that
   way. Prefer comments near boundary crossings: A2A protocol to Hermes runtime,
   Hermes tool to HTTP client, adapter event to task/artifact/status payload, and
   SQLite snapshot to public response. Skip obvious assignments, imports, simple
   constructors, and direct field copies.

3. Write comments for maintainers.
   Explain why the code exists, what contract it protects, or which
   compatibility behavior it preserves. Keep comments short. Prefer docstrings
   for public helpers, service methods, adapters, and classes. Prefer inline
   comments only before tricky blocks, compatibility decisions, protocol
   translations, or intentional error-handling choices. If a comment repeats a
   function name or restates a single line, delete it.

4. Verify the sweep.
   Run or update targeted tests only if the user also requested behavior
   changes. For comment-only changes, inspect the diff and confirm no executable
   code changed. Report any intentionally skipped areas where comments would
   become stale or speculative.

## Repo Hotspots

Focus comments around these files when doing a maintainer sweep:

- `src/hermes_a2a/server.py`: inbound HTTP, JSON-RPC dispatch, SSE streaming,
  auth, request errors, push notification delivery, and service orchestration.
- `src/hermes_a2a/client.py`: outbound A2A requests, remote agent resolution,
  JSON-RPC request construction, streaming response parsing, and remote errors.
- `src/hermes_a2a/mapping.py`: AgentCard generation, Hermes event to A2A task
  mapping, message/part/artifact/status conversion, and SSE payload shape.
- `src/hermes_a2a/__init__.py`, `src/hermes_a2a/tools.py`, and
  `src/hermes_a2a/adapter.py`: Hermes plugin registration, tool handler
  contracts, and the boundary between demo execution and real Hermes runtime
  integration.
- `src/hermes_a2a/store.py`: durable task snapshots, event journals, push
  configs, and remote delegation bookkeeping.
- `src/hermes_a2a/config.py`: environment-driven configuration, remote agent
  presets, public URL derivation, and store path resolution.

## Comment Quality Bar

Good comments:

- "Keep the remote mapping separate from the task snapshot so local task IDs can
  remain stable even when a delegated agent uses its own task ID."
- "This endpoint is unauthenticated because A2A discovery requires the public
  agent card before a client knows which security scheme to use."
- "The adapter emits Hermes-native events; mapping.py is the only place that
  should translate those into A2A task/status/artifact shapes."

Avoid comments like:

- "Set task_id to the task ID."
- "Import json."
- "Loop over events."
- Comments that claim official A2A compliance without checking the current
  spec and the emitted wire shape.
