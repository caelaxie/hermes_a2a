"""Configuration parsing tests."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys_path = str(ROOT / "src")
if sys_path not in os.sys.path:
    os.sys.path.insert(0, sys_path)

from hermes_a2a.config import load_config


class ConfigTests(unittest.TestCase):
    def test_load_config_parses_remote_agents_and_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.dict(
                os.environ,
                {
                    "A2A_STORE_PATH": str(Path(tmpdir) / "state.db"),
                    "A2A_EXPORTED_SKILLS": "delegate, inspect",
                    "A2A_REMOTE_AGENTS_JSON": (
                        '{"demo":{"url":"https://example.test","description":"Demo","headers":{"Authorization":"Bearer x"}}}'
                    ),
                },
                clear=True,
            ):
                config = load_config()

        self.assertEqual(config.exported_skills, ["delegate", "inspect"])
        self.assertEqual(len(config.remote_agents), 1)
        self.assertEqual(config.remote_agents[0].alias, "demo")
        self.assertEqual(config.remote_agents[0].url, "https://example.test")
        self.assertEqual(
            config.remote_agents[0].headers,
            {"Authorization": "Bearer x"},
        )
