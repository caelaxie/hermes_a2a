# Repository Instructions

## Project Shape

This repository is the `a2a` Hermes plugin. It supports two load paths:

- packaged plugin entry point: `hermes_a2a` from `pyproject.toml`
- directory plugin compatibility from the repo root shims: `__init__.py`,
  `schemas.py`, and `tools.py`

Keep real implementation under `src/hermes_a2a/`. Root-level files should stay
thin compatibility shims unless the Hermes directory-plugin contract requires
otherwise.

## A2A Protocol Compliance

The official A2A protocol specification is:

https://a2a-protocol.org/latest/specification/

When changing any public A2A surface, verify against the latest official spec,
not only existing tests or README examples. Protocol-sensitive files include:

- `src/hermes_a2a/server.py` for inbound HTTP, JSON-RPC, SSE, auth, and errors
- `src/hermes_a2a/client.py` for outbound A2A JSON-RPC calls
- `src/hermes_a2a/mapping.py` for AgentCard, tasks, messages, parts, artifacts,
  statuses, and stream events
- `src/hermes_a2a/store.py` for persisted protocol state
- `tests/test_server.py` for end-to-end protocol regression coverage

Treat spec compliance as a contract. If this plugin declares A2A 1.0 support,
then JSON-RPC methods, AgentCard fields, version handling, task/message/part
schemas, response wrappers, streaming events, push notification config shapes,
and errors must match the official A2A 1.0 spec.

Do not introduce new protocol aliases or compatibility fields silently. If
legacy support is needed, document it in code/tests and keep official A2A
behavior as the default path.

## Hermes Plugin Conventions

- Register tools and CLI commands in `src/hermes_a2a/__init__.py`.
- Keep model-facing tool schemas in `src/hermes_a2a/schemas.py`.
- Tool handlers in `src/hermes_a2a/tools.py` must accept `args, **kwargs` and
  return JSON strings on both success and error paths.
- Keep `plugin.yaml` aligned with registered tools.
- The adapter boundary is `src/hermes_a2a/adapter.py`; do not mix Hermes runtime
  execution details into protocol mapping code.
- Keep configuration in `src/hermes_a2a/config.py` and prefer environment
  variables already used by the repo before adding new ones.

## Testing

Use the repo's unittest suite:

```bash
uv run python -m unittest discover -s tests -v
```

If uv cannot read or write the default cache in a sandboxed environment, use a
writable cache path:

```bash
env UV_CACHE_DIR=/tmp/hermes-a2a-uv-cache uv run python -m unittest discover -s tests -v
```

Add or update regression tests when fixing protocol bugs. For A2A compliance
work, prefer tests that exercise the public HTTP/JSON-RPC/SSE behavior through
`create_server()` instead of only testing helpers.

## Change Discipline

- Keep changes narrowly scoped to the protocol, plugin, or CLI surface being
  edited.
- Do not rewrite storage or mapping layers just to clean up style.
- Preserve durable SQLite behavior unless the task explicitly changes the state
  model.
- Update README examples when public commands, environment variables, or
  protocol behavior change.
- Before claiming compliance, run the test suite and include at least one check
  against the official A2A method/card shape affected by the change.
