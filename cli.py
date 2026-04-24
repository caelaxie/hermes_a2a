"""Repo-root CLI shim for Hermes directory-plugin compatibility."""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from hermes_a2a.cli import register_cli  # noqa: E402
