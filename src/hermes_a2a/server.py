"""Inbound A2A server and orchestration helpers."""

from __future__ import annotations

import hashlib
import json
import threading
import urllib.request
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Iterable
from urllib.parse import urlparse
from uuid import uuid4

from .adapter import (
    DemoHermesExecutionAdapter,
    HermesExecutionAdapter,
    HermesSubprocessExecutionAdapter,
)
from .config import A2APluginConfig, HERMES_TOP_LEVEL_CLI_MIN_COMMIT, load_config
from .mapping import (
    apply_hermes_event,
    build_agent_card,
    build_initial_task,
    extract_text_from_message,
    make_sse_payload,
    trim_task_for_response,
)
from .protocol import (
    A2A_CONTENT_TYPE,
    A2AProtocolError,
    ERROR_INTERNAL,
    ERROR_INVALID_PARAMS,
    ERROR_METHOD_NOT_FOUND,
    ERROR_PARSE,
    ERROR_TASK_NOT_FOUND,
    ERROR_UNSUPPORTED_OPERATION,
    ERROR_VERSION_NOT_SUPPORTED,
    METHOD_CANCEL_TASK,
    METHOD_CREATE_PUSH_CONFIG,
    METHOD_DELETE_PUSH_CONFIG,
    METHOD_GET_EXTENDED_AGENT_CARD,
    METHOD_GET_PUSH_CONFIG,
    METHOD_GET_TASK,
    METHOD_LIST_PUSH_CONFIGS,
    METHOD_LIST_TASKS,
    METHOD_SEND_MESSAGE,
    METHOD_SEND_STREAMING_MESSAGE,
    METHOD_SUBSCRIBE_TO_TASK,
    PROTOCOL_VERSION,
    TASK_STATE_CANCELED,
    TERMINAL_TASK_STATES,
    decode_page_token,
    decode_task_page_token,
    encode_page_token,
    encode_task_page_token,
    jsonrpc_error,
    jsonrpc_success,
    parse_rfc3339_timestamp,
    push_config_name,
)
from .store import SQLiteTaskStore


def _build_execution_adapter(config: A2APluginConfig) -> HermesExecutionAdapter:
    if config.execution_adapter == "hermes":
        return HermesSubprocessExecutionAdapter(
            command=config.hermes_command,
            timeout_seconds=config.default_timeout_seconds,
            extra_args=config.hermes_extra_args,
        )
    if config.execution_adapter == "demo":
        return DemoHermesExecutionAdapter()
    raise ValueError(
        "Unsupported A2A_EXECUTION_ADAPTER "
        f"{config.execution_adapter!r}; expected 'hermes' or 'demo'"
    )


def _required_string(params: dict, field: str) -> str:
    value = str(params.get(field) or "").strip()
    if not value:
        raise ValueError(f"{field} is required")
    return value


