"""Mapping helpers between Hermes execution events and A2A shapes."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Iterable
from uuid import uuid4

from .adapter import HermesEvent
from .config import A2APluginConfig
from .protocol import (
    PROTOCOL_VERSION,
    TASK_STATE_SUBMITTED,
    normalize_task_state,
)


def utc_timestamp() -> str:
    """Return an RFC3339 timestamp in UTC."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def extract_text_from_message(message: dict | None) -> str:
    """Extract text from an official A2A Message object."""
    if not isinstance(message, dict):
        raise ValueError("SendMessageRequest.message is required")
    parts = message.get("parts")
    if not isinstance(parts, list) or not parts:
        raise ValueError("Message.parts must contain at least one part")
    texts: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            raise ValueError("Message.parts entries must be objects")
        content_fields = [field for field in ("text", "raw", "url", "data") if field in part]
        if len(content_fields) != 1:
            raise ValueError("Message.parts entries must contain exactly one of text, raw, url, or data")
        field = content_fields[0]
        if field == "text":
            if not isinstance(part["text"], str):
                raise ValueError("Text parts must contain string text")
            texts.append(part["text"])
            continue
        if field == "data":
            texts.append("data: " + json.dumps(part["data"], sort_keys=True))
            continue
        if field == "url":
            if not isinstance(part["url"], str):
                raise ValueError("URL parts must contain string url")
            details = [part["url"]]
            if part.get("filename"):
                details.append(f"filename={part['filename']}")
            if part.get("mediaType"):
                details.append(f"mediaType={part['mediaType']}")
            texts.append("file: " + " ".join(details))
            continue
        if not isinstance(part["raw"], str):
            raise ValueError("Raw parts must contain base64 string raw content")
        details = [part["raw"]]
        if part.get("filename"):
            details.append(f"filename={part['filename']}")
        if part.get("mediaType"):
            details.append(f"mediaType={part['mediaType']}")
        texts.append("raw: " + " ".join(details))
        continue
    if not texts:
        raise ValueError("Message.parts must contain text, raw, url, or data content")
    return "\n".join(texts)


def build_text_part(text: str) -> dict:
    return {"text": text}


def build_data_part(data) -> dict:
    return {"data": data, "mediaType": "application/json"}


def build_file_part(uri: str) -> dict:
    return {"url": uri}


def build_message(
    role: str,
    parts: list[dict],
    message_id: str | None = None,
    task_id: str = "",
    context_id: str = "",
    metadata: dict | None = None,
) -> dict:
    message = {
        "messageId": message_id or str(uuid4()),
        "role": role,
        "parts": parts,
    }
    if context_id:
        message["contextId"] = context_id
    if task_id:
        message["taskId"] = task_id
    if metadata:
        message["metadata"] = metadata
    return message


def build_artifact_from_event(event: HermesEvent) -> dict | None:
    """Turn one Hermes artifact event into the narrow A2A artifact shape."""
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
    message: dict,
    direction: str,
    metadata: dict | None = None,
) -> dict:
    """Create the task snapshot before any runtime event has been applied."""
    timestamp = utc_timestamp()
    user_message = dict(message)
    user_message.setdefault("messageId", str(uuid4()))
    user_message.setdefault("role", "ROLE_USER")
    user_message["taskId"] = task_id
    user_message["contextId"] = context_id
    return {
        "id": task_id,
        "contextId": context_id,
        "metadata": metadata or {},
        "artifacts": [],
        "history": [user_message],
        "status": {
            "state": TASK_STATE_SUBMITTED,
            "timestamp": timestamp,
            "message": build_message(
                "ROLE_AGENT",
                [build_text_part("Task submitted")],
                task_id=task_id,
                context_id=context_id,
            ),
        },
    }


def apply_hermes_event(task: dict, event: HermesEvent) -> dict:
    """Apply one adapter event to a task snapshot and return an SSE envelope.

    This is the main protocol translation point: adapters emit Hermes-native
    status/artifact events, while server, client, and store code deal in A2A
    task snapshots and event names.
    """
    timestamp = utc_timestamp()

    if event.kind in {"status", "requires_input"}:
        state = normalize_task_state(event.state)
        message = build_message(
            "ROLE_AGENT",
            [build_text_part(event.message or state)],
            task_id=task["id"],
            context_id=task["contextId"],
        )
        task["status"] = {
            "state": state,
            "timestamp": timestamp,
            "message": message,
        }
        task.setdefault("history", []).append(message)
        return {
            "statusUpdate": {
                "taskId": task["id"],
                "contextId": task["contextId"],
                "status": task["status"],
            },
        }

    artifact = build_artifact_from_event(event)
    if artifact is not None:
        task.setdefault("artifacts", []).append(artifact)
        return {
            "artifactUpdate": {
                "taskId": task["id"],
                "contextId": task["contextId"],
                "artifact": artifact,
                "append": False,
                "lastChunk": True,
            },
        }

    return {"task": task}


def build_agent_card(config: A2APluginConfig) -> dict:
    """Build the public discovery card for this Hermes bridge."""
    skills = [
        {
            "id": skill,
            "name": skill.replace("-", " ").title(),
            "description": f"Explicitly exported Hermes skill: {skill}",
            "tags": ["hermes", "a2a"],
            "inputModes": ["text/plain", "application/json"],
            "outputModes": ["text/plain", "application/json"],
        }
        for skill in config.exported_skills
    ]
    card = {
        "name": "Hermes A2A Plugin",
        "description": "Hermes exposed as a bidirectional A2A bridge.",
        "supportedInterfaces": [
            {
                "url": config.rpc_url,
                "protocolBinding": "JSONRPC",
                "protocolVersion": PROTOCOL_VERSION,
            }
        ],
        "provider": {
            "organization": "Hermes",
            "url": config.resolved_public_base_url,
        },
        "version": config.version,
        "documentationUrl": config.card_url,
        "defaultInputModes": ["text/plain", "application/json"],
        "defaultOutputModes": ["text/plain", "application/json"],
        "capabilities": {
            "streaming": True,
            "pushNotifications": True,
        },
        "skills": skills,
    }
    if config.bearer_token:
        card["securitySchemes"] = {
            "bearerAuth": {
                "httpAuthSecurityScheme": {
                    "scheme": "Bearer",
                }
            }
        }
        card["security"] = [{"bearerAuth": []}]
    return card


def make_sse_payload(payload: dict) -> bytes:
    """Encode one A2A JSON-RPC stream response as an SSE frame."""
    import json

    return f"data: {json.dumps(payload, sort_keys=True)}\n\n".encode("utf-8")


def trim_task_for_response(task: dict, history_length: int | None = None, include_artifacts: bool = True) -> dict:
    """Return a task copy with response-level history/artifact constraints applied."""
    result = json_clone(task)
    if history_length is not None:
        if history_length < 0:
            raise ValueError("historyLength must be non-negative")
        result["history"] = result.get("history", [])[-history_length:] if history_length else []
    if not include_artifacts:
        result.pop("artifacts", None)
    return result


def json_clone(value: dict) -> dict:
    import json

    return json.loads(json.dumps(value))


def summarize_agents(agents: Iterable[dict]) -> list[dict]:
    return [
        {
            "alias": agent["alias"],
            "url": agent["url"],
            "description": agent.get("description", ""),
        }
        for agent in agents
    ]
