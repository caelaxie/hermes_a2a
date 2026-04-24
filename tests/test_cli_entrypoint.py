"""Regression tests for the standalone hermes-a2a CLI entrypoint."""

from __future__ import annotations

import io
import json
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
        self.assertIn("only works on Hermes versions", payload["message"])

    def test_usage_prefers_standalone_console_script(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            cli.handle_cli(Namespace(a2a_command=None))

        self.assertEqual(
            stdout.getvalue().strip(),
            "Usage: hermes-a2a {status|card|serve|agents list|task get|task cancel}",
        )

    def test_register_cli_exposes_same_parser_tree_for_hermes_core(self) -> None:
        parser = ArgumentParser(prog="hermes a2a")

        cli.register_cli(parser)
        args = parser.parse_args(["status"])

        self.assertEqual(args.a2a_command, "status")
        self.assertIs(args.func, cli.handle_cli)


if __name__ == "__main__":
    unittest.main()
