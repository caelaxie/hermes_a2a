"""Hermes execution adapter contracts."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterable


@dataclass(slots=True)
class HermesEvent:
    """Structured event emitted by a Hermes execution adapter."""

    kind: str
    state: str
    message: str = ""
    text: str = ""
    data: dict | None = None
    file_uri: str = ""
    metadata: dict[str, str] = field(default_factory=dict)


class HermesExecutionAdapter(ABC):
    """Stable interface between Hermes internals and A2A protocol surfaces."""

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


class DemoHermesExecutionAdapter(HermesExecutionAdapter):
    """Fallback adapter used until a real Hermes runtime is wired in."""

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
                state="cancelled",
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
