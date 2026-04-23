"""Regression tests for the repo-root shim layout."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import schemas as root_schemas  # noqa: E402
import tools as root_tools  # noqa: E402

sys.path.insert(0, str(ROOT / "src"))
from hermes_a2a import tools as pkg_tools  # noqa: E402
from hermes_a2a import schemas as pkg_schemas  # noqa: E402


class ShimTests(unittest.TestCase):
    def test_repo_root_exports_packaged_functions(self) -> None:
        self.assertIs(root_tools.tool_a2a_delegate, pkg_tools.tool_a2a_delegate)
        self.assertIs(root_tools.tool_a2a_status, pkg_tools.tool_a2a_status)
        self.assertIs(root_schemas.A2A_DELEGATE_SCHEMA, pkg_schemas.A2A_DELEGATE_SCHEMA)
