# Repository Instructions

## Project Shape

This repository is the `a2a` Hermes plugin. It supports two load paths:

- packaged plugin entry point: `hermes_a2a` from `pyproject.toml`
- standalone console script: `hermes-a2a` from `src/hermes_a2a/cli.py`
- directory plugin compatibility from the repo root shims: `__init__.py`,
  `schemas.py`, and `tools.py`

Keep real implementation under `src/hermes_a2a/`. Root-level files should stay
thin compatibility shims unless the Hermes directory-plugin contract requires
otherwise.

## Implementation Map

- `src/hermes_a2a/__init__.py` registers the Hermes tools and the `a2a` CLI
  command group.
- `src/hermes_a2a/cli.py` owns both `hermes a2a ...` command wiring and the
  fallback `hermes-a2a` console script.
- `src/hermes_a2a/config.py` owns all environment parsing and computed URLs.
- `src/hermes_a2a/adapter.py` is the only boundary for executing Hermes runtime
  work. The `hermes` adapter shells out to `hermes chat --quiet ... -q`, while
  the `demo` adapter is for deterministic protocol tests.
- `src/hermes_a2a/server.py` owns the inbound HTTP, JSON-RPC, SSE, auth, push
  notification, and task orchestration surface.
- `src/hermes_a2a/client.py` is the minimal outbound A2A JSON-RPC/SSE client
  used by remote delegation tools.
- `src/hermes_a2a/mapping.py` translates between Hermes adapter events and A2A
  AgentCard, task, message, part, artifact, status, and SSE event shapes.
- `src/hermes_a2a/store.py` is the durable SQLite store for task snapshots,
  event journals, push configs, and outbound remote-task mappings.
- `src/hermes_a2a/schemas.py` contains the model-facing Hermes tool schemas.
- `src/hermes_a2a/tools.py` contains Hermes tool handlers and must return JSON
  strings on both success and error paths.

## A2A Protocol Compliance

The official A2A protocol specification is:

https://a2a-protocol.org/latest/specification/

When changing any public A2A surface, verify against the latest official spec,
not only existing tests or README examples. Protocol-sensitive files include:

- `src/hermes_a2a/server.py` for inbound HTTP, JSON-RPC, SSE, auth, and errors
- `src/hermes_a2a/client.py` for outbound A2A JSON-RPC calls
- `src/hermes_a2a/mapping.py` for AgentCard, tasks, messages, parts, artifacts,
  statuses, and stream events
- `src/hermes_a2a/store.py` for persisted protocol state, event replay, push
  configs, and remote delegation bookkeeping
- `tests/test_server.py` for end-to-end protocol regression coverage

Treat spec compliance as a contract. If this plugin declares A2A 1.0 support,
then JSON-RPC methods, AgentCard fields, version handling, task/message/part
schemas, response wrappers, streaming events, push notification config shapes,
and errors must match the official A2A 1.0 spec.

Do not introduce new protocol aliases or compatibility fields silently. If
legacy support is needed, document it in code/tests and keep official A2A
behavior as the default path.

## Runtime Surfaces

The current inbound server exposes:

- `GET /.well-known/agent-card.json`
- `POST /rpc` for official A2A 1.0 JSON-RPC methods: `SendMessage`,
  `SendStreamingMessage`, `GetTask`, `ListTasks`, `CancelTask`,
  `SubscribeToTask`, `CreateTaskPushNotificationConfig`,
  `GetTaskPushNotificationConfig`, `ListTaskPushNotificationConfig`,
  `DeleteTaskPushNotificationConfig`, and `GetExtendedAgentCard`

Legacy slash-style JSON-RPC methods and custom SSE replay endpoints are not
part of the public A2A surface.

The current Hermes tools are:

- `a2a_status`
- `a2a_list_agents`
- `a2a_get_task`
- `a2a_cancel_task`
- `a2a_delegate`

The current CLI commands are:

- `hermes a2a status`
- `hermes a2a card`
- `hermes a2a serve`
- `hermes a2a agents list`
- `hermes a2a task get <id>`
- `hermes a2a task cancel <id>`
- `hermes-a2a ...` with the same subcommands for Hermes versions that do not
  discover standalone plugin CLI commands

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
- Keep `README.md` in sync when public commands, config variables, or protocol
  behavior changes.

## Configuration

Current environment variables parsed by `src/hermes_a2a/config.py`:

- `A2A_HOST`
- `A2A_PORT`
- `A2A_PUBLIC_BASE_URL`
- `A2A_STORE_PATH`
- `A2A_BEARER_TOKEN`
- `A2A_EXPORTED_SKILLS`
- `A2A_REMOTE_AGENTS_JSON`
- `A2A_DEFAULT_TIMEOUT_SECONDS`
- `A2A_ALLOW_RUNTIME_WRITE`
- `A2A_EXECUTION_ADAPTER` (`hermes` by default, `demo` for deterministic tests)
- `A2A_HERMES_COMMAND`
- `A2A_HERMES_EXTRA_ARGS`

Prefer extending this config object over reading environment variables directly
from server, client, adapter, tool, or CLI code. Keep config parsing covered by
`tests/test_config.py`.

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

Test ownership map:

- `tests/test_server.py` covers inbound JSON-RPC/SSE behavior, push config CRUD,
  and loopback outbound delegation against a local server.
- `tests/test_adapter.py` covers Hermes subprocess adapter command construction,
  output sanitization, timeout/startup failure handling, truncation, and adapter
  selection.
- `tests/test_store.py` covers durable SQLite task, event, push config, and
  remote-task persistence.
- `tests/test_tools.py` covers JSON-string Hermes tool behavior and status
  payloads.
- `tests/test_config.py` covers environment parsing.
- `tests/test_register.py`, `tests/test_root_plugin.py`, and `tests/test_shims.py`
  cover packaged and directory-plugin registration paths.
- `tests/test_cli_entrypoint.py` covers the standalone `hermes-a2a` entrypoint.

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
