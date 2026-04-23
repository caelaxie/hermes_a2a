"""Inbound A2A server and orchestration helpers."""

from __future__ import annotations

import json
import threading
import urllib.request
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Iterable
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from .adapter import DemoHermesExecutionAdapter, HermesExecutionAdapter
from .config import A2APluginConfig, load_config
from .mapping import (
    apply_hermes_event,
    build_agent_card,
    build_initial_task,
    extract_text_from_message,
    make_sse_payload,
)
from .store import SQLiteTaskStore


def _jsonrpc_error(request_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


class A2AService:
    """Application service shared by Hermes tools, CLI, and HTTP handlers."""

    def __init__(
        self,
        config: A2APluginConfig | None = None,
        store: SQLiteTaskStore | None = None,
        adapter: HermesExecutionAdapter | None = None,
    ) -> None:
        self.config = config or load_config()
        self.store = store or SQLiteTaskStore(self.config.resolved_store_path)
        self.adapter = adapter or DemoHermesExecutionAdapter()

    def status_payload(self) -> dict:
        payload = self.config.status_dict()
        payload["status"] = "ok"
        payload["server"] = {
            "card": self.agent_card(),
            "local_tasks": len(self.store.list_tasks()),
        }
        payload["message"] = (
            "Hermes A2A bridge is configured. Start `hermes-a2a serve` "
            "(or `hermes a2a serve` on Hermes versions with standalone plugin "
            "CLI discovery) to expose the inbound JSON-RPC + SSE surface."
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
        existing = self.store.get_task(task_id)
        if existing is None:
            return self.adapter.stream(task_id, context_id, message_text, metadata) if stream else self.adapter.start(task_id, context_id, message_text, metadata)
        return self.adapter.stream(task_id, context_id, message_text, metadata) if stream else self.adapter.continue_task(task_id, context_id, message_text, metadata)

    def _notify_push(self, task_id: str, event_name: str, data: dict) -> None:
        config = self.store.get_push_config(task_id)
        if not config:
            return
        push_config = config["pushNotificationConfig"]
        payload = json.dumps({"event": event_name, "data": data}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        token = push_config.get("token", "")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(
            push_config["url"],
            data=payload,
            headers=headers,
        )
        try:
            urllib.request.urlopen(request, timeout=self.config.default_timeout_seconds).read()
        except Exception:
            # Push delivery is best-effort. The event remains durable in SQLite.
            return

    def send_message(
        self,
        params: dict,
        stream: bool = False,
    ) -> tuple[dict, list[dict]]:
        task_id = str(params.get("taskId") or uuid4())
        context_id = str(params.get("contextId") or task_id)
        message_text = extract_text_from_message(params.get("message"))
        task = self.store.get_task(task_id)
        if task is None:
            task = build_initial_task(task_id, context_id, message_text, direction="inbound")
        events: list[dict] = []

        for adapter_event in self._iter_adapter_events(
            task_id,
            context_id,
            message_text,
            stream=stream,
            metadata={"mode": "stream" if stream else "send"},
        ):
            envelope = apply_hermes_event(task, adapter_event)
            seq = self.store.append_event(task_id, envelope["event"], envelope["data"])
            envelope["data"]["sequence"] = seq
            events.append(envelope)
            self._notify_push(task_id, envelope["event"], envelope["data"])

        task.setdefault("metadata", {}).update(self.adapter.finalize_task(task_id, context_id))
        self.store.upsert_task(task, direction="inbound")
        return task, events

    def get_task(self, task_id: str) -> dict:
        task = self.store.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        return task

    def cancel_task(self, task_id: str) -> dict:
        task = self.get_task(task_id)
        context_id = task.get("contextId", task_id)
        for adapter_event in self.adapter.cancel(task_id, context_id):
            envelope = apply_hermes_event(task, adapter_event)
            seq = self.store.append_event(task_id, envelope["event"], envelope["data"])
            envelope["data"]["sequence"] = seq
            self._notify_push(task_id, envelope["event"], envelope["data"])
        self.store.upsert_task(task, direction=task.get("direction", "inbound"))
        return task

    def resubscribe(self, task_id: str, after_seq: int = 0) -> list[dict]:
        return self.store.list_events(task_id, after_seq=after_seq)

    def set_push_config(self, params: dict) -> dict:
        task_id = str(params.get("id") or params.get("taskId") or "").strip()
        if not task_id:
            raise ValueError("Missing task id")
        config = params.get("pushNotificationConfig") or {}
        url = str(config.get("url", "")).strip()
        token = str(config.get("token", "")).strip()
        if not url:
            raise ValueError("Missing push notification url")
        return self.store.set_push_config(task_id, url, token)

    def get_push_config(self, params: dict) -> dict | None:
        task_id = str(params.get("id") or params.get("taskId") or "").strip()
        if not task_id:
            raise ValueError("Missing task id")
        return self.store.get_push_config(task_id)

    def list_push_configs(self) -> list[dict]:
        return self.store.list_push_configs()

    def delete_push_config(self, params: dict) -> None:
        task_id = str(params.get("id") or params.get("taskId") or "").strip()
        if not task_id:
            raise ValueError("Missing task id")
        self.store.delete_push_config(task_id)

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

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urlparse(self.path)
        if parsed.path == "/.well-known/agent-card.json":
            self._send_json(self._service.agent_card())
            return

        if not self._require_auth():
            self._send_json({"error": "Unauthorized"}, status=HTTPStatus.UNAUTHORIZED)
            return

        if parsed.path.startswith("/tasks/") and parsed.path.endswith("/events"):
            task_id = parsed.path.split("/")[2]
            after_seq = int(parse_qs(parsed.query).get("after_seq", ["0"])[0])
            events = self._service.resubscribe(task_id, after_seq=after_seq)
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            for event in events:
                self.wfile.write(make_sse_payload(event["event"], event["data"]))
                self.wfile.flush()
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
        request = json.loads(request_bytes.decode("utf-8"))
        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}

        try:
            if method == "message/send":
                task, _ = self._service.send_message(params, stream=False)
                self._send_json({"jsonrpc": "2.0", "id": request_id, "result": task})
                return

            if method == "message/stream":
                task, events = self._service.send_message(params, stream=True)
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                for event in events:
                    self.wfile.write(make_sse_payload(event["event"], event["data"]))
                    self.wfile.flush()
                self.wfile.write(make_sse_payload("task", task))
                self.wfile.flush()
                return

            if method == "tasks/get":
                task_id = str(params.get("id") or "")
                self._send_json(
                    {"jsonrpc": "2.0", "id": request_id, "result": self._service.get_task(task_id)}
                )
                return

            if method == "tasks/cancel":
                task_id = str(params.get("id") or "")
                self._send_json(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": self._service.cancel_task(task_id),
                    }
                )
                return

            if method == "tasks/resubscribe":
                task_id = str(params.get("id") or "")
                after_seq = int(params.get("afterSeq", 0))
                events = self._service.resubscribe(task_id, after_seq=after_seq)
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                for event in events:
                    self.wfile.write(make_sse_payload(event["event"], event["data"]))
                    self.wfile.flush()
                return

            if method == "tasks/pushNotificationConfig/set":
                result = self._service.set_push_config(params)
                self._send_json({"jsonrpc": "2.0", "id": request_id, "result": result})
                return

            if method == "tasks/pushNotificationConfig/get":
                result = self._service.get_push_config(params)
                self._send_json({"jsonrpc": "2.0", "id": request_id, "result": result})
                return

            if method == "tasks/pushNotificationConfig/list":
                result = self._service.list_push_configs()
                self._send_json({"jsonrpc": "2.0", "id": request_id, "result": result})
                return

            if method == "tasks/pushNotificationConfig/delete":
                self._service.delete_push_config(params)
                self._send_json({"jsonrpc": "2.0", "id": request_id, "result": None})
                return

            self._send_json(_jsonrpc_error(request_id, -32601, f"Unknown method: {method}"))
        except KeyError as exc:
            self._send_json(_jsonrpc_error(request_id, -32004, f"Task not found: {exc.args[0]}"))
        except ValueError as exc:
            self._send_json(_jsonrpc_error(request_id, -32602, str(exc)))
        except Exception as exc:  # pragma: no cover - defensive path
            self._send_json(_jsonrpc_error(request_id, -32000, str(exc)))


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
