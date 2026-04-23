"""Repo-root shim for the packaged Hermes A2A plugin."""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from hermes_a2a import register  # noqa: E402
