# a2a

Hermes plugin that exposes bidirectional A2A bridge surfaces.

This repo supports both Hermes deployment paths:

- directory plugin: clone the repo into `~/.hermes/plugins/a2a/`
- packaged plugin: install it with `pip`, then let Hermes discover the entry point

The repo root stays Hermes-compatible, but all real implementation lives in `src/hermes_a2a/`.

## Layout

```text
.
├── __init__.py
├── plugin.yaml
├── pyproject.toml
├── schemas.py
├── src/
│   └── hermes_a2a/
│       ├── __init__.py
│       ├── adapter.py
│       ├── cli.py
│       ├── client.py
│       ├── config.py
│       ├── mapping.py
│       ├── schemas.py
│       ├── server.py
│       ├── store.py
│       └── tools.py
├── tests/
│   ├── test_register.py
│   ├── test_root_plugin.py
│   ├── test_server.py
│   ├── test_shims.py
│   ├── test_store.py
│   └── test_tools.py
└── tools.py
```

## Local development

```bash
uv sync
uv run python -m unittest discover -s tests -v
```

Optional SDK install if you want to swap in the upstream Python A2A stack later:

```bash
uv sync --extra sdk
```

## Deployment

```bash
mkdir -p ~/.hermes/plugins
git clone <repo-url> ~/.hermes/plugins/a2a
```

Then restart Hermes and verify the plugin is visible:

```bash
hermes plugins list
```

If your Hermes version supports top-level CLI commands from standalone plugins,
you can use:

```bash
hermes a2a status
hermes a2a card
```

For Hermes versions that do not yet discover standalone plugin CLI commands, use
the plugin-owned console script instead:

```bash
uv run hermes-a2a status
uv run hermes-a2a card
```

## Runtime surfaces

- Inbound server:
  - `GET /.well-known/agent-card.json`
    publishes an A2A 1.0 AgentCard with the JSON-RPC endpoint in
    `supportedInterfaces`; when `A2A_BEARER_TOKEN` is configured, the card
    advertises the required bearer auth scheme while remaining publicly
    discoverable.
  - `POST /rpc` for the official A2A 1.0 JSON-RPC methods:
    `SendMessage`, `SendStreamingMessage`, `GetTask`, `ListTasks`,
    `CancelTask`, `SubscribeToTask`, `CreateTaskPushNotificationConfig`,
    `GetTaskPushNotificationConfig`, `ListTaskPushNotificationConfig`,
    `DeleteTaskPushNotificationConfig`, and `GetExtendedAgentCard`
- Outbound Hermes tools:
  - `a2a_status`
  - `a2a_list_agents`
  - `a2a_get_task`
  - `a2a_cancel_task`
  - `a2a_delegate`
- CLI:
  - `hermes a2a status`
  - `hermes a2a card`
  - `hermes a2a serve`
  - `hermes a2a agents list`
  - `hermes a2a task get <id>`
  - `hermes a2a task cancel <id>`

## Config

The plugin is configured through environment variables:

- `A2A_HOST`
- `A2A_PORT`
- `A2A_PUBLIC_BASE_URL`
- `A2A_STORE_PATH`
- `A2A_BEARER_TOKEN`
- `A2A_EXPORTED_SKILLS`
- `A2A_REMOTE_AGENTS_JSON`
- `A2A_EXECUTION_ADAPTER` (`hermes` by default, set to `demo` for deterministic protocol testing)
- `A2A_HERMES_COMMAND` (`hermes` by default)
- `A2A_HERMES_EXTRA_ARGS` (optional shell-style arguments appended to `hermes chat`)

`A2A_REMOTE_AGENTS_JSON` should decode to an object or list. Example:

```json
{
  "demo": {
    "url": "https://agent.example.com",
    "description": "demo remote agent",
    "headers": {
      "Authorization": "Bearer secret"
    }
  }
}
```

## Notes

- JSON-RPC requests must include `A2A-Version: 1.0`. Legacy slash-style methods
  such as `message/send` and old task/message/part response fields are not
  supported.
- By default the inbound server routes A2A `SendMessage` and
  `SendStreamingMessage` calls through `hermes chat -q ... --quiet`. Set
  `A2A_EXECUTION_ADAPTER=demo` to use the deterministic demo adapter for
  protocol testing without invoking a model.
- The Hermes subprocess adapter is synchronous: streaming endpoints emit A2A
  JSON-RPC SSE `data:` frames after the underlying Hermes CLI call returns.
- The SQLite store is durable by default and keeps official A2A task snapshots,
  StreamResponse event payloads, remote delegation tracking, and named inbound
  push notification config state.
