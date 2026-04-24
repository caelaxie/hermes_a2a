"""Tests for Hermes execution adapters."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys_path = str(ROOT / "src")
if sys_path not in os.sys.path:
    os.sys.path.insert(0, sys_path)

from hermes_a2a.adapter import HermesSubprocessExecutionAdapter
from hermes_a2a.config import A2APluginConfig
from hermes_a2a.server import A2AService


class HermesSubprocessAdapterTests(unittest.TestCase):
    def test_start_invokes_hermes_chat_and_emits_text_artifact(self) -> None:
        completed = mock.Mock(
            returncode=0,
            stdout="session_id: abc123\nHermes says hello\n",
            stderr="",
        )
        adapter = HermesSubprocessExecutionAdapter(
            command="hermes",
            timeout_seconds=7.5,
            extra_args=["--model", "test-model"],
            runner=mock.Mock(return_value=completed),
        )

        events = list(adapter.start("task-1", "ctx-1", "hello"))

        adapter.runner.assert_called_once_with(
            ["hermes", "chat", "--quiet", "--model", "test-model", "-q", "hello"],
            capture_output=True,
            text=True,
            timeout=7.5,
        )
        self.assertEqual(events[0].state, "working")
        self.assertEqual(events[1].text, "Hermes says hello")
        self.assertEqual(events[1].metadata["artifact_id"], "hermes-response")
        self.assertEqual(events[-1].state, "completed")

    def test_start_emits_sanitized_failed_status_when_hermes_command_fails(self) -> None:
        completed = mock.Mock(returncode=2, stdout="", stderr="boom with /secret/path")
        adapter = HermesSubprocessExecutionAdapter(
            command="hermes",
            timeout_seconds=1,
            runner=mock.Mock(return_value=completed),
        )

        events = list(adapter.start("task-1", "ctx-1", "hello"))

        self.assertEqual(events[-1].state, "failed")
        self.assertEqual(events[-1].message, "Hermes runtime failed")
        self.assertEqual(events[-1].metadata["exit_code"], "2")

    def test_start_emits_failed_status_when_hermes_command_times_out(self) -> None:
        adapter = HermesSubprocessExecutionAdapter(
            command="hermes",
            timeout_seconds=1,
            runner=mock.Mock(side_effect=subprocess.TimeoutExpired("hermes", 1)),
        )

        events = list(adapter.start("task-1", "ctx-1", "hello"))

        self.assertEqual(events[-1].state, "failed")
        self.assertIn("timed out", events[-1].message)

    def test_start_emits_sanitized_failed_status_when_hermes_command_cannot_start(self) -> None:
        adapter = HermesSubprocessExecutionAdapter(
            command="hermes",
            timeout_seconds=1,
            runner=mock.Mock(side_effect=OSError("/secret/path missing")),
        )

        events = list(adapter.start("task-1", "ctx-1", "hello"))

        self.assertEqual(events[-1].state, "failed")
        self.assertEqual(events[-1].message, "Hermes runtime command failed to start")

    def test_start_truncates_large_success_output(self) -> None:
        completed = mock.Mock(returncode=0, stdout="abcdef", stderr="")
        adapter = HermesSubprocessExecutionAdapter(
            command="hermes",
            max_output_chars=3,
            runner=mock.Mock(return_value=completed),
        )

        events = list(adapter.start("task-1", "ctx-1", "hello"))

        self.assertEqual(events[1].text, "abc\n[truncated 3 chars]")

    def test_service_uses_hermes_adapter_when_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = A2APluginConfig(
                store_path=str(Path(tmpdir) / "state.db"),
                execution_adapter="hermes",
                hermes_command="hermes",
            )
            service = A2AService(config=config)
            try:
                self.assertIsInstance(service.adapter, HermesSubprocessExecutionAdapter)
                self.assertEqual(
                    service.adapter.timeout_seconds,
                    config.default_timeout_seconds,
                )
            finally:
                service.close()

    def test_service_rejects_unknown_execution_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = A2APluginConfig(
                store_path=str(Path(tmpdir) / "state.db"),
                execution_adapter="typo",
            )

            with self.assertRaisesRegex(ValueError, "Unsupported A2A_EXECUTION_ADAPTER"):
                A2AService(config=config)


if __name__ == "__main__":
    unittest.main()
