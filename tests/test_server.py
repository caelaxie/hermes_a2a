"""Integration tests for inbound and outbound A2A flows."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys_path = str(ROOT / "src")
if sys_path not in os.sys.path:
    os.sys.path.insert(0, sys_path)

from hermes_a2a.config import A2APluginConfig
from hermes_a2a.server import create_server
from hermes_a2a.tools import tool_a2a_delegate, tool_a2a_get_task


class ServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        config = A2APluginConfig(
            host="127.0.0.1",
            port=0,
            store_path=str(Path(self.tmpdir.name) / "server.db"),
            exported_skills=["delegate"],
            execution_adapter="demo",
        )
        self.server = create_server(config=config)
        self.server.start()

    def tearDown(self) -> None:
        self.server.stop()
        self.tmpdir.cleanup()

    def _rpc(self, method: str, params: dict, accept: str = "application/json"):
        payload = json.dumps(
            {"jsonrpc": "2.0", "id": "test", "method": method, "params": params}
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.server.base_url}/rpc",
            data=payload,
            headers={"Content-Type": "application/json", "Accept": accept},
        )
        return urllib.request.urlopen(request, timeout=5)

    def test_agent_card_and_core_rpc_round_trip(self) -> None:
        with urllib.request.urlopen(
            f"{self.server.base_url}/.well-known/agent-card.json", timeout=5
        ) as response:
            card = json.loads(response.read().decode("utf-8"))

        with self._rpc(
            "message/send",
            {"message": {"role": "user", "parts": [{"type": "text", "text": "hello"}]}},
        ) as response:
            task_payload = json.loads(response.read().decode("utf-8"))

        task_id = task_payload["result"]["id"]

        with self._rpc("tasks/get", {"id": task_id}) as response:
            fetched = json.loads(response.read().decode("utf-8"))

        self.assertEqual(card["skills"][0]["id"], "delegate")
        self.assertEqual(task_payload["result"]["status"]["state"], "completed")
        self.assertEqual(fetched["result"]["id"], task_id)

    def test_stream_resubscribe_and_push_config_crud(self) -> None:
        with self._rpc(
            "message/stream",
            {"message": {"role": "user", "parts": [{"type": "text", "text": "hello"}]}},
            accept="text/event-stream",
        ) as response:
            stream_body = response.read().decode("utf-8")

        self.assertIn("event: task_status_update", stream_body)
        self.assertIn("event: task", stream_body)
        task_data_line = [
            line for line in stream_body.splitlines() if line.startswith("data: ") and '"id"' in line
        ][-1]
        task = json.loads(task_data_line.split(":", 1)[1].strip())
        task_id = task["id"]

        with self._rpc(
            "tasks/pushNotificationConfig/set",
            {"id": task_id, "pushNotificationConfig": {"url": "https://callback.test", "token": "tok"}},
        ) as response:
            set_payload = json.loads(response.read().decode("utf-8"))

        with self._rpc("tasks/pushNotificationConfig/get", {"id": task_id}) as response:
            get_payload = json.loads(response.read().decode("utf-8"))

        with self._rpc("tasks/pushNotificationConfig/list", {}) as response:
            list_payload = json.loads(response.read().decode("utf-8"))

        with self._rpc(
            "tasks/resubscribe",
            {"id": task_id, "afterSeq": 0},
            accept="text/event-stream",
        ) as response:
            replay = response.read().decode("utf-8")

        with self._rpc("tasks/pushNotificationConfig/delete", {"id": task_id}) as response:
            delete_payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(set_payload["result"]["taskId"], task_id)
        self.assertEqual(get_payload["result"]["pushNotificationConfig"]["url"], "https://callback.test")
        self.assertEqual(len(list_payload["result"]), 1)
        self.assertIn("event: task_artifact_update", replay)
        self.assertIsNone(delete_payload["result"])

    def test_outbound_delegate_round_trips_against_local_server(self) -> None:
        env = {
            "A2A_STORE_PATH": str(Path(self.tmpdir.name) / "client.db"),
            "A2A_REMOTE_AGENTS_JSON": json.dumps(
                {"local": {"url": self.server.base_url, "description": "loopback"}}
            ),
        }
        with mock.patch.dict(os.environ, env, clear=True):
            delegated = json.loads(
                tool_a2a_delegate({"target": "local", "message": "hello from tool", "mode": "wait"})
            )
            refreshed = json.loads(tool_a2a_get_task({"task_id": delegated["task"]["id"]}))

        self.assertEqual(delegated["task"]["status"]["state"], "completed")
        self.assertEqual(refreshed["id"], delegated["task"]["id"])
