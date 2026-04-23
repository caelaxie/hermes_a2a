"""Tests for the repo-root Hermes plugin loading path."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class FakeContext:
    def __init__(self) -> None:
        self.tools = []
        self.cli_commands = []

    def register_tool(self, **kwargs) -> None:
        self.tools.append(kwargs)

    def register_cli_command(self, **kwargs) -> None:
        self.cli_commands.append(kwargs)


class RootPluginTests(unittest.TestCase):
    def test_repo_root_registers_when_loaded_as_directory_plugin(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "repo_root_plugin",
            ROOT / "__init__.py",
            submodule_search_locations=[str(ROOT)],
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)

        module = importlib.util.module_from_spec(spec)
        sys.modules["repo_root_plugin"] = module
        spec.loader.exec_module(module)

        ctx = FakeContext()
        module.register(ctx)

        self.assertEqual(len(ctx.tools), 5)
        self.assertEqual(
            {tool["name"] for tool in ctx.tools},
            {
                "a2a_status",
                "a2a_list_agents",
                "a2a_get_task",
                "a2a_cancel_task",
                "a2a_delegate",
            },
        )
        self.assertEqual(len(ctx.cli_commands), 1)
        self.assertEqual(ctx.cli_commands[0]["name"], "a2a")


if __name__ == "__main__":
    unittest.main()
