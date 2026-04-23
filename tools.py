"""Repo-root shim for packaged tool handlers."""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from hermes_a2a.tools import (  # noqa: E402
    get_status_payload,
    tool_a2a_cancel_task,
    tool_a2a_delegate,
    tool_a2a_get_task,
    tool_a2a_list_agents,
    tool_a2a_status,
)
