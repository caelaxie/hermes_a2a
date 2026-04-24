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
├── cli.py
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

Use the plugin-owned console script for CLI operations:

```bash
uv run hermes-a2a status
uv run hermes-a2a card
```

Some Hermes versions may also expose standalone plugin CLI commands at the
top level after the plugin is enabled:

```bash
hermes a2a status
hermes a2a card
```

Treat `hermes-a2a` as the reliable command until Hermes core exposes
standalone plugin CLI discovery in your installation.

This plugin registers the `a2a` command through both `ctx.register_cli_command`
and a repo-root `cli.py` compatibility shim. Current Hermes core integration is
tracked upstream in [NousResearch/hermes-agent#13643](https://github.com/NousResearch/hermes-agent/pull/13643).
After installing a Hermes build with that support, verify the top-level path:

```bash
hermes a2a status
```

### Persistent inbound server

For local protocol tests, running the bridge in the foreground is enough:

```bash
uv run hermes-a2a serve
```

For a deployment that other A2A clients can discover and delegate to, supervise
the same command so `A2A_PUBLIC_BASE_URL` stays continuously reachable. A
systemd user service is the simplest Linux pattern because stdout and stderr are
captured in the user journal and the process can restart after failures.

Create an environment file such as `~/.config/hermes-a2a/env`:

```ini
A2A_HOST=127.0.0.1
A2A_PORT=8000
A2A_PUBLIC_BASE_URL=https://a2a.example.com
A2A_STORE_PATH=/home/alice/.local/share/hermes-a2a/state.db
A2A_BEARER_TOKEN=replace-with-a-long-random-token
A2A_DEFAULT_TIMEOUT_SECONDS=120
A2A_EXECUTION_ADAPTER=hermes
A2A_HERMES_COMMAND=/usr/local/bin/hermes
```

Use `A2A_HOST=0.0.0.0` only when the process should accept direct network
connections. Keep `A2A_STORE_PATH` on persistent storage and protect the env
file if it contains `A2A_BEARER_TOKEN`:

```bash
mkdir -p ~/.config/hermes-a2a ~/.local/share/hermes-a2a
chmod 700 ~/.config/hermes-a2a ~/.local/share/hermes-a2a
chmod 600 ~/.config/hermes-a2a/env
```

Install the package or clone in a stable location, then use an absolute
`hermes-a2a` path in `~/.config/systemd/user/hermes-a2a.service`:

```ini
[Unit]
Description=Hermes A2A inbound bridge
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/.local/share/hermes-a2a
EnvironmentFile=%h/.config/hermes-a2a/env
ExecStart=%h/.local/pipx/venvs/hermes-a2a/bin/hermes-a2a serve
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=default.target
```

Adjust `ExecStart` to the actual console script path for your installation. If
you keep the repo checkout and run through uv, use the absolute uv path with
`--directory /path/to/hermes_a2a hermes-a2a serve`. Then enable and start the
service:

```bash
systemctl --user daemon-reload
systemctl --user enable --now hermes-a2a.service
systemctl --user status hermes-a2a.service
journalctl --user -u hermes-a2a.service -f
```

If the service must start before the user logs in, enable lingering for that
account with `loginctl enable-linger "$USER"`.

When exposing the bridge through a reverse proxy, terminate TLS at the proxy and
set `A2A_PUBLIC_BASE_URL` to the external HTTPS origin. Forward both
`/.well-known/agent-card.json` and `/rpc` to the same local service. The
AgentCard endpoint intentionally remains publicly discoverable so clients can
read the bridge URL, protocol version, and advertised security scheme. When
`A2A_BEARER_TOKEN` is set, `/rpc` requires `Authorization: Bearer ...`.
Preserve the `Authorization` and `A2A-Version` headers, allow request durations
at least as long as `A2A_DEFAULT_TIMEOUT_SECONDS`, and disable response
buffering for SSE calls to `SendStreamingMessage` and `SubscribeToTask`.

Containers follow the same runtime contract: run `hermes-a2a serve` as the main
process, set `A2A_HOST=0.0.0.0`, publish `A2A_PORT`, mount a persistent
`A2A_STORE_PATH`, inject secrets through the orchestrator, and collect stdout
and stderr with the platform log driver.

Verify a deployment from outside the host or proxy:

```bash
BASE_URL=https://a2a.example.com
TOKEN=replace-with-a-long-random-token

curl -sS "$BASE_URL/.well-known/agent-card.json"

hermes-a2a status
# Or, from Hermes, call the registered `a2a_status` tool.

curl -sS "$BASE_URL/rpc" \
  -H "Content-Type: application/json" \
  -H "A2A-Version: 1.0" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "jsonrpc": "2.0",
    "id": "deploy-check-1",
    "method": "SendMessage",
    "params": {
      "message": {
        "messageId": "deploy-check-message-1",
        "role": "ROLE_USER",
        "parts": [{"text": "hello from deployment check"}]
      }
    }
  }'
```

For deterministic smoke tests that do not invoke Hermes or a model, temporarily
set `A2A_EXECUTION_ADAPTER=demo`, restart the service, run the same `SendMessage`
check, then restore `A2A_EXECUTION_ADAPTER=hermes` for real delegation.

## Runtime surfaces

- Inbound server:
  - `GET /.well-known/agent-card.json`
    publishes an A2A 1.0 AgentCard with the JSON-RPC endpoint in
    `supportedInterfaces`; when `A2A_BEARER_TOKEN` is configured, the card
    advertises the required bearer auth scheme while remaining publicly
    discoverable. Responses include `Cache-Control` and `ETag` headers so
    clients can cache the card and detect changes.
  - `POST /rpc` for the official A2A 1.0 JSON-RPC methods:
    `SendMessage`, `SendStreamingMessage`, `GetTask`, `ListTasks`,
    `CancelTask`, `SubscribeToTask`, `CreateTaskPushNotificationConfig`,
    `GetTaskPushNotificationConfig`, `ListTaskPushNotificationConfigs`,
    `DeleteTaskPushNotificationConfig`, and `GetExtendedAgentCard`
- Outbound Hermes tools:
  - `a2a_status`
  - `a2a_list_agents`
  - `a2a_get_task`
  - `a2a_cancel_task`
  - `a2a_delegate`
- CLI:
  - `hermes-a2a status`
  - `hermes-a2a card`
  - `hermes-a2a serve`
  - `hermes-a2a agents list`
  - `hermes-a2a task get <id>`
  - `hermes-a2a task cancel <id>`

  Hermes versions with standalone plugin CLI discovery may additionally support
  the same commands under `hermes a2a ...`; see
  [NousResearch/hermes-agent#13643](https://github.com/NousResearch/hermes-agent/pull/13643)
  for the upstream CLI wiring.

## Config

The plugin is configured through environment variables:

- `A2A_HOST`
- `A2A_PORT`
- `A2A_PUBLIC_BASE_URL`
- `A2A_STORE_PATH`
- `A2A_BEARER_TOKEN`
- `A2A_EXPORTED_SKILLS`
- `A2A_REMOTE_AGENTS_JSON`
- `A2A_DEFAULT_TIMEOUT_SECONDS` (`120` by default for Hermes runtime and
  outbound A2A calls)
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
  supported. A missing version header is rejected because this server only
  advertises A2A 1.0 in its AgentCard `supportedInterfaces`.
- The official Python SDK `ClientFactory` path sets the A2A version header on
  its HTTP client. Prefer that path for SDK clients:

  ```python
  from a2a.client import ClientConfig, ClientFactory

  factory = ClientFactory(ClientConfig(streaming=False))
  client = await factory.create_from_url("http://127.0.0.1:9999")
  ```

  If you build raw HTTP requests or instantiate lower-level SDK transports
  directly, set the header yourself:

  ```python
  headers = {
      "Content-Type": "application/json",
      "A2A-Version": "1.0",
  }
  ```
- Task RPCs use direct task IDs in params such as `{"id": "task-id"}`. Push
  notification config RPCs use the flat A2A 1.0 `TaskPushNotificationConfig`
  shape with `taskId`, config `id`, `url`, optional `token`, and optional
  `authentication`; resource-name params such as `tasks/{id}` are not
  supported.

Example push notification config request:

```json
{
  "taskId": "task-id",
  "id": "callback-1",
  "url": "https://example.com/a2a/push",
  "token": "task-callback-token",
  "authentication": {
    "scheme": "Bearer",
    "credentials": "secret"
  }
}
```
- By default the inbound server routes A2A `SendMessage` and
  `SendStreamingMessage` calls through `hermes chat -q ... --quiet`. Set
  `A2A_EXECUTION_ADAPTER=demo` to use the deterministic demo adapter for
  protocol testing without invoking a model.
- The default runtime timeout is 120 seconds. Override it with
  `A2A_DEFAULT_TIMEOUT_SECONDS` when a deployment needs shorter or longer model
  execution and remote-agent request windows.
- The Hermes subprocess adapter starts streaming immediately with a task status
  update, then forwards incremental Hermes subprocess stdout chunks as A2A
  artifact updates when the runtime flushes output before exit. If Hermes does
  not provide fine-grained output for the invocation, the adapter falls back to
  the final CLI output after `hermes chat` returns.
- The AgentCard keeps the official A2A `streaming: true` capability because
  `SendStreamingMessage` and `SubscribeToTask` return SSE task events. It also
  advertises the non-required
  `https://github.com/caelaxie/hermes_a2a/extensions/runtime-streaming`
  extension so clients can distinguish task-event streaming from incremental
  Hermes runtime-output streaming.
- The SQLite store is durable by default and keeps official A2A task snapshots,
  StreamResponse event payloads, remote delegation tracking, and named inbound
  push notification config state.