class A2AService:
    """Application service shared by Hermes tools, CLI, and HTTP handlers.

    This class owns orchestration across config, storage, adapters, and
    protocol mapping so the HTTP handler remains a thin transport layer.
    """

    def __init__(
        self,
        config: A2APluginConfig | None = None,
        store: SQLiteTaskStore | None = None,
        adapter: HermesExecutionAdapter | None = None,
    ) -> None:
        self.config = config or load_config()
        self.store = store or SQLiteTaskStore(self.config.resolved_store_path)
        self.adapter = adapter or _build_execution_adapter(self.config)

    def status_payload(self) -> dict:
        payload = self.config.status_dict()
        payload["status"] = "ok"
        payload["server"] = {
            "card": self.agent_card(),
            "local_tasks": len(self.store.list_tasks()),
        }
        payload["message"] = (
            "Hermes A2A bridge is configured. Start the inbound JSON-RPC + SSE "
            "surface with `hermes-a2a serve`. `hermes a2a serve` only works on "
            "Hermes builds with standalone plugin CLI discovery; no released "
            "Hermes tag includes that support yet, so use `hermes-a2a` unless "
            f"your Hermes build contains {HERMES_TOP_LEVEL_CLI_MIN_COMMIT}."
        )
        return payload

    def agent_card(self) -> dict:
        return build_agent_card(self.config)

    def _iter_adapter_events(
        self,
        task_id: str,
        context_id: str,
        message_text: str,
        stream: bool,
        metadata: dict | None = None,
    ) -> Iterable:
        metadata = dict(metadata or {})
        hermes_session = self.store.get_hermes_session(task_id, context_id)
        if hermes_session:
            metadata["hermes_session_id"] = hermes_session["hermesSessionId"]
        # Existing tasks represent continuation. Streaming still uses the
        # adapter's streaming method because the transport contract, not task
        # freshness, decides how updates should be delivered to the caller.
        existing = self.store.get_task(task_id)
        if existing is None:
            if stream:
                return self.adapter.stream(task_id, context_id, message_text, metadata)
            return self.adapter.start(task_id, context_id, message_text, metadata)
        if stream:
            return self.adapter.stream(task_id, context_id, message_text, metadata)
        return self.adapter.continue_task(task_id, context_id, message_text, metadata)

    def _notify_push(self, task_id: str, stream_response: dict) -> None:
        for push_config in self.store.list_push_configs_for_task(task_id):
            payload = json.dumps(stream_response, sort_keys=True).encode("utf-8")
            headers = {"Content-Type": A2A_CONTENT_TYPE}
            auth = push_config.get("authentication") or {}
            scheme = str(auth.get("scheme") or "").strip()
            credentials = str(auth.get("credentials", "")).strip()
            if credentials and scheme:
                headers["Authorization"] = f"{scheme} {credentials}"
            request = urllib.request.Request(
                push_config["url"],
                data=payload,
                headers=headers,
            )
            try:
                urllib.request.urlopen(request, timeout=self.config.default_timeout_seconds).read()
            except Exception:
                # Push delivery is best-effort. The event remains durable in SQLite.
                continue

    def _prepare_message_task(self, params: dict) -> tuple[dict, str, str, str]:
        message = params.get("message")
        message_text = extract_text_from_message(message)
        if not isinstance(message, dict):
            raise ValueError("SendMessageRequest.message is required")
        if not message.get("messageId"):
            raise ValueError("Message.messageId is required")
        if message.get("role") != "ROLE_USER":
            raise ValueError("Message.role must be ROLE_USER")
        task_id = str(message.get("taskId") or uuid4())
        task = self.store.get_task(task_id)
        context_id = str(
            task.get("contextId")
            if task is not None
            else message.get("contextId") or task_id
        )
        if message.get("contextId") and str(message["contextId"]) != context_id:
            raise ValueError("Message.contextId must match the task context")
        message = dict(message)
        message["taskId"] = task_id
        message["contextId"] = context_id
        if task is None:
            task = build_initial_task(task_id, context_id, message, direction="inbound")
        else:
            task.setdefault("history", []).append(message)
        self._configure_push_from_send_message(task_id, params.get("configuration") or {})
        return task, task_id, context_id, message_text

    def _record_adapter_event(self, task: dict, task_id: str, adapter_event) -> dict:
        hermes_session_id = str(
            adapter_event.metadata.get("hermes_session_id") or ""
        ).strip()
        if hermes_session_id:
            self.store.set_hermes_session(task_id, task["contextId"], hermes_session_id)
        stream_response = apply_hermes_event(task, adapter_event)
        self.store.upsert_task(task, direction="inbound")
        self.store.append_event(task_id, stream_response)
        self._notify_push(task_id, stream_response)
        return stream_response

    def _finalize_message_task(self, task: dict, task_id: str, context_id: str) -> dict:
        latest = self.store.get_task(task_id)
        if latest and latest.get("status", {}).get("state") == TASK_STATE_CANCELED:
            task = latest
        adapter_metadata = self.adapter.finalize_task(task_id, context_id)
        task.setdefault("metadata", {}).update(
            {
                "adapter": adapter_metadata.get("adapter", "unknown"),
                "adapterMetadata": adapter_metadata.get("metadata", {}),
            }
        )
        self.store.upsert_task(task, direction="inbound")
        return task

    def send_message(
        self,
        params: dict,
        stream: bool = False,
    ) -> tuple[dict, list[dict]]:
        task, task_id, context_id, message_text = self._prepare_message_task(params)
        events: list[dict] = []

        # Persist each mapped event before push delivery. If a callback fails,
        # resubscribe/event replay can still return the durable update.
        for adapter_event in self._iter_adapter_events(
            task_id,
            context_id,
            message_text,
            stream=stream,
            metadata={"mode": "stream" if stream else "send"},
        ):
            stream_response = self._record_adapter_event(task, task_id, adapter_event)
            events.append(stream_response)

        task = self._finalize_message_task(task, task_id, context_id)
        return task, events

    def stream_message(self, params: dict) -> Iterable[dict]:
        task, task_id, context_id, message_text = self._prepare_message_task(params)
        self.store.upsert_task(task, direction="inbound")

        def stream_responses() -> Iterable[dict]:
            nonlocal task
            finalized = False
            try:
                for adapter_event in self._iter_adapter_events(
                    task_id,
                    context_id,
                    message_text,
                    stream=True,
                    metadata={"mode": "stream"},
                ):
                    yield self._record_adapter_event(task, task_id, adapter_event)

                task = self._finalize_message_task(task, task_id, context_id)
                finalized = True
                yield {"task": task}
            finally:
                if not finalized:
                    self._finalize_message_task(task, task_id, context_id)

        return stream_responses()

    def _configure_push_from_send_message(self, task_id: str, configuration: dict) -> None:
        if not isinstance(configuration, dict):
            raise ValueError("SendMessageRequest.configuration must be an object")
        push_config_wrapper = configuration.get("taskPushNotificationConfig")
        if push_config_wrapper is None:
            return
        if not isinstance(push_config_wrapper, dict):
            raise ValueError("configuration.taskPushNotificationConfig must be an object")
        configured_task_id = str(push_config_wrapper.get("taskId") or "").strip()
        if configured_task_id and configured_task_id != task_id:
            raise ValueError("taskPushNotificationConfig.taskId must be empty or match the task id")
        if not str(push_config_wrapper.get("url", "")).strip():
            raise ValueError("taskPushNotificationConfig.url is required")
        config_id = str(push_config_wrapper.get("id") or uuid4()).strip()
        stored_push_config = dict(push_config_wrapper)
        stored_push_config["taskId"] = task_id
        stored_push_config["id"] = config_id
        self.store.set_push_config(
            task_id,
            config_id,
            stored_push_config,
        )

    def get_task(self, task_id: str) -> dict:
        task = self.store.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        return task

    def cancel_task(self, task_id: str) -> dict:
        task = self.get_task(task_id)
        context_id = task.get("contextId", task_id)
        for adapter_event in self.adapter.cancel(task_id, context_id):
            stream_response = apply_hermes_event(task, adapter_event)
            self.store.append_event(task_id, stream_response)
            self._notify_push(task_id, stream_response)
        self.store.upsert_task(task, direction="inbound")
        return task

    def subscribe_task(self, task_id: str) -> list[dict]:
        task = self.get_task(task_id)
        state = task.get("status", {}).get("state", "")
        if state in TERMINAL_TASK_STATES:
            raise A2AProtocolError(
                ERROR_UNSUPPORTED_OPERATION,
                "SubscribeToTask is not supported for terminal tasks",
            )
        return [{"task": task}, *self.store.list_events(task_id)]

    def list_tasks(self, params: dict) -> dict:
        page_size = int(params.get("pageSize") or 50)
        if page_size < 1 or page_size > 100:
            raise ValueError("pageSize must be between 1 and 100")
        cursor = decode_task_page_token(str(params.get("pageToken") or ""))
        context_id = str(params.get("contextId") or "")
        status = str(params.get("status") or "")
        status_after = params.get("statusTimestampAfter")
        history_length = params.get("historyLength")
        history_limit = int(history_length) if history_length is not None else None
        include_artifacts = bool(params.get("includeArtifacts", False))
        tasks = self.store.list_tasks()
        if context_id:
            tasks = [task for task in tasks if task.get("contextId") == context_id]
        if status:
            tasks = [task for task in tasks if task.get("status", {}).get("state") == status]
        if status_after:
            status_after_time = parse_rfc3339_timestamp(str(status_after))
            tasks = [
                task
                for task in tasks
                if parse_rfc3339_timestamp(str(task.get("status", {}).get("timestamp", "")))
                >= status_after_time
            ]
        tasks.sort(
            key=lambda task: (
                str(task.get("status", {}).get("timestamp", "")),
                str(task.get("id", "")),
            ),
            reverse=True,
        )
        total_size = len(tasks)
        if cursor:
            cursor_key = (cursor["statusTimestamp"], cursor["id"])
            tasks = [
                task
                for task in tasks
                if (
                    str(task.get("status", {}).get("timestamp", "")),
                    str(task.get("id", "")),
                )
                < cursor_key
            ]
        page = tasks[:page_size]
        return {
            "tasks": [
                trim_task_for_response(task, history_limit, include_artifacts=include_artifacts)
                for task in page
            ],
            "nextPageToken": encode_task_page_token(page[-1]) if len(tasks) > page_size else "",
            "pageSize": page_size,
            "totalSize": total_size,
        }

    def create_push_config(self, params: dict) -> dict:
        task_id = _required_string(params, "taskId")
        self.get_task(task_id)
        if not str(params.get("url", "")).strip():
            raise ValueError("url is required")
        config_id = str(params.get("id") or uuid4()).strip()
        stored_push_config = dict(params)
        stored_push_config["taskId"] = task_id
        stored_push_config["id"] = config_id
        return self.store.set_push_config(
            task_id,
            config_id,
            stored_push_config,
        )

    def get_push_config(self, params: dict) -> dict:
        task_id = _required_string(params, "taskId")
        config_id = _required_string(params, "id")
        name = push_config_name(task_id, config_id)
        result = self.store.get_push_config(name)
        if result is None:
            raise KeyError(name)
        return result

    def list_push_configs(self, params: dict) -> dict:
        task_id = _required_string(params, "taskId")
        self.get_task(task_id)
        page_size = int(params.get("pageSize") or 50)
        if page_size < 1 or page_size > 100:
            raise ValueError("pageSize must be between 1 and 100")
        offset = decode_page_token(str(params.get("pageToken") or ""))
        configs = self.store.list_push_configs(task_id)
        page = configs[offset : offset + page_size]
        next_offset = offset + page_size
        return {
            "configs": page,
            "nextPageToken": encode_page_token(next_offset) if next_offset < len(configs) else "",
        }

    def delete_push_config(self, params: dict) -> None:
        task_id = _required_string(params, "taskId")
        config_id = _required_string(params, "id")
        name = push_config_name(task_id, config_id)
        if self.store.get_push_config(name) is None:
            raise KeyError(name)
        self.store.delete_push_config(name)

    def extended_agent_card(self) -> dict:
        card = self.agent_card()
        if card.get("capabilities", {}).get("extendedAgentCard") is not True:
            raise A2AProtocolError(
                ERROR_UNSUPPORTED_OPERATION,
                "Extended agent card is not supported",
            )
        return card

    def close(self) -> None:
        self.store.close()


