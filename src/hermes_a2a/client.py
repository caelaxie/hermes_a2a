"""Minimal outbound A2A JSON-RPC client."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Iterator

from .config import A2APluginConfig, RemoteAgentPreset


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

    def _request(
        self,
        path: str,
        body: dict | None = None,
        accept: str = "application/json",
    ):
        url = f"{self.base_url}{path}"
        payload = None
        headers = {"Accept": accept, **self.headers}
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
            return json.loads(response.read().decode("utf-8"))

    def send_message(
        self,
        message: str,
        task_id: str = "",
        context_id: str = "",
    ) -> dict:
        request = {
            "jsonrpc": "2.0",
            "id": "delegate",
            "method": "message/send",
            "params": {
                "taskId": task_id or None,
                "contextId": context_id or None,
                "message": {"role": "user", "parts": [{"type": "text", "text": message}]},
            },
        }
        with self._request("/rpc", request) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if "error" in payload:
            raise A2AClientError(payload["error"]["message"])
        return payload["result"]

    def stream_message(
        self,
        message: str,
        task_id: str = "",
        context_id: str = "",
    ) -> Iterator[dict]:
        request = {
            "jsonrpc": "2.0",
            "id": "stream",
            "method": "message/stream",
            "params": {
                "taskId": task_id or None,
                "contextId": context_id or None,
                "message": {"role": "user", "parts": [{"type": "text", "text": message}]},
            },
        }
        with self._request("/rpc", request, accept="text/event-stream") as response:
            event_name = "message"
            for raw_line in response:
                line = raw_line.decode("utf-8").strip()
                if not line:
                    continue
                if line.startswith("event:"):
                    event_name = line.split(":", 1)[1].strip()
                    continue
                if line.startswith("data:"):
                    # The local server emits one JSON object per SSE `data:`
                    # line, so this parser deliberately stays line-oriented.
                    data = json.loads(line.split(":", 1)[1].strip())
                    yield {"event": event_name, "data": data}

    def get_task(self, task_id: str) -> dict:
        request = {
            "jsonrpc": "2.0",
            "id": "task-get",
            "method": "tasks/get",
            "params": {"id": task_id},
        }
        with self._request("/rpc", request) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if "error" in payload:
            raise A2AClientError(payload["error"]["message"])
        return payload["result"]

    def cancel_task(self, task_id: str) -> dict:
        request = {
            "jsonrpc": "2.0",
            "id": "task-cancel",
            "method": "tasks/cancel",
            "params": {"id": task_id},
        }
        with self._request("/rpc", request) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if "error" in payload:
            raise A2AClientError(payload["error"]["message"])
        return payload["result"]
