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

Top-level plugin CLI discovery is not available in any released Hermes tag as
of 2026-04-24. The first known Hermes core support is the unreleased upstream
PR [NousResearch/hermes-agent#13643](https://github.com/NousResearch/hermes-agent/pull/13643)
at commit `308bbf6a5480223ec484b342422fe883e8ac81e4`; the latest release
checked for this note, `v2026.4.23`, does not include it.

After installing a Hermes build that contains that commit, the same commands
should be available at the top level:

```bash
hermes a2a status
hermes a2a card
```

Treat `hermes-a2a` as the reliable fallback on older or released Hermes
installs.

This plugin registers the `a2a` command through both `ctx.register_cli_command`
and a repo-root `cli.py` compatibility shim. To verify top-level registration
in an environment with the required Hermes core support, run:

```bash
hermes a2a status
```

The JSON status payload also reports the current compatibility state under
`hermes_cli.top_level_cli_discovery`.

The unittest suite includes the same integration check as an opt-in test for
machines that have a supported Hermes build installed:

```bash
HERMES_A2A_VERIFY_TOP_LEVEL_CLI=1 uv run python -m unittest tests.test_cli_entrypoint.CliEntrypointTests.test_installed_hermes_exposes_top_level_cli_when_supported -v
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

  Hermes builds containing upstream PR
  [NousResearch/hermes-agent#13643](https://github.com/NousResearch/hermes-agent/pull/13643)
  at commit `308bbf6a5480223ec484b342422fe883e8ac81e4` may additionally
  support the same commands under `hermes a2a ...`. No released Hermes tag
  includes that support as of 2026-04-24, so keep `hermes-a2a` documented for
  older installs.

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
- The AgentCard keeps the official A2A `streaming: true` capability because
  `SendStreamingMessage` and `SubscribeToTask` return SSE task events. It also
  advertises the non-required
  `https://github.com/caelaxie/hermes_a2a/extensions/runtime-streaming`
  extension so clients can distinguish task-event streaming from token/tool
  runtime streaming.
- The SQLite store is durable by default and keeps official A2A task snapshots,
  StreamResponse event payloads, remote delegation tracking, and named inbound
  push notification config state.
