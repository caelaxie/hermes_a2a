"""Mapping helpers between Hermes execution events and A2A shapes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from .adapter import HermesEvent
from .config import A2APluginConfig


def utc_timestamp() -> str:
    """Return an RFC3339 timestamp in UTC."""
    return datetime.now(timezone.utc).isoformat()


def extract_text_from_message(message: dict | str | None) -> str:
    """Extract a user-facing text payload from a loose A2A message shape."""
    if message is None:
        return ""
    if isinstance(message, str):
        return message
    if not isinstance(message, dict):
        return str(message)

    if "text" in message and isinstance(message["text"], str):
        return message["text"]

    parts = message.get("parts")
    if isinstance(parts, list):
        texts: list[str] = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text" and isinstance(part.get("text"), str):
                texts.append(part["text"])
                continue
            root = part.get("root")
            if isinstance(root, dict):
                if root.get("kind") == "text" and isinstance(root.get("text"), str):
                    texts.append(root["text"])
                elif root.get("type") == "text" and isinstance(root.get("text"), str):
                    texts.append(root["text"])
        if texts:
            return "\n".join(texts)

    metadata = message.get("metadata")
    if isinstance(metadata, dict) and isinstance(metadata.get("text"), str):
        return metadata["text"]

    return ""


def build_text_part(text: str) -> dict:
    return {"type": "text", "text": text}


def build_data_part(data: dict) -> dict:
    return {"type": "data", "data": data}


def build_file_part(uri: str) -> dict:
    return {"type": "file", "uri": uri}


def build_artifact_from_event(event: HermesEvent) -> dict | None:
    artifact_id = event.metadata.get("artifact_id", "artifact")
    if event.text:
        parts = [build_text_part(event.text)]
    elif event.data is not None:
        parts = [build_data_part(event.data)]
    elif event.file_uri:
        parts = [build_file_part(event.file_uri)]
    else:
        return None
    return {
        "artifactId": artifact_id,
        "name": artifact_id,
        "parts": parts,
    }


def build_initial_task(
    task_id: str,
    context_id: str,
    message_text: str,
    direction: str,
    metadata: dict | None = None,
) -> dict:
    timestamp = utc_timestamp()
    return {
        "kind": "task",
        "id": task_id,
        "contextId": context_id,
        "direction": direction,
        "historyLength": 1,
        "createdAt": timestamp,
        "updatedAt": timestamp,
        "messages": [
            {
                "role": "user",
                "parts": [build_text_part(message_text)],
                "timestamp": timestamp,
            }
        ],
        "artifacts": [],
        "metadata": metadata or {},
        "status": {
            "state": "submitted",
            "timestamp": timestamp,
            "message": {
                "role": "agent",
                "parts": [build_text_part("Task submitted")],
                "timestamp": timestamp,
            },
        },
    }


def apply_hermes_event(task: dict, event: HermesEvent) -> dict:
    """Apply one adapter event to a task snapshot and return an event envelope."""
    timestamp = utc_timestamp()
    task["updatedAt"] = timestamp
    task["historyLength"] = int(task.get("historyLength", 0)) + 1

    if event.kind in {"status", "requires_input"}:
        task["status"] = {
            "state": event.state,
            "timestamp": timestamp,
            "message": {
                "role": "agent",
                "parts": [build_text_part(event.message or event.state)],
                "timestamp": timestamp,
            },
        }
        task.setdefault("messages", []).append(task["status"]["message"])
        return {
            "event": "task_status_update",
            "data": {
                "taskId": task["id"],
                "contextId": task["contextId"],
                "state": event.state,
                "message": event.message or event.state,
                "timestamp": timestamp,
            },
        }

    artifact = build_artifact_from_event(event)
    if artifact is not None:
        task.setdefault("artifacts", []).append(artifact)
        return {
            "event": "task_artifact_update",
            "data": {
                "taskId": task["id"],
                "contextId": task["contextId"],
                "artifact": artifact,
                "timestamp": timestamp,
            },
        }

    return {
        "event": "task_update",
        "data": {
            "taskId": task["id"],
            "contextId": task["contextId"],
            "timestamp": timestamp,
        },
    }


def build_agent_card(config: A2APluginConfig) -> dict:
    """Build the public agent card published by the plugin."""
    skills = [
        {
            "id": skill,
            "name": skill.replace("-", " ").title(),
            "description": f"Explicitly exported Hermes skill: {skill}",
            "tags": ["hermes", "a2a"],
        }
        for skill in config.exported_skills
    ]
    card = {
        "name": "Hermes A2A Plugin",
        "description": "Hermes exposed as a bidirectional A2A bridge.",
        "url": config.rpc_url,
        "provider": {"organization": "Hermes"},
        "version": config.version,
        "documentationUrl": config.card_url,
        "preferredTransport": "JSONRPC",
        "protocolVersion": "1.0",
        "defaultInputModes": ["text/plain", "application/json"],
        "defaultOutputModes": ["text/plain", "application/json"],
        "capabilities": {
            "streaming": True,
            "pushNotifications": True,
        },
        "skills": skills,
    }
    if config.bearer_token:
        card["security"] = [{"type": "bearer", "scheme": "Bearer"}]
    return card


def make_sse_payload(event_name: str, data: dict) -> bytes:
    """Encode an SSE event payload."""
    import json

    return (
        f"event: {event_name}\n"
        f"data: {json.dumps(data, sort_keys=True)}\n\n"
    ).encode("utf-8")


def summarize_agents(agents: Iterable[dict]) -> list[dict]:
    return [
        {
            "alias": agent["alias"],
            "url": agent["url"],
            "description": agent.get("description", ""),
        }
        for agent in agents
    ]
