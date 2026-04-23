"""Registration tests for the Hermes A2A scaffold."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_root_plugin():
    spec = importlib.util.spec_from_file_location(
        "a2a_plugin",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["a2a_plugin"] = module
    spec.loader.exec_module(module)
    return module


class FakeContext:
    def __init__(self) -> None:
        self.tools = []
        self.cli_commands = []

    def register_tool(self, **kwargs) -> None:
        self.tools.append(kwargs)

    def register_cli_command(self, **kwargs) -> None:
        self.cli_commands.append(kwargs)


class RegisterTests(unittest.TestCase):
    def test_register_wires_tool_and_cli_command(self) -> None:
        plugin = load_root_plugin()
        ctx = FakeContext()

        plugin.register(ctx)

        self.assertEqual(len(ctx.tools), 1)
        self.assertEqual(ctx.tools[0]["name"], "a2a_status")
        self.assertEqual(ctx.tools[0]["toolset"], "a2a")

        self.assertEqual(len(ctx.cli_commands), 1)
        self.assertEqual(ctx.cli_commands[0]["name"], "a2a")


if __name__ == "__main__":
    unittest.main()
