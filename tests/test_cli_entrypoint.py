"""Regression tests for the standalone hermes-a2a CLI entrypoint."""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import unittest
from argparse import ArgumentParser
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hermes_a2a import cli  # noqa: E402


class CliEntrypointTests(unittest.TestCase):
    def test_main_dispatches_status_command(self) -> None:
        with patch.dict("os.environ", {"A2A_STORE_PATH": ":memory:"}, clear=False):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = cli.main(["status"])

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["plugin"], "a2a")
        self.assertEqual(payload["status"], "ok")
        self.assertIn("`hermes-a2a serve`", payload["message"])
        self.assertIn("no released Hermes tag includes", payload["message"])
        self.assertEqual(payload["hermes_cli"]["fallback_command"], "hermes-a2a")
        self.assertEqual(payload["hermes_cli"]["top_level_command"], "hermes a2a")
        self.assertIsNone(
            payload["hermes_cli"]["top_level_cli_discovery"]["minimum_release"]
        )
        self.assertEqual(
            payload["hermes_cli"]["top_level_cli_discovery"]["minimum_commit"],
            "308bbf6a5480223ec484b342422fe883e8ac81e4",
        )

    def test_usage_prefers_standalone_console_script(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            cli.handle_cli(Namespace(a2a_command=None))

        self.assertEqual(
            stdout.getvalue().strip(),
            "Usage: hermes-a2a {status|card|serve|agents list|task get|task cancel}",
        )

    def test_readme_documents_top_level_cli_compatibility_gate(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("uv run hermes-a2a status", readme)
        self.assertIn("hermes a2a status", readme)
        self.assertIn("No released Hermes tag", readme)
        self.assertIn("v2026.4.23", readme)
        self.assertIn("308bbf6a5480223ec484b342422fe883e8ac81e4", readme)
        self.assertIn("HERMES_A2A_VERIFY_TOP_LEVEL_CLI=1", readme)

    def test_register_cli_exposes_same_parser_tree_for_hermes_core(self) -> None:
        parser = ArgumentParser(prog="hermes a2a")

        cli.register_cli(parser)
        args = parser.parse_args(["status"])

        self.assertEqual(args.a2a_command, "status")
        self.assertIs(args.func, cli.handle_cli)

    @unittest.skipUnless(
        os.environ.get("HERMES_A2A_VERIFY_TOP_LEVEL_CLI") == "1",
        "set HERMES_A2A_VERIFY_TOP_LEVEL_CLI=1 to verify an installed Hermes core",
    )
    def test_installed_hermes_exposes_top_level_cli_when_supported(self) -> None:
        hermes = shutil.which("hermes")
        if hermes is None:
            self.skipTest("hermes executable is not on PATH")

        env = os.environ.copy()
        env.setdefault("A2A_STORE_PATH", ":memory:")
        result = subprocess.run(
            [hermes, "a2a", "status"],
            check=True,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=20,
        )
        payload = json.loads(result.stdout)

        self.assertEqual(payload["plugin"], "a2a")
        self.assertEqual(payload["status"], "ok")


if __name__ == "__main__":
    unittest.main()
