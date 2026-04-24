"""Minimal outbound A2A JSON-RPC client."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Iterator
from uuid import uuid4

from .config import A2APluginConfig
from .mapping import build_text_part
from .protocol import (
    METHOD_CANCEL_TASK,
    METHOD_GET_TASK,
    METHOD_SEND_MESSAGE,
    METHOD_SEND_STREAMING_MESSAGE,
    PROTOCOL_VERSION,
    RPC_PATH,
)


class A2AClientError(RuntimeError):
    """Raised when a remote A2A request fails."""


def resolve_agent_target(
    target: str,
    config: A2APluginConfig,
) -> tuple[str, dict[str, str], str]:
    """Resolve a direct URL or configured alias into a concrete agent target.

    Direct URLs intentionally receive no preset headers; aliases are the place
    for persisted per-agent auth metadata from `A2A_REMOTE_AGENTS_JSON`.
    """
    stripped = target.strip()
    if stripped.startswith("http://") or stripped.startswith("https://"):
        return stripped.rstrip("/"), {}, "direct"

    for agent in config.remote_agents:
        if agent.alias == stripped:
            return agent.url.rstrip("/"), dict(agent.headers), agent.alias

    raise A2AClientError(f"Unknown remote agent alias: {target}")


class A2AClient:
    """Small JSON-RPC client for remote A2A agents.

    The Hermes tool layer uses this client for outbound delegation. Keep it
    protocol-shaped and free of local task-store decisions.
    """

    def __init__(
        self,
        base_url: str,
        headers: dict[str, str] | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = headers or {}
        self.timeout = timeout
        self._jsonrpc_url: str | None = None

    def _resolve_url(self, path_or_url: str) -> str:
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            return path_or_url
        return urllib.parse.urljoin(f"{self.base_url}/", path_or_url.lstrip("/"))

    def _fallback_jsonrpc_url(self) -> str:
        return self._resolve_url(RPC_PATH)

    def _select_jsonrpc_url(self, card: dict) -> str:
        for interface in card.get("supportedInterfaces") or []:
            if not isinstance(interface, dict):
                continue
            if (
                interface.get("protocolBinding") == "JSONRPC"
                and interface.get("protocolVersion") == PROTOCOL_VERSION
                and interface.get("url")
            ):
                return self._resolve_url(str(interface["url"]))
        return self._fallback_jsonrpc_url()

    def _jsonrpc_endpoint(self) -> str:
        if self._jsonrpc_url is None:
            self.get_agent_card()
        return self._jsonrpc_url or self._fallback_jsonrpc_url()

    def _request(
        self,
        path_or_url: str,
        body: dict | None = None,
        accept: str = "application/json",
    ):
        url = self._resolve_url(path_or_url)
        payload = None
        headers = {"Accept": accept, "A2A-Version": PROTOCOL_VERSION, **self.headers}
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=payload, headers=headers)
        try:
            return urllib.request.urlopen(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:  # pragma: no cover - exercised through callers
            body_bytes = exc.read()
            raise A2AClientError(
                f"Remote request failed with {exc.code}: {body_bytes.decode('utf-8', errors='replace')}"
            ) from exc
        except urllib.error.URLError as exc:  # pragma: no cover - exercised through callers
            raise A2AClientError(str(exc.reason)) from exc

    def get_agent_card(self) -> dict:
        with self._request("/.well-known/agent-card.json") as response:
            card = json.loads(response.read().decode("utf-8"))
        self._jsonrpc_url = self._select_jsonrpc_url(card)
        return card

    def send_message(
        self,
        message: str,
        task_id: str = "",
        context_id: str = "",
    ) -> dict:
        request = {
            "jsonrpc": "2.0",
            "id": "delegate",
            "method": METHOD_SEND_MESSAGE,
            "params": {
                "message": {
                    "messageId": str(uuid4()),
                    "role": "ROLE_USER",
                    "parts": [build_text_part(message)],
                    **({"taskId": task_id} if task_id else {}),
                    **({"contextId": context_id} if context_id else {}),
                },
            },
        }
        with self._request(self._jsonrpc_endpoint(), request) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if "error" in payload:
            raise A2AClientError(payload["error"]["message"])
        result = payload.get("result") or {}
        if "task" not in result:
            raise A2AClientError("Remote SendMessage response did not contain result.task")
        return result["task"]

    def stream_message(
        self,
        message: str,
        task_id: str = "",
        context_id: str = "",
    ) -> Iterator[dict]:
        request = {
            "jsonrpc": "2.0",
            "id": "stream",
            "method": METHOD_SEND_STREAMING_MESSAGE,
            "params": {
                "message": {
                    "messageId": str(uuid4()),
                    "role": "ROLE_USER",
                    "parts": [build_text_part(message)],
                    **({"taskId": task_id} if task_id else {}),
                    **({"contextId": context_id} if context_id else {}),
                },
            },
        }
        with self._request(
            self._jsonrpc_endpoint(),
            request,
            accept="text/event-stream",
        ) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8").strip()
                if not line:
                    continue
                if line.startswith("data:"):
                    payload = json.loads(line.split(":", 1)[1].strip())
                    if "error" in payload:
                        raise A2AClientError(payload["error"]["message"])
                    yield payload["result"]

    def get_task(self, task_id: str) -> dict:
        request = {
            "jsonrpc": "2.0",
            "id": "task-get",
            "method": METHOD_GET_TASK,
            "params": {"id": task_id},
        }
        with self._request(self._jsonrpc_endpoint(), request) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if "error" in payload:
            raise A2AClientError(payload["error"]["message"])
        return payload["result"]

    def cancel_task(self, task_id: str) -> dict:
        request = {
            "jsonrpc": "2.0",
            "id": "task-cancel",
            "method": METHOD_CANCEL_TASK,
            "params": {"id": task_id},
        }
        with self._request(self._jsonrpc_endpoint(), request) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if "error" in payload:
            raise A2AClientError(payload["error"]["message"])
        return payload["result"]
