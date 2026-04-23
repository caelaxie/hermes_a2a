"""Hermes tool handlers backed by the packaged A2A implementation."""

from __future__ import annotations

import json
from typing import Any

from .client import A2AClient, A2AClientError, resolve_agent_target
from .config import load_config
from .server import A2AService


def _service() -> A2AService:
    return A2AService(config=load_config())


def get_status_payload() -> dict:
    """Return effective plugin status information."""
    service = _service()
    try:
        return service.status_payload()
    finally:
        service.close()


def tool_a2a_status(args: dict[str, Any], **kwargs) -> str:
    del args, kwargs
    try:
        return json.dumps(get_status_payload())
    except Exception as exc:  # pragma: no cover - defensive path
        return json.dumps({"error": str(exc)})


def tool_a2a_list_agents(args: dict[str, Any], **kwargs) -> str:
    del args, kwargs
    try:
        config = load_config()
        payload = {
            "agents": [
                {
                    "alias": agent.alias,
                    "url": agent.url,
                    "description": agent.description,
                    "headers_present": bool(agent.headers),
                }
                for agent in config.remote_agents
            ]
        }
        return json.dumps(payload)
    except Exception as exc:  # pragma: no cover - defensive path
        return json.dumps({"error": str(exc)})


def _refresh_remote_task(service: A2AService, task_id: str) -> dict:
    task = service.get_task(task_id)
    remote = service.store.get_remote_task(task_id)
    if not remote:
        return task

    client = A2AClient(
        remote["agentUrl"],
        timeout=service.config.default_timeout_seconds,
    )
    latest = client.get_task(remote["remoteTaskId"])
    latest.setdefault("metadata", {}).update(
        {
            "remoteAgentUrl": remote["agentUrl"],
            "remoteTaskId": remote["remoteTaskId"],
        }
    )
    service.store.upsert_task(latest, direction="outbound")
    return latest


def tool_a2a_get_task(args: dict[str, Any], **kwargs) -> str:
    del kwargs
    service = _service()
    try:
        payload = _refresh_remote_task(service, str(args.get("task_id", "")).strip())
        return json.dumps(payload)
    except Exception as exc:
        return json.dumps({"error": str(exc)})
    finally:
        service.close()


def tool_a2a_cancel_task(args: dict[str, Any], **kwargs) -> str:
    del kwargs
    service = _service()
    try:
        task_id = str(args.get("task_id", "")).strip()
        remote = service.store.get_remote_task(task_id)
        if remote:
            client = A2AClient(
                remote["agentUrl"],
                timeout=service.config.default_timeout_seconds,
            )
            payload = client.cancel_task(remote["remoteTaskId"])
            payload.setdefault("metadata", {}).update(
                {
                    "remoteAgentUrl": remote["agentUrl"],
                    "remoteTaskId": remote["remoteTaskId"],
                }
            )
            service.store.upsert_task(payload, direction="outbound")
            return json.dumps(payload)

        return json.dumps(service.cancel_task(task_id))
    except Exception as exc:
        return json.dumps({"error": str(exc)})
    finally:
        service.close()


def tool_a2a_delegate(args: dict[str, Any], **kwargs) -> str:
    del kwargs
    config = load_config()
    service = A2AService(config=config)
    try:
        target = str(args.get("target", "")).strip()
        message = str(args.get("message", "")).strip()
        mode = str(args.get("mode", "wait")).strip() or "wait"
        task_id = str(args.get("task_id", "")).strip()
        context_id = str(args.get("context_id", "")).strip()
        agent_url, headers, resolved_target = resolve_agent_target(target, config)
        client = A2AClient(agent_url, headers=headers, timeout=config.default_timeout_seconds)
        card = client.get_agent_card()

        if mode == "stream":
            events = list(client.stream_message(message, task_id=task_id, context_id=context_id))
            final_task = None
            for event in events:
                if event["event"] == "task":
                    final_task = event["data"]
            if final_task is None:
                raise A2AClientError("Remote stream did not yield a final task snapshot")
            final_task.setdefault("metadata", {}).update(
                {"remoteAgentUrl": agent_url, "resolvedTarget": resolved_target}
            )
            service.store.upsert_task(final_task, direction="outbound")
            service.store.set_remote_task(final_task["id"], agent_url, final_task["id"])
            return json.dumps({"card": card, "events": events, "task": final_task})

        task = client.send_message(message, task_id=task_id, context_id=context_id)
        task.setdefault("metadata", {}).update(
            {"remoteAgentUrl": agent_url, "resolvedTarget": resolved_target}
        )
        service.store.upsert_task(task, direction="outbound")
        service.store.set_remote_task(task["id"], agent_url, task["id"])
        if mode == "poll":
            return json.dumps({"card": card, "task": task, "mode": "poll"})
        return json.dumps({"card": card, "task": task, "mode": "wait"})
    except Exception as exc:
        return json.dumps({"error": str(exc)})
    finally:
        service.close()
