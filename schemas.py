"""Repo-root shim for packaged tool schemas."""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from hermes_a2a.schemas import (  # noqa: E402
    A2A_CANCEL_TASK_SCHEMA,
    A2A_DELEGATE_SCHEMA,
    A2A_GET_TASK_SCHEMA,
    A2A_LIST_AGENTS_SCHEMA,
    A2A_STATUS_SCHEMA,
)
