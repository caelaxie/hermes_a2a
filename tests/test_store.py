"""SQLite store behavior tests."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys_path = str(ROOT / "src")
if sys_path not in os.sys.path:
    os.sys.path.insert(0, sys_path)

from hermes_a2a.mapping import build_initial_task
from hermes_a2a.store import SQLiteTaskStore


class StoreTests(unittest.TestCase):
    def test_store_persists_task_events_and_push_configs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SQLiteTaskStore(str(Path(tmpdir) / "state.db"))
            task = build_initial_task("task-1", "ctx-1", "hello", direction="inbound")
            store.upsert_task(task, direction="inbound")
            seq = store.append_event("task-1", "task_status_update", {"state": "working"})
            store.set_push_config("task-1", "https://callback.test", "token")
            store.set_remote_task("task-1", "https://agent.test", "task-1")

            stored = store.get_task("task-1")
            events = store.list_events("task-1")
            push = store.get_push_config("task-1")
            remote = store.get_remote_task("task-1")
            store.close()

        self.assertEqual(stored["id"], "task-1")
        self.assertEqual(seq, 1)
        self.assertEqual(events[0]["event"], "task_status_update")
        self.assertEqual(push["pushNotificationConfig"]["url"], "https://callback.test")
        self.assertEqual(remote["agentUrl"], "https://agent.test")
