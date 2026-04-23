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
  - `POST /rpc` for `message/send`, `message/stream`, `tasks/get`, `tasks/cancel`,
    `tasks/resubscribe`, and push notification config methods
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

- The current execution adapter is a demo adapter. The protocol, storage, and CLI surfaces are real, but the adapter is the seam where actual Hermes runtime integration should be added.
- The SQLite store is durable by default and keeps task snapshots, event history, remote delegation tracking, and inbound push notification config state.
