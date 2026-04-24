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
from hermes_a2a.protocol import push_config_name
from hermes_a2a.store import SQLiteTaskStore


class StoreTests(unittest.TestCase):
    def test_store_persists_task_events_and_push_configs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SQLiteTaskStore(str(Path(tmpdir) / "state.db"))
            task = build_initial_task(
                "task-1",
                "ctx-1",
                {
                    "messageId": "msg-1",
                    "role": "ROLE_USER",
                    "parts": [{"text": "hello"}],
                },
                direction="inbound",
            )
            store.upsert_task(task, direction="inbound")
            seq = store.append_event(
                "task-1",
                {
                    "statusUpdate": {
                        "taskId": "task-1",
                        "contextId": "ctx-1",
                        "status": {"state": "TASK_STATE_WORKING"},
                    }
                },
            )
            config_name = push_config_name("task-1", "cfg-1")
            store.set_push_config(
                "task-1",
                "cfg-1",
                {
                    "pushNotificationConfig": {
                        "url": "https://callback.test",
                        "token": "token",
                    },
                },
            )
            store.set_remote_task("task-1", "https://agent.test", "task-1")

            stored = store.get_task("task-1")
            events = store.list_events("task-1")
            push = store.get_push_config(config_name)
            remote = store.get_remote_task("task-1")
            store.close()

        self.assertEqual(stored["id"], "task-1")
        self.assertEqual(seq, 1)
        self.assertEqual(events[0]["statusUpdate"]["status"]["state"], "TASK_STATE_WORKING")
        self.assertEqual(push["taskId"], "task-1")
        self.assertEqual(push["pushNotificationConfig"]["url"], "https://callback.test")
        self.assertEqual(remote["agentUrl"], "https://agent.test")
