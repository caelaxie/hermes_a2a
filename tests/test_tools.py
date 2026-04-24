"""Tool behavior tests for the Hermes A2A scaffold."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import get_status_payload, tool_a2a_status


class ToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_status_payload_defaults_to_unconfigured(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"A2A_STORE_PATH": str(Path(self.tmpdir.name) / "state.db")},
            clear=True,
        ):
            payload = get_status_payload()

        self.assertEqual(payload["plugin"], "a2a")
        self.assertEqual(payload["status"], "ok")
        self.assertFalse(payload["config"]["bearer_token_present"])
        self.assertEqual(payload["config"]["default_timeout_seconds"], 120.0)
        self.assertEqual(payload["config"]["remote_agents"], [])
        self.assertEqual(payload["hermes_cli"]["fallback_command"], "hermes-a2a")
        self.assertEqual(payload["hermes_cli"]["top_level_command"], "hermes a2a")
        self.assertEqual(
            payload["hermes_cli"]["top_level_cli_discovery"]["state"],
            "unreleased-upstream",
        )
        self.assertEqual(
            payload["hermes_cli"]["top_level_cli_discovery"]["minimum_commit"],
            "308bbf6a5480223ec484b342422fe883e8ac81e4",
        )

    def test_status_payload_reflects_environment(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "A2A_PUBLIC_BASE_URL": "https://example.test/base",
                "A2A_BEARER_TOKEN": "secret",
                "A2A_EXPORTED_SKILLS": "delegate,inspect",
                "A2A_STORE_PATH": str(Path(self.tmpdir.name) / "state.db"),
            },
            clear=True,
        ):
            payload = get_status_payload()

        self.assertEqual(payload["config"]["public_base_url"], "https://example.test/base")
        self.assertTrue(payload["config"]["bearer_token_present"])
        self.assertEqual(payload["config"]["exported_skills"], ["delegate", "inspect"])

    def test_tool_returns_json_string(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"A2A_STORE_PATH": str(Path(self.tmpdir.name) / "state.db")},
            clear=True,
        ):
            result = tool_a2a_status({})
        payload = json.loads(result)

        self.assertEqual(payload["plugin"], "a2a")
        self.assertEqual(payload["status"], "ok")


if __name__ == "__main__":
    unittest.main()
