"""Hermes execution adapter contracts."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterable


@dataclass(slots=True)
class HermesEvent:
    """Hermes-native event before it is translated into A2A task shapes.

    Adapters should stay ignorant of AgentCard, Task, Part, and SSE schemas.
    `mapping.py` is the single boundary that turns these events into protocol
    payloads.
    """

    kind: str
    state: str
    message: str = ""
    text: str = ""
    data: dict | None = None
    file_uri: str = ""
    metadata: dict[str, str] = field(default_factory=dict)


class HermesExecutionAdapter(ABC):
    """Stable interface between Hermes internals and A2A protocol surfaces.

    The service layer drives task lifecycle decisions; adapter implementations
    only report runtime progress, artifacts, and terminal metadata.
    """

    @abstractmethod
    def start(
        self,
        task_id: str,
        context_id: str,
        message: str,
        metadata: dict | None = None,
    ) -> Iterable[HermesEvent]:
        """Start a new Hermes-backed task."""

    @abstractmethod
    def continue_task(
        self,
        task_id: str,
        context_id: str,
        message: str,
        metadata: dict | None = None,
    ) -> Iterable[HermesEvent]:
        """Continue an existing Hermes-backed task."""

    @abstractmethod
    def stream(
        self,
        task_id: str,
        context_id: str,
        message: str,
        metadata: dict | None = None,
    ) -> Iterable[HermesEvent]:
        """Return incremental events for a task."""

    @abstractmethod
    def cancel(
        self,
        task_id: str,
        context_id: str,
        metadata: dict | None = None,
    ) -> Iterable[HermesEvent]:
        """Cancel an in-flight task."""

    @abstractmethod
    def finalize_task(
        self,
        task_id: str,
        context_id: str,
        metadata: dict | None = None,
    ) -> dict:
        """Return adapter-level terminal metadata for the task."""


class HermesSubprocessExecutionAdapter(HermesExecutionAdapter):
    """Adapter that delegates inbound A2A messages to the Hermes CLI runtime."""

    def __init__(
        self,
        command: str = "hermes",
        timeout_seconds: float = 120.0,
        extra_args: list[str] | None = None,
        runner=None,
        max_output_chars: int = 20_000,
    ) -> None:
        self.command = command or "hermes"
        self.timeout_seconds = timeout_seconds
        self.extra_args = list(extra_args or [])
        self.runner = runner
        self.max_output_chars = max(1, int(max_output_chars))
        self._process_lock = threading.Lock()
        self._active_processes: dict[str, subprocess.Popen | None] = {}
        self._cancel_requested: set[str] = set()

    def _truncate(self, text: str) -> str:
        if len(text) <= self.max_output_chars:
            return text
        omitted = len(text) - self.max_output_chars
        return f"{text[: self.max_output_chars]}\n[truncated {omitted} chars]"

    def _clean_stdout(self, stdout: str) -> str:
        # Hermes CLI sessions may print bookkeeping lines that are useful for
        # local shells but should not become user-visible A2A artifacts.
        lines = [line for line in stdout.splitlines() if not line.startswith("session_id:")]
        return self._truncate("\n".join(lines).strip())

    def _extract_session_id(self, stdout: str) -> str:
        for line in stdout.splitlines():
            label, separator, value = line.partition(":")
            if not separator:
                continue
            if label.strip() in {"session_id", "Session"}:
                session_id = value.strip()
                if session_id:
                    return session_id
        return ""

    def _session_id_from_metadata(self, metadata: dict | None) -> str:
        if not isinstance(metadata, dict):
            return ""
        return str(metadata.get("hermes_session_id") or "").strip()

    def _reserve_process_slot(self, task_id: str) -> None:
        with self._process_lock:
            self._cancel_requested.discard(task_id)
            self._active_processes[task_id] = None

    def _register_process(self, task_id: str, process: subprocess.Popen) -> bool:
        with self._process_lock:
            if task_id in self._cancel_requested:
                return False
            self._active_processes[task_id] = process
            return True

    def _clear_process_slot(self, task_id: str) -> None:
        with self._process_lock:
            self._active_processes.pop(task_id, None)
            self._cancel_requested.discard(task_id)

    def _is_cancel_requested(self, task_id: str) -> bool:
        with self._process_lock:
            return task_id in self._cancel_requested

    def _request_cancel(self, task_id: str) -> subprocess.Popen | None:
        with self._process_lock:
            if task_id not in self._active_processes:
                return None
            self._cancel_requested.add(task_id)
            return self._active_processes[task_id]

    def _terminate_process(self, process: subprocess.Popen) -> None:
        try:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
        except ProcessLookupError:
            return
        except OSError:
            try:
                process.terminate()
            except OSError:
                return

    def _kill_process(self, process: subprocess.Popen) -> None:
        try:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            return
        except OSError:
            try:
                process.kill()
            except OSError:
                return

    def _stop_process(self, process: subprocess.Popen) -> None:
        self._terminate_process(process)
        try:
            process.communicate(timeout=1)
        except subprocess.TimeoutExpired:
            self._kill_process(process)
            process.communicate()

    def _run_with_process_tracking(
        self,
        task_id: str,
        command: list[str],
    ) -> subprocess.CompletedProcess | None:
        popen_kwargs = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
        }
        if os.name != "nt":
            popen_kwargs["start_new_session"] = True
        process = subprocess.Popen(command, **popen_kwargs)
        if not self._register_process(task_id, process):
            self._stop_process(process)
            return None

        deadline = time.monotonic() + self.timeout_seconds
        while True:
            if self._is_cancel_requested(task_id):
                self._stop_process(process)
                return None

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._stop_process(process)
                raise subprocess.TimeoutExpired(command, self.timeout_seconds)

            try:
                stdout, stderr = process.communicate(timeout=min(0.1, remaining))
            except subprocess.TimeoutExpired:
                continue
            if self._is_cancel_requested(task_id):
                return None
            return subprocess.CompletedProcess(
                command,
                process.returncode,
                stdout,
                stderr,
            )

    def _run(
        self,
        task_id: str,
        context_id: str,
        message: str,
        resume_session_id: str = "",
    ) -> Iterable[HermesEvent]:
        command = [self.command, "chat", "--quiet", *self.extra_args]
        if resume_session_id:
            command.extend(["--resume", resume_session_id])
        command.extend(["-q", message])
        self._reserve_process_slot(task_id)
        try:
            yield HermesEvent(
                kind="status",
                state="working",
                message="Hermes runtime execution started",
                metadata={"task_id": task_id, "context_id": context_id},
            )
            if self._is_cancel_requested(task_id):
                return
            if self.runner is not None:
                completed = self.runner(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                )
            else:
                completed = self._run_with_process_tracking(task_id, command)
                if completed is None:
                    return
            if self._is_cancel_requested(task_id):
                return

            raw_stdout = completed.stdout or ""
            hermes_session_id = self._extract_session_id(raw_stdout)
            stdout = self._clean_stdout(raw_stdout)
            if completed.returncode != 0:
                yield HermesEvent(
                    kind="status",
                    state="failed",
                    message="Hermes runtime failed",
                    metadata={
                        "task_id": task_id,
                        "context_id": context_id,
                        "exit_code": str(completed.returncode),
                    },
                )
                return

            if stdout:
                yield HermesEvent(
                    kind="artifact",
                    state="working",
                    message="Hermes runtime response emitted",
                    text=stdout,
                    metadata={"artifact_id": "hermes-response"},
                )
            if self._is_cancel_requested(task_id):
                return
            yield HermesEvent(
                kind="status",
                state="completed",
                message="Hermes runtime execution completed",
                metadata={
                    "task_id": task_id,
                    "context_id": context_id,
                    **(
                        {"hermes_session_id": hermes_session_id}
                        if hermes_session_id
                        else {}
                    ),
                },
            )
        except subprocess.TimeoutExpired:
            yield HermesEvent(
                kind="status",
                state="failed",
                message=f"Hermes runtime timed out after {self.timeout_seconds:g}s",
                metadata={"task_id": task_id, "context_id": context_id},
            )
            return
        except OSError:
            yield HermesEvent(
                kind="status",
                state="failed",
                message="Hermes runtime command failed to start",
                metadata={"task_id": task_id, "context_id": context_id},
            )
            return
        finally:
            self._clear_process_slot(task_id)

    def start(
        self,
        task_id: str,
        context_id: str,
        message: str,
        metadata: dict | None = None,
    ) -> Iterable[HermesEvent]:
        return self._run(
            task_id,
            context_id,
            message,
            resume_session_id=self._session_id_from_metadata(metadata),
        )

    def continue_task(
        self,
        task_id: str,
        context_id: str,
        message: str,
        metadata: dict | None = None,
    ) -> Iterable[HermesEvent]:
        return self._run(
            task_id,
            context_id,
            message,
            resume_session_id=self._session_id_from_metadata(metadata),
        )

    def stream(
        self,
        task_id: str,
        context_id: str,
        message: str,
        metadata: dict | None = None,
    ) -> Iterable[HermesEvent]:
        return self._run(
            task_id,
            context_id,
            message,
            resume_session_id=self._session_id_from_metadata(metadata),
        )

    def cancel(
        self,
        task_id: str,
        context_id: str,
        metadata: dict | None = None,
    ) -> Iterable[HermesEvent]:
        del metadata
        process = self._request_cancel(task_id)
        if process is not None:
            self._terminate_process(process)
        return [
            HermesEvent(
                kind="status",
                state="canceled",
                message="Hermes runtime subprocess cancellation requested",
                metadata={"task_id": task_id, "context_id": context_id},
            )
        ]

    def finalize_task(
        self,
        task_id: str,
        context_id: str,
        metadata: dict | None = None,
    ) -> dict:
        return {
            "taskId": task_id,
            "contextId": context_id,
            "adapter": "hermes-subprocess",
            "command": self.command,
            "metadata": metadata or {},
        }


class DemoHermesExecutionAdapter(HermesExecutionAdapter):
    """Deterministic adapter for tests and local protocol debugging.

    Keep this adapter predictable: server and tool tests use message text to
    force status, data, file, and input-required branches without invoking a
    model or subprocess.
    """

    def _run(
        self,
        task_id: str,
        context_id: str,
        message: str,
        metadata: dict | None,
    ) -> Iterable[HermesEvent]:
        metadata = metadata or {}
        lowered = message.lower()

        yield HermesEvent(
            kind="status",
            state="working",
            message="Hermes execution started",
            metadata={"task_id": task_id, "context_id": context_id},
        )

        if "fail" in lowered:
            yield HermesEvent(
                kind="status",
                state="failed",
                message="Demo Hermes adapter was asked to fail this task",
            )
            return

        if "need input" in lowered or "requires input" in lowered:
            yield HermesEvent(
                kind="requires_input",
                state="input-required",
                message="Demo Hermes adapter requires more input to continue",
            )
            return

        if message.startswith("data:"):
            raw_data = message.split(":", 1)[1].strip()
            try:
                parsed = json.loads(raw_data)
            except json.JSONDecodeError:
                parsed = {"raw": raw_data}
            yield HermesEvent(
                kind="artifact",
                state="working",
                message="Structured data artifact emitted",
                data=parsed,
                metadata={"artifact_id": "data-result"},
            )
        elif message.startswith("file:"):
            file_uri = message.split(":", 1)[1].strip()
            yield HermesEvent(
                kind="artifact",
                state="working",
                message="File artifact emitted",
                file_uri=file_uri,
                metadata={"artifact_id": "file-result"},
            )
        else:
            prefix = metadata.get("mode", "standard")
            yield HermesEvent(
                kind="artifact",
                state="working",
                message="Text artifact emitted",
                text=f"[{prefix}] Hermes handled: {message}",
                metadata={"artifact_id": "text-result"},
            )

        yield HermesEvent(
            kind="status",
            state="completed",
            message="Hermes execution completed",
        )

    def start(
        self,
        task_id: str,
        context_id: str,
        message: str,
        metadata: dict | None = None,
    ) -> Iterable[HermesEvent]:
        return self._run(task_id, context_id, message, metadata)

    def continue_task(
        self,
        task_id: str,
        context_id: str,
        message: str,
        metadata: dict | None = None,
    ) -> Iterable[HermesEvent]:
        return self._run(task_id, context_id, message, metadata)

    def stream(
        self,
        task_id: str,
        context_id: str,
        message: str,
        metadata: dict | None = None,
    ) -> Iterable[HermesEvent]:
        return self._run(task_id, context_id, message, metadata)

    def cancel(
        self,
        task_id: str,
        context_id: str,
        metadata: dict | None = None,
    ) -> Iterable[HermesEvent]:
        del metadata
        return [
            HermesEvent(
                kind="status",
                state="canceled",
                message="Hermes execution cancelled",
                metadata={"task_id": task_id, "context_id": context_id},
            )
        ]

    def finalize_task(
        self,
        task_id: str,
        context_id: str,
        metadata: dict | None = None,
    ) -> dict:
        return {
            "taskId": task_id,
            "contextId": context_id,
            "adapter": "demo",
            "metadata": metadata or {},
        }
