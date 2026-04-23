"""Tool handlers for the Hermes A2A plugin scaffold."""

from __future__ import annotations

import json
import os


def get_status_payload() -> dict:
    """Build a stable status payload used by the tool and CLI."""
    base_url = os.getenv("A2A_BASE_URL", "").strip()
    api_key_present = bool(os.getenv("A2A_API_KEY", "").strip())

    return {
        "plugin": "a2a",
        "status": "ok",
        "configured": bool(base_url),
        "config": {
            "a2a_base_url": base_url or None,
            "a2a_api_key_present": api_key_present,
        },
        "message": (
            "Scaffold is installed. Replace the placeholder tool handlers with real "
            "A2A integration logic when the target API contract is ready."
        ),
    }


def tool_a2a_status(args: dict, **kwargs) -> str:
    """Return scaffold deployment status as JSON."""
    try:
        return json.dumps(get_status_payload())
    except Exception as exc:  # pragma: no cover - defensive path
        return json.dumps({"error": str(exc)})
