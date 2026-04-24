"""Tests for Hermes execution adapters."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys_path = str(ROOT / "src")
if sys_path not in os.sys.path:
    os.sys.path.insert(0, sys_path)

from hermes_a2a.adapter import (
    HermesEvent,
    HermesExecutionAdapter,
    HermesSubprocessExecutionAdapter,
)
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
        self.assertEqual(events[-1].metadata["hermes_session_id"], "abc123")

    def test_continue_task_resumes_stored_hermes_session(self) -> None:
        completed = mock.Mock(returncode=0, stdout="continued\n", stderr="")
        adapter = HermesSubprocessExecutionAdapter(
            command="hermes",
            runner=mock.Mock(return_value=completed),
        )

        list(
            adapter.continue_task(
                "task-1",
                "ctx-1",
                "follow up",
                metadata={"hermes_session_id": "20260424_101500_abc123"},
            )
        )

        adapter.runner.assert_called_once_with(
            [
                "hermes",
                "chat",
                "--quiet",
                "--resume",
                "20260424_101500_abc123",
                "-q",
                "follow up",
            ],
            capture_output=True,
            text=True,
            timeout=120.0,
        )

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

    def test_cancel_terminates_active_hermes_subprocess(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_hermes = Path(tmpdir) / "fake-hermes"
            fake_hermes.write_text(
                "\n".join(
                    [
                        "#!/usr/bin/env python3",
                        "import time",
                        "time.sleep(30)",
                    ]
                )
            )
            fake_hermes.chmod(0o755)
            adapter = HermesSubprocessExecutionAdapter(
                command=str(fake_hermes),
                timeout_seconds=1,
            )
            events = []

            def run_adapter() -> None:
                for event in adapter.start("task-cancel", "ctx-1", "hello"):
                    events.append(event)

            thread = threading.Thread(target=run_adapter)
            thread.start()
            try:
                for _ in range(100):
                    if events and events[0].state == "working":
                        break
                    thread.join(0.01)
                self.assertEqual(events[0].state, "working")

                cancel_events = list(adapter.cancel("task-cancel", "ctx-1"))

                self.assertEqual(cancel_events[-1].state, "canceled")
                thread.join(0.5)
                canceled_promptly = not thread.is_alive()
            finally:
                thread.join(2)

            self.assertTrue(canceled_promptly)
            self.assertNotIn("completed", [event.state for event in events])

    def test_stream_emits_stdout_chunk_before_subprocess_exits(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            started_marker = tmp_path / "chunk-written"
            done_marker = tmp_path / "process-done"
            fake_hermes = tmp_path / "fake-hermes"
            fake_hermes.write_text(
                "\n".join(
                    [
                        "#!/usr/bin/env python3",
                        "import pathlib",
                        "import sys",
                        "import time",
                        f"started = pathlib.Path({str(started_marker)!r})",
                        f"done = pathlib.Path({str(done_marker)!r})",
                        "sys.stdout.write('first chunk\\n')",
                        "sys.stdout.flush()",
                        "started.touch()",
                        "time.sleep(0.5)",
                        "sys.stdout.write('second chunk\\n')",
                        "sys.stdout.flush()",
                        "done.touch()",
                    ]
                )
            )
            fake_hermes.chmod(0o755)
            adapter = HermesSubprocessExecutionAdapter(
                command=str(fake_hermes),
                timeout_seconds=2,
            )

            events = adapter.stream("task-stream", "ctx-1", "hello")
            first_event = next(events)
            self.assertEqual(first_event.state, "working")
            first_artifact = next(events)

            self.assertEqual(first_artifact.kind, "artifact")
            self.assertEqual(first_artifact.text, "first chunk\n")
            self.assertEqual(first_artifact.metadata["append"], "false")
            self.assertEqual(first_artifact.metadata["last_chunk"], "false")
            self.assertTrue(started_marker.exists())
            self.assertFalse(done_marker.exists())

            remaining_events = list(events)

            self.assertTrue(done_marker.exists())
            remaining_artifacts = [
                event for event in remaining_events if event.kind == "artifact"
            ]
            self.assertTrue(remaining_artifacts)
            self.assertIn("completed", [event.state for event in remaining_events])

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

    def test_service_persists_and_reuses_hermes_session_for_task_continuation(self) -> None:
        class RecordingAdapter(HermesExecutionAdapter):
            def __init__(self) -> None:
                self.continue_metadata = None

            def start(self, task_id, context_id, message, metadata=None):
                del message, metadata
                return [
                    HermesEvent(
                        kind="status",
                        state="completed",
                        message="done",
                        metadata={
                            "task_id": task_id,
                            "context_id": context_id,
                            "hermes_session_id": "20260424_101500_abc123",
                        },
                    )
                ]

            def continue_task(self, task_id, context_id, message, metadata=None):
                del task_id, context_id, message
                self.continue_metadata = metadata
                return [
                    HermesEvent(kind="status", state="completed", message="continued")
                ]

            def stream(self, task_id, context_id, message, metadata=None):
                return self.continue_task(task_id, context_id, message, metadata)

            def cancel(self, task_id, context_id, metadata=None):
                del task_id, context_id, metadata
                return []

            def finalize_task(self, task_id, context_id, metadata=None):
                del task_id, context_id
                return {"adapter": "recording", "metadata": metadata or {}}

        with tempfile.TemporaryDirectory() as tmpdir:
            config = A2APluginConfig(store_path=str(Path(tmpdir) / "state.db"))
            adapter = RecordingAdapter()
            service = A2AService(config=config, adapter=adapter)
            try:
                task, _ = service.send_message(
                    {
                        "message": {
                            "messageId": "msg-1",
                            "role": "ROLE_USER",
                            "parts": [{"text": "hello"}],
                        }
                    }
                )
                service.send_message(
                    {
                        "message": {
                            "messageId": "msg-2",
                            "role": "ROLE_USER",
                            "taskId": task["id"],
                            "contextId": task["contextId"],
                            "parts": [{"text": "follow up"}],
                        }
                    }
                )
            finally:
                service.close()

        self.assertEqual(
            adapter.continue_metadata["hermes_session_id"],
            "20260424_101500_abc123",
        )


if __name__ == "__main__":
    unittest.main()
