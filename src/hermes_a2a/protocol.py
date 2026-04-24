"""A2A 1.0 protocol constants and small wire-shape helpers."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime, timezone


PROTOCOL_VERSION = "1.0"
RPC_PATH = "/rpc"
A2A_CONTENT_TYPE = "application/a2a+json"

METHOD_SEND_MESSAGE = "SendMessage"
METHOD_SEND_STREAMING_MESSAGE = "SendStreamingMessage"
METHOD_GET_TASK = "GetTask"
METHOD_LIST_TASKS = "ListTasks"
METHOD_CANCEL_TASK = "CancelTask"
METHOD_SUBSCRIBE_TO_TASK = "SubscribeToTask"
METHOD_CREATE_PUSH_CONFIG = "CreateTaskPushNotificationConfig"
METHOD_GET_PUSH_CONFIG = "GetTaskPushNotificationConfig"
METHOD_LIST_PUSH_CONFIGS = "ListTaskPushNotificationConfigs"
METHOD_DELETE_PUSH_CONFIG = "DeleteTaskPushNotificationConfig"
METHOD_GET_EXTENDED_AGENT_CARD = "GetExtendedAgentCard"

TASK_STATE_SUBMITTED = "TASK_STATE_SUBMITTED"
TASK_STATE_WORKING = "TASK_STATE_WORKING"
TASK_STATE_COMPLETED = "TASK_STATE_COMPLETED"
TASK_STATE_FAILED = "TASK_STATE_FAILED"
TASK_STATE_CANCELED = "TASK_STATE_CANCELED"
TASK_STATE_INPUT_REQUIRED = "TASK_STATE_INPUT_REQUIRED"
TASK_STATE_REJECTED = "TASK_STATE_REJECTED"
TASK_STATE_AUTH_REQUIRED = "TASK_STATE_AUTH_REQUIRED"

TERMINAL_TASK_STATES = {
    TASK_STATE_COMPLETED,
    TASK_STATE_FAILED,
    TASK_STATE_CANCELED,
    TASK_STATE_REJECTED,
}

ERROR_PARSE = -32700
ERROR_METHOD_NOT_FOUND = -32601
ERROR_INVALID_PARAMS = -32602
ERROR_INTERNAL = -32603
ERROR_TASK_NOT_FOUND = -32001
ERROR_UNSUPPORTED_OPERATION = -32004
ERROR_VERSION_NOT_SUPPORTED = -32009

STATE_MAP = {
    "submitted": TASK_STATE_SUBMITTED,
    "working": TASK_STATE_WORKING,
    "completed": TASK_STATE_COMPLETED,
    "failed": TASK_STATE_FAILED,
    "canceled": TASK_STATE_CANCELED,
    "input-required": TASK_STATE_INPUT_REQUIRED,
    "input_required": TASK_STATE_INPUT_REQUIRED,
    "requires-input": TASK_STATE_INPUT_REQUIRED,
    "rejected": TASK_STATE_REJECTED,
    "auth-required": TASK_STATE_AUTH_REQUIRED,
}


@dataclass(slots=True)
class A2AProtocolError(Exception):
    """Exception carrying an A2A JSON-RPC error code."""

    code: int
    message: str


def normalize_task_state(state: str) -> str:
    if state.startswith("TASK_STATE_"):
        return state
    return STATE_MAP.get(state.strip().lower(), TASK_STATE_WORKING)


def jsonrpc_success(request_id, result: dict | list | None) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def jsonrpc_error(request_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def push_config_name(task_id: str, config_id: str) -> str:
    return f"tasks/{task_id}/pushNotificationConfigs/{config_id}"


def encode_page_token(offset: int) -> str:
    if offset <= 0:
        return ""
    raw = json.dumps({"offset": offset}, sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_page_token(token: str) -> int:
    if not token:
        return 0
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii"))
        decoded = json.loads(raw.decode("utf-8"))
        return max(0, int(decoded.get("offset", 0)))
    except Exception as exc:
        raise ValueError("Invalid pageToken") from exc


def encode_task_page_token(task: dict) -> str:
    raw = json.dumps(
        {
            "statusTimestamp": str(task.get("status", {}).get("timestamp", "")),
            "id": str(task.get("id", "")),
        },
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_task_page_token(token: str) -> dict:
    if not token:
        return {}
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii"))
        decoded = json.loads(raw.decode("utf-8"))
        return {
            "statusTimestamp": str(decoded.get("statusTimestamp", "")),
            "id": str(decoded.get("id", "")),
        }
    except Exception as exc:
        raise ValueError("Invalid pageToken") from exc


def parse_rfc3339_timestamp(value: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError("Timestamp is required")
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("Invalid RFC3339 timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
