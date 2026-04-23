"""Regression tests for the standalone hermes-a2a CLI entrypoint."""

from __future__ import annotations

import io
import json
import sys
import unittest
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


if __name__ == "__main__":
    unittest.main()