class _RequestHandler(BaseHTTPRequestHandler):
    """HTTP entrypoint for the A2A service."""

    server_version = "HermesA2A/0.1"

    def __init__(self, *args, service: A2AService, **kwargs):
        self._service = service
        super().__init__(*args, **kwargs)

    def log_message(self, format: str, *args) -> None:  # noqa: A003 - inherited name
        del format, args

    def _require_auth(self) -> bool:
        token = self._service.config.bearer_token
        if not token:
            return True
        header = self.headers.get("Authorization", "")
        return header == f"Bearer {token}"

    def _send_json(self, payload: dict, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_agent_card(self) -> None:
        body = json.dumps(self._service.agent_card(), sort_keys=True).encode("utf-8")
        etag = hashlib.sha256(body).hexdigest()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "public, max-age=300")
        self.send_header("ETag", f'"{etag}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_stream(self, request_id, stream_responses: Iterable[dict]) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        for stream_response in stream_responses:
            try:
                self.wfile.write(make_sse_payload(jsonrpc_success(request_id, stream_response)))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urlparse(self.path)
        if parsed.path == "/.well-known/agent-card.json":
            # Discovery is public so clients can learn the agent's advertised
            # transport and security scheme before making authenticated calls.
            self._send_agent_card()
            return

        if not self._require_auth():
            self._send_json({"error": "Unauthorized"}, status=HTTPStatus.UNAUTHORIZED)
            return

        self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if not self._require_auth():
            self._send_json({"error": "Unauthorized"}, status=HTTPStatus.UNAUTHORIZED)
            return

        if self.path != "/rpc":
            self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)
            return

        content_length = int(self.headers.get("Content-Length", "0"))
        request_bytes = self.rfile.read(content_length)
        try:
            request = json.loads(request_bytes.decode("utf-8"))
        except json.JSONDecodeError:
            self._send_json(jsonrpc_error(None, ERROR_PARSE, "Invalid JSON payload"))
            return
        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}
        if self.headers.get("A2A-Version", "") != PROTOCOL_VERSION:
            self._send_json(
                jsonrpc_error(
                    request_id,
                    ERROR_VERSION_NOT_SUPPORTED,
                    "A2A-Version header must be 1.0",
                )
            )
            return

        try:
            if method == METHOD_SEND_MESSAGE:
                task, _ = self._service.send_message(params, stream=False)
                history_length = (params.get("configuration") or {}).get("historyLength")
                result = {
                    "task": trim_task_for_response(
                        task,
                        int(history_length) if history_length is not None else None,
                    )
                }
                self._send_json(jsonrpc_success(request_id, result))
                return

            if method == METHOD_SEND_STREAMING_MESSAGE:
                self._send_stream(request_id, self._service.stream_message(params))
                return

            if method == METHOD_GET_TASK:
                task_id = _required_string(params, "id")
                history_length = params.get("historyLength")
                task = trim_task_for_response(
                    self._service.get_task(task_id),
                    int(history_length) if history_length is not None else None,
                )
                self._send_json(jsonrpc_success(request_id, task))
                return

            if method == METHOD_LIST_TASKS:
                self._send_json(jsonrpc_success(request_id, self._service.list_tasks(params)))
                return

            if method == METHOD_CANCEL_TASK:
                task_id = _required_string(params, "id")
                self._send_json(jsonrpc_success(request_id, self._service.cancel_task(task_id)))
                return

            if method == METHOD_SUBSCRIBE_TO_TASK:
                task_id = _required_string(params, "id")
                self._send_stream(request_id, self._service.subscribe_task(task_id))
                return

            if method == METHOD_CREATE_PUSH_CONFIG:
                self._send_json(jsonrpc_success(request_id, self._service.create_push_config(params)))
                return

            if method == METHOD_GET_PUSH_CONFIG:
                self._send_json(jsonrpc_success(request_id, self._service.get_push_config(params)))
                return

            if method == METHOD_LIST_PUSH_CONFIGS:
                self._send_json(jsonrpc_success(request_id, self._service.list_push_configs(params)))
                return

            if method == METHOD_DELETE_PUSH_CONFIG:
                self._service.delete_push_config(params)
                self._send_json(jsonrpc_success(request_id, None))
                return

            if method == METHOD_GET_EXTENDED_AGENT_CARD:
                self._send_json(jsonrpc_success(request_id, self._service.extended_agent_card()))
                return

            self._send_json(jsonrpc_error(request_id, ERROR_METHOD_NOT_FOUND, f"Unknown method: {method}"))
        except A2AProtocolError as exc:
            self._send_json(jsonrpc_error(request_id, exc.code, exc.message))
        except KeyError as exc:
            self._send_json(jsonrpc_error(request_id, ERROR_TASK_NOT_FOUND, f"Task not found: {exc.args[0]}"))
        except ValueError as exc:
            self._send_json(jsonrpc_error(request_id, ERROR_INVALID_PARAMS, str(exc)))
        except Exception as exc:  # pragma: no cover - defensive path
            self._send_json(jsonrpc_error(request_id, ERROR_INTERNAL, str(exc)))


class ManagedA2AServer:
    """Manage a background ThreadingHTTPServer instance."""

    def __init__(self, service: A2AService) -> None:
        self.service = service
        handler = partial(_RequestHandler, service=service)
        self.httpd = ThreadingHTTPServer((service.config.host, service.config.port), handler)
        host, port = self.httpd.server_address
        if not service.config.public_base_url:
            service.config.public_base_url = f"http://{host}:{port}"
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        return self.service.config.resolved_public_base_url

    def start(self) -> None:
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self.service.close()

    def serve_forever(self) -> None:
        try:
            self.httpd.serve_forever()
        finally:
            self.service.close()


def create_server(
    config: A2APluginConfig | None = None,
    adapter: HermesExecutionAdapter | None = None,
) -> ManagedA2AServer:
    """Create a managed server instance."""
    service = A2AService(config=config, adapter=adapter)
    return ManagedA2AServer(service)
