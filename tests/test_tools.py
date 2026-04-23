"""Tool behavior tests for the Hermes A2A scaffold."""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import get_status_payload, tool_a2a_status


class ToolTests(unittest.TestCase):
    def test_status_payload_defaults_to_unconfigured(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            payload = get_status_payload()

        self.assertEqual(payload["plugin"], "a2a")
        self.assertFalse(payload["configured"])
        self.assertIsNone(payload["config"]["a2a_base_url"])
        self.assertFalse(payload["config"]["a2a_api_key_present"])

    def test_status_payload_reflects_environment(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"A2A_BASE_URL": "https://example.test", "A2A_API_KEY": "secret"},
            clear=True,
        ):
            payload = get_status_payload()

        self.assertTrue(payload["configured"])
        self.assertEqual(payload["config"]["a2a_base_url"], "https://example.test")
        self.assertTrue(payload["config"]["a2a_api_key_present"])

    def test_tool_returns_json_string(self) -> None:
        result = tool_a2a_status({})
        payload = json.loads(result)

        self.assertEqual(payload["plugin"], "a2a")
        self.assertEqual(payload["status"], "ok")


if __name__ == "__main__":
    unittest.main()
