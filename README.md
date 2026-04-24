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
  update, then emits the final Hermes CLI output after `hermes chat` returns.
  It does not yet stream individual model tokens from the Hermes CLI.
- The SQLite store is durable by default and keeps official A2A task snapshots,
  StreamResponse event payloads, remote delegation tracking, and named inbound
  push notification config state.
