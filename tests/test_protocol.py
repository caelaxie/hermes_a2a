"""Tests for A2A protocol constants and helpers."""

from __future__ import annotations

import os
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys_path = str(ROOT / "src")
if sys_path not in os.sys.path:
    os.sys.path.insert(0, sys_path)

from hermes_a2a.protocol import TASK_STATE_AUTH_REQUIRED, normalize_task_state


class ProtocolTests(unittest.TestCase):
    def test_auth_required_state_normalization_uses_a2a_1_0_constant(self) -> None:
        self.assertEqual(TASK_STATE_AUTH_REQUIRED, "TASK_STATE_AUTH_REQUIRED")
        self.assertEqual(
            normalize_task_state("auth-required"),
            "TASK_STATE_AUTH_REQUIRED",
        )


if __name__ == "__main__":
    unittest.main()
