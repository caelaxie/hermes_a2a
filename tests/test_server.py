"""Integration tests for inbound and outbound A2A 1.0 flows."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys_path = str(ROOT / "src")
if sys_path not in os.sys.path:
    os.sys.path.insert(0, sys_path)

from hermes_a2a.config import A2APluginConfig
from hermes_a2a.protocol import (
    ERROR_INVALID_PARAMS,
    ERROR_METHOD_NOT_FOUND,
    ERROR_UNSUPPORTED_OPERATION,
    ERROR_VERSION_NOT_SUPPORTED,
    PROTOCOL_VERSION,
)
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

    def _message(self, text: str, task_id: str = "", context_id: str = "") -> dict:
        message = {
            "messageId": str(uuid4()),
            "role": "ROLE_USER",
            "parts": [{"text": text}],
        }
        if task_id:
            message["taskId"] = task_id
        if context_id:
            message["contextId"] = context_id
        return message

    def _rpc(
        self,
        method: str,
        params: dict,
        accept: str = "application/json",
        version: str | None = PROTOCOL_VERSION,
    ):
        payload = json.dumps(
            {"jsonrpc": "2.0", "id": "test", "method": method, "params": params}
        ).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": accept}
        if version is not None:
            headers["A2A-Version"] = version
        request = urllib.request.Request(
            f"{self.server.base_url}/rpc",
            data=payload,
            headers=headers,
        )
        return urllib.request.urlopen(request, timeout=5)

    def _read_rpc(self, method: str, params: dict, version: str | None = PROTOCOL_VERSION) -> dict:
        with self._rpc(method, params, version=version) as response:
            return json.loads(response.read().decode("utf-8"))

    def _read_stream(self, method: str, params: dict) -> list[dict]:
        with self._rpc(method, params, accept="text/event-stream") as response:
            body = response.read().decode("utf-8")
        self.assertNotIn("event:", body)
        return [
            json.loads(line.split(":", 1)[1].strip())
            for line in body.splitlines()
            if line.startswith("data:")
        ]

    def _assert_no_legacy_task_fields(self, value) -> None:
        if isinstance(value, dict):
            for forbidden in ("kind", "messages", "historyLength", "createdAt", "updatedAt", "type"):
                self.assertNotIn(forbidden, value)
            for child in value.values():
                self._assert_no_legacy_task_fields(child)
        elif isinstance(value, list):
            for child in value:
                self._assert_no_legacy_task_fields(child)

    def _assert_one_of(self, value: dict, fields: tuple[str, ...]) -> str:
        present = [field for field in fields if field in value]
        self.assertEqual(len(present), 1)
        return present[0]

    def _assert_stream_response(self, value: dict) -> str:
        field = self._assert_one_of(value, ("task", "message", "statusUpdate", "artifactUpdate"))
        if field == "statusUpdate":
            self.assertNotIn("final", value["statusUpdate"])
        return field

    def _assert_part(self, part: dict) -> str:
        field = self._assert_one_of(part, ("text", "raw", "url", "data"))
        self.assertNotIn("file", part)
        self.assertNotIn("kind", part)
        self.assertNotIn("type", part)
        return field

    def _assert_message(self, message: dict) -> None:
        self.assertIn(message["role"], {"ROLE_USER", "ROLE_AGENT"})
        self.assertTrue(message["messageId"])
        for part in message["parts"]:
            self._assert_part(part)

    def _assert_task(self, task: dict) -> None:
        self._assert_no_legacy_task_fields(task)
        self.assertIn("history", task)
        self.assertNotIn("messages", task)
        self.assertIn(task["status"]["state"], {
            "TASK_STATE_SUBMITTED",
            "TASK_STATE_WORKING",
            "TASK_STATE_COMPLETED",
            "TASK_STATE_FAILED",
            "TASK_STATE_CANCELED",
            "TASK_STATE_INPUT_REQUIRED",
            "TASK_STATE_REJECTED",
            "TASK_STATE_AUTH_REQUIRED",
        })
        for message in task.get("history", []):
            self._assert_message(message)
        for artifact in task.get("artifacts", []):
            for part in artifact["parts"]:
                self._assert_part(part)

    def _start_callback_server(self):
        callback = {}

        class PushHandler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler API
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                callback["content_type"] = self.headers.get("Content-Type")
                callback["authorization"] = self.headers.get("Authorization")
                callback["payload"] = json.loads(body.decode("utf-8"))
                self.send_response(200)
                self.end_headers()

            def log_message(self, format, *args):  # noqa: A003 - inherited name
                del format, args

        callback_server = HTTPServer(("127.0.0.1", 0), PushHandler)
        callback_thread = threading.Thread(target=callback_server.serve_forever, daemon=True)
        callback_thread.start()
        callback_url = f"http://127.0.0.1:{callback_server.server_address[1]}/push"
        return callback, callback_url, callback_server, callback_thread

    def test_agent_card_and_core_rpc_round_trip(self) -> None:
        with urllib.request.urlopen(
            f"{self.server.base_url}/.well-known/agent-card.json", timeout=5
        ) as response:
            card = json.loads(response.read().decode("utf-8"))

        self.assertEqual(
            card["supportedInterfaces"],
            [
                {
                    "url": f"{self.server.base_url}/rpc",
                    "protocolBinding": "JSONRPC",
                    "protocolVersion": "1.0",
                }
            ],
        )
        self.assertNotIn("url", card)
        self.assertNotIn("protocolVersions", card)
        self.assertNotIn("protocolVersion", card)
        self.assertNotIn("preferredTransport", card)
        self.assertEqual(card["skills"][0]["inputModes"], ["text/plain", "application/json"])

        task_payload = self._read_rpc("SendMessage", {"message": self._message("hello")})
        self.assertEqual(self._assert_one_of(task_payload["result"], ("task", "message")), "task")
        task = task_payload["result"]["task"]
        task_id = task["id"]
        fetched = self._read_rpc("GetTask", {"id": task_id, "historyLength": 1})

        self.assertEqual(task["status"]["state"], "TASK_STATE_COMPLETED")
        self.assertEqual(fetched["result"]["id"], task_id)
        self.assertEqual(len(fetched["result"]["history"]), 1)
        self._assert_task(task)

    def test_bearer_agent_card_remains_public_and_declares_security(self) -> None:
        config = A2APluginConfig(
            host="127.0.0.1",
            port=0,
            store_path=str(Path(self.tmpdir.name) / "auth-server.db"),
            bearer_token="secret",
            exported_skills=["delegate"],
            execution_adapter="demo",
        )
        auth_server = create_server(config=config)
        auth_server.start()
        try:
            with urllib.request.urlopen(
                f"{auth_server.base_url}/.well-known/agent-card.json", timeout=5
            ) as response:
                card = json.loads(response.read().decode("utf-8"))

            self.assertEqual(
                card["securitySchemes"],
                {
                    "bearerAuth": {
                        "httpAuthSecurityScheme": {
                            "scheme": "Bearer",
                        }
                    }
                },
            )
            self.assertEqual(card["security"], [{"bearerAuth": []}])

            payload = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "test",
                    "method": "SendMessage",
                    "params": {"message": self._message("hello")},
                }
            ).encode("utf-8")
            request = urllib.request.Request(
                f"{auth_server.base_url}/rpc",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "A2A-Version": PROTOCOL_VERSION,
                },
            )
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(request, timeout=5)
            self.assertEqual(raised.exception.code, 401)
        finally:
            auth_server.stop()

    def test_stream_subscribe_list_cancel_and_push_config_crud(self) -> None:
        stream_events = self._read_stream(
            "SendStreamingMessage",
            {"message": self._message("hello")},
        )
        stream_results = [event["result"] for event in stream_events]
        for result in stream_results:
            self._assert_stream_response(result)
        self.assertTrue(any("statusUpdate" in result for result in stream_results))
        self.assertTrue(any("artifactUpdate" in result for result in stream_results))
        task = stream_results[-1]["task"]
        task_id = task["id"]
        self._assert_task(task)

        list_payload = self._read_rpc(
            "ListTasks",
            {
                "contextId": task["contextId"],
                "status": "TASK_STATE_COMPLETED",
                "pageSize": 10,
            },
        )
        self.assertEqual(list_payload["result"]["totalSize"], 1)
        self.assertEqual(list_payload["result"]["nextPageToken"], "")
        self.assertNotIn("artifacts", list_payload["result"]["tasks"][0])

        cancel_payload = self._read_rpc("CancelTask", {"id": task_id})
        self.assertEqual(cancel_payload["result"]["status"]["state"], "TASK_STATE_CANCELED")

        input_payload = self._read_rpc("SendMessage", {"message": self._message("need input")})
        input_task = input_payload["result"]["task"]
        replay = self._read_stream("SubscribeToTask", {"id": input_task["id"]})
        self.assertIn("task", replay[0]["result"])
        self.assertEqual(replay[0]["result"]["task"]["status"]["state"], "TASK_STATE_INPUT_REQUIRED")

        callback, callback_url, callback_server, callback_thread = self._start_callback_server()

        set_payload = self._read_rpc(
            "CreateTaskPushNotificationConfig",
            {
                "taskId": task_id,
                "pushNotificationConfig": {
                    "id": "cfg-1",
                    "url": callback_url,
                    "token": "tok",
                    "authentication": {
                        "scheme": "Bearer",
                        "credentials": "secret",
                    },
                },
            },
        )
        get_payload = self._read_rpc("GetTaskPushNotificationConfig", {"taskId": task_id, "id": "cfg-1"})
        list_configs = self._read_rpc("ListTaskPushNotificationConfigs", {"taskId": task_id})

        self.assertEqual(set_payload["result"]["taskId"], task_id)
        self.assertEqual(set_payload["result"]["pushNotificationConfig"]["id"], "cfg-1")
        self.assertEqual(get_payload["result"]["pushNotificationConfig"]["url"], callback_url)
        self.assertEqual(len(list_configs["result"]["configs"]), 1)
        self.assertEqual(list_configs["result"]["nextPageToken"], "")

        try:
            self._read_rpc(
                "SendMessage",
                {
                    "message": self._message(
                        "follow up",
                        task_id=task_id,
                        context_id=task["contextId"],
                    )
                },
            )
        finally:
            callback_server.shutdown()
            callback_server.server_close()
            callback_thread.join(timeout=2)

        self.assertEqual(callback["content_type"], "application/a2a+json")
        self.assertEqual(callback["authorization"], "Bearer secret")
        self.assertIn("statusUpdate", callback["payload"])
        self._assert_stream_response(callback["payload"])

        delete_payload = self._read_rpc(
            "DeleteTaskPushNotificationConfig",
            {"taskId": task_id, "id": "cfg-1"},
        )
        self.assertIsNone(delete_payload["result"])

    def test_send_message_configuration_registers_push_config(self) -> None:
        callback, callback_url, callback_server, callback_thread = self._start_callback_server()
        try:
            payload = self._read_rpc(
                "SendMessage",
                {
                    "message": self._message("hello with inline callback"),
                    "configuration": {
                        "taskPushNotificationConfig": {
                            "taskId": "",
                            "pushNotificationConfig": {
                                "id": "inline",
                                "url": callback_url,
                                "authentication": {
                                    "scheme": "Bearer",
                                    "credentials": "inline-secret",
                                },
                            },
                        }
                    },
                },
            )
        finally:
            callback_server.shutdown()
            callback_server.server_close()
            callback_thread.join(timeout=2)

        task = payload["result"]["task"]
        stored = self._read_rpc("GetTaskPushNotificationConfig", {"taskId": task["id"], "id": "inline"})

        self.assertEqual(stored["result"]["pushNotificationConfig"]["url"], callback_url)
        self.assertEqual(callback["content_type"], "application/a2a+json")
        self.assertEqual(callback["authorization"], "Bearer inline-secret")
        self.assertIn("statusUpdate", callback["payload"])
        self._assert_stream_response(callback["payload"])

    def test_official_part_shapes_are_not_silently_dropped(self) -> None:
        data_payload = self._read_rpc(
            "SendMessage",
            {
                "message": {
                    "messageId": str(uuid4()),
                    "role": "ROLE_USER",
                    "parts": [{"data": {"question": "structured"}}],
                }
            },
        )
        data_part = data_payload["result"]["task"]["artifacts"][0]["parts"][0]
        self.assertEqual(self._assert_part(data_part), "data")
        self.assertEqual(data_part["data"], {"question": "structured"})
        self.assertEqual(data_part["mediaType"], "application/json")

        raw_payload = self._read_rpc(
            "SendMessage",
            {
                "message": {
                    "messageId": str(uuid4()),
                    "role": "ROLE_USER",
                    "parts": [
                        {
                            "raw": "aGVsbG8=",
                            "filename": "doc.txt",
                            "mediaType": "text/plain",
                        }
                    ],
                }
            },
        )
        raw_part = raw_payload["result"]["task"]["artifacts"][0]["parts"][0]
        self.assertEqual(self._assert_part(raw_part), "text")
        self.assertIn("raw: aGVsbG8=", raw_part["text"])
        self.assertIn("filename=doc.txt", raw_part["text"])

        url_payload = self._read_rpc(
            "SendMessage",
            {
                "message": {
                    "messageId": str(uuid4()),
                    "role": "ROLE_USER",
                    "parts": [
                        {
                            "url": "https://example.test/doc.txt",
                            "filename": "doc.txt",
                            "mediaType": "text/plain",
                        }
                    ],
                }
            },
        )
        url_part = url_payload["result"]["task"]["artifacts"][0]["parts"][0]
        self.assertEqual(self._assert_part(url_part), "url")
        self.assertIn("https://example.test/doc.txt", url_part["url"])
        self.assertIn("mediaType=text/plain", url_part["url"])

        invalid = self._read_rpc(
            "SendMessage",
            {
                "message": {
                    "messageId": str(uuid4()),
                    "role": "ROLE_USER",
                    "parts": [{"text": "hello", "data": {"extra": True}}],
                }
            },
        )
        self.assertEqual(invalid["error"]["code"], ERROR_INVALID_PARAMS)

    def test_list_tasks_status_timestamp_after_parses_offsets(self) -> None:
        payload = self._read_rpc("SendMessage", {"message": self._message("offset filter")})
        task = payload["result"]["task"]
        timestamp = task["status"]["timestamp"]
        timestamp_dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        equivalent_offset = timestamp_dt.astimezone(timezone(timedelta(hours=1))).isoformat(
            timespec="milliseconds"
        )

        listed = self._read_rpc(
            "ListTasks",
            {
                "statusTimestampAfter": equivalent_offset,
                "pageSize": 10,
            },
        )

        self.assertEqual(listed["result"]["totalSize"], 1)
        self.assertEqual(listed["result"]["tasks"][0]["id"], task["id"])

    def test_extended_card_version_and_legacy_method_errors(self) -> None:
        missing_version = self._read_rpc(
            "SendMessage",
            {"message": self._message("hello")},
            version=None,
        )
        legacy = self._read_rpc("message/send", {"message": self._message("hello")})
        extended = self._read_rpc("GetExtendedAgentCard", {})

        self.assertEqual(missing_version["error"]["code"], ERROR_VERSION_NOT_SUPPORTED)
        self.assertEqual(legacy["error"]["code"], ERROR_METHOD_NOT_FOUND)
        self.assertEqual(extended["error"]["code"], ERROR_UNSUPPORTED_OPERATION)

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

        self.assertEqual(delegated["task"]["status"]["state"], "TASK_STATE_COMPLETED")
        self.assertEqual(refreshed["id"], delegated["task"]["id"])
