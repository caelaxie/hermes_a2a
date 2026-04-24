"""Integration tests for inbound and outbound A2A 1.0 flows."""

from __future__ import annotations

import asyncio
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

from hermes_a2a.adapter import HermesEvent, HermesExecutionAdapter
from hermes_a2a.client import A2AClient
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


class BlockingStreamAdapter(HermesExecutionAdapter):
    def __init__(self) -> None:
        self.first_event_yielded = threading.Event()
        self.release = threading.Event()
        self.canceled = threading.Event()
        self.cancel_calls: list[tuple[str, str]] = []

    def _events(self, task_id: str, context_id: str) -> list[HermesEvent]:
        return [
            HermesEvent(
                kind="status",
                state="working",
                message="blocking adapter started",
                metadata={"task_id": task_id, "context_id": context_id},
            ),
            HermesEvent(
                kind="artifact",
                state="working",
                message="blocking adapter output emitted",
                text="finished",
                metadata={"artifact_id": "blocking-output"},
            ),
            HermesEvent(
                kind="status",
                state="completed",
                message="blocking adapter completed",
                metadata={"task_id": task_id, "context_id": context_id},
            ),
        ]

    def start(
        self,
        task_id: str,
        context_id: str,
        message: str,
        metadata: dict | None = None,
    ):
        del message, metadata
        return self._events(task_id, context_id)

    def continue_task(
        self,
        task_id: str,
        context_id: str,
        message: str,
        metadata: dict | None = None,
    ):
        del message, metadata
        return self._events(task_id, context_id)

    def stream(
        self,
        task_id: str,
        context_id: str,
        message: str,
        metadata: dict | None = None,
    ):
        del message, metadata
        events = self._events(task_id, context_id)
        self.first_event_yielded.set()
        yield events[0]
        while not self.release.wait(timeout=0.05):
            if self.canceled.is_set():
                return
        if self.canceled.is_set():
            return
        yield from events[1:]

    def cancel(
        self,
        task_id: str,
        context_id: str,
        metadata: dict | None = None,
    ):
        del metadata
        self.cancel_calls.append((task_id, context_id))
        self.canceled.set()
        return [
            HermesEvent(
                kind="status",
                state="canceled",
                message="blocking adapter canceled",
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
            "adapter": "blocking",
            "metadata": metadata or {},
        }


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

    def _rpc_to_server(
        self,
        base_url: str,
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
            f"{base_url}/rpc",
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
            cache_control = response.getheader("Cache-Control")
            etag = response.getheader("ETag")

        with urllib.request.urlopen(
            f"{self.server.base_url}/.well-known/agent-card.json", timeout=5
        ) as response:
            repeat_etag = response.getheader("ETag")

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
        self.assertTrue(card["capabilities"]["streaming"])
        self.assertEqual(card["skills"][0]["inputModes"], ["text/plain", "application/json"])
        self.assertEqual(cache_control, "public, max-age=300")
        self.assertTrue(etag)
        self.assertEqual(repeat_etag, etag)

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
        try:
            from a2a.types.a2a_pb2 import AgentCard
            from google.protobuf.json_format import ParseDict
        except ImportError as exc:
            self.skipTest(f"a2a-sdk protobuf parser unavailable: {exc}")

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
            self.assertNotIn("security", card)
            self.assertEqual(card["securityRequirements"], [{"schemes": {"bearerAuth": []}}])
            ParseDict(card, AgentCard(), ignore_unknown_fields=False)

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
        terminal_subscribe = self._read_rpc("SubscribeToTask", {"id": task_id})
        self.assertEqual(terminal_subscribe["error"]["code"], ERROR_UNSUPPORTED_OPERATION)

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
                "id": "cfg-1",
                "url": callback_url,
                "token": "tok",
                "authentication": {
                    "scheme": "Bearer",
                    "credentials": "secret",
                },
            },
        )
        get_payload = self._read_rpc("GetTaskPushNotificationConfig", {"taskId": task_id, "id": "cfg-1"})
        list_configs = self._read_rpc("ListTaskPushNotificationConfigs", {"taskId": task_id})

        self.assertEqual(set_payload["result"]["taskId"], task_id)
        self.assertEqual(set_payload["result"]["id"], "cfg-1")
        self.assertEqual(set_payload["result"]["url"], callback_url)
        self.assertEqual(set_payload["result"]["token"], "tok")
        self.assertEqual(get_payload["result"]["url"], callback_url)
        self.assertEqual(len(list_configs["result"]["configs"]), 1)
        self.assertEqual(list_configs["result"]["configs"][0]["id"], "cfg-1")
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

    def test_send_streaming_message_flushes_events_before_adapter_finishes(self) -> None:
        adapter = BlockingStreamAdapter()
        with tempfile.TemporaryDirectory() as tmpdir:
            config = A2APluginConfig(
                host="127.0.0.1",
                port=0,
                store_path=str(Path(tmpdir) / "stream.db"),
                execution_adapter="demo",
            )
            server = create_server(config=config, adapter=adapter)
            server.start()
            first_line: dict[str, str] = {}
            first_line_read = threading.Event()
            client_error: list[BaseException] = []

            def read_first_stream_line() -> None:
                try:
                    with self._rpc_to_server(
                        server.base_url,
                        "SendStreamingMessage",
                        {"message": self._message("slow hello")},
                        accept="text/event-stream",
                    ) as response:
                        first_line["content_type"] = response.headers.get_content_type()
                        first_line["line"] = response.readline().decode("utf-8")
                        first_line_read.set()
                        response.read()
                except BaseException as exc:  # pragma: no cover - failure diagnostics
                    client_error.append(exc)

            client = threading.Thread(target=read_first_stream_line)
            client.start()
            try:
                self.assertTrue(adapter.first_event_yielded.wait(timeout=2))
                self.assertTrue(
                    first_line_read.wait(timeout=0.5),
                    "first SSE frame stayed buffered until task completion",
                )
                self.assertFalse(client_error)
                self.assertEqual(first_line["content_type"], "text/event-stream")
                self.assertTrue(first_line["line"].startswith("data: "))
                payload = json.loads(first_line["line"].split(":", 1)[1].strip())
                self.assertIn("statusUpdate", payload["result"])
            finally:
                adapter.release.set()
                client.join(timeout=2)
                server.stop()

    def test_cancel_task_finds_active_streaming_task_before_finalization(self) -> None:
        adapter = BlockingStreamAdapter()
        with tempfile.TemporaryDirectory() as tmpdir:
            config = A2APluginConfig(
                host="127.0.0.1",
                port=0,
                store_path=str(Path(tmpdir) / "cancel-stream.db"),
                execution_adapter="demo",
            )
            server = create_server(config=config, adapter=adapter)
            server.start()
            first_payload: dict[str, dict] = {}
            first_line_read = threading.Event()
            client_error: list[BaseException] = []

            def read_stream() -> None:
                try:
                    with self._rpc_to_server(
                        server.base_url,
                        "SendStreamingMessage",
                        {"message": self._message("cancel me while streaming")},
                        accept="text/event-stream",
                    ) as response:
                        line = response.readline().decode("utf-8")
                        first_payload["event"] = json.loads(line.split(":", 1)[1].strip())
                        first_line_read.set()
                        response.read()
                except BaseException as exc:  # pragma: no cover - failure diagnostics
                    client_error.append(exc)

            client = threading.Thread(target=read_stream)
            client.start()
            try:
                self.assertTrue(adapter.first_event_yielded.wait(timeout=2))
                self.assertTrue(first_line_read.wait(timeout=2))
                task_id = first_payload["event"]["result"]["statusUpdate"]["taskId"]

                with self._rpc_to_server(
                    server.base_url,
                    "CancelTask",
                    {"id": task_id},
                ) as response:
                    cancel_payload = json.loads(response.read().decode("utf-8"))

                self.assertNotIn("error", cancel_payload)
                self.assertEqual(
                    cancel_payload["result"]["status"]["state"],
                    "TASK_STATE_CANCELED",
                )
                self.assertEqual(adapter.cancel_calls, [(task_id, task_id)])
                client.join(timeout=2)
                self.assertFalse(client.is_alive())
                with self._rpc_to_server(server.base_url, "GetTask", {"id": task_id}) as response:
                    stored_payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(
                    stored_payload["result"]["status"]["state"],
                    "TASK_STATE_CANCELED",
                )
            finally:
                adapter.release.set()
                client.join(timeout=2)
                server.stop()

            self.assertFalse(client_error)

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
                            "id": "inline",
                            "url": callback_url,
                            "authentication": {
                                "scheme": "Bearer",
                                "credentials": "inline-secret",
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

        self.assertEqual(stored["result"]["taskId"], task["id"])
        self.assertEqual(stored["result"]["id"], "inline")
        self.assertEqual(stored["result"]["url"], callback_url)
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
        unsupported_version = self._read_rpc(
            "SendMessage",
            {"message": self._message("hello")},
            version="0.3",
        )
        legacy = self._read_rpc("message/send", {"message": self._message("hello")})
        extended = self._read_rpc("GetExtendedAgentCard", {})

        self.assertEqual(missing_version["error"]["code"], ERROR_VERSION_NOT_SUPPORTED)
        self.assertEqual(unsupported_version["error"]["code"], ERROR_VERSION_NOT_SUPPORTED)
        self.assertEqual(legacy["error"]["code"], ERROR_METHOD_NOT_FOUND)
        self.assertEqual(extended["error"]["code"], ERROR_UNSUPPORTED_OPERATION)

    def test_official_sdk_jsonrpc_client_sends_required_version_header(self) -> None:
        try:
            import httpx
            from a2a.client import ClientConfig, ClientFactory
            from a2a.types.a2a_pb2 import (
                Message,
                Part,
                ROLE_USER,
                SendMessageRequest,
                TASK_STATE_COMPLETED,
            )
        except ImportError as exc:
            self.skipTest(f"optional a2a-sdk extra is not installed: {exc}")

        async def run_sdk_request() -> int:
            http_client = httpx.AsyncClient(trust_env=False)
            client = await ClientFactory(
                ClientConfig(streaming=False, httpx_client=http_client)
            ).create_from_url(
                self.server.base_url,
                resolver_http_kwargs={"follow_redirects": False},
            )
            try:
                request = SendMessageRequest(
                    message=Message(
                        message_id=str(uuid4()),
                        role=ROLE_USER,
                        parts=[Part(text="hello from sdk")],
                    )
                )
                events = [event async for event in client.send_message(request)]
            finally:
                await client.close()
            self.assertEqual(len(events), 1)
            self.assertTrue(events[0].HasField("task"))
            return events[0].task.status.state

        state = asyncio.run(run_sdk_request())

        self.assertEqual(state, TASK_STATE_COMPLETED)

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

    def test_outbound_client_uses_jsonrpc_url_from_agent_card(self) -> None:
        requests = []

        class CustomRpcHandler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
                if self.path != "/.well-known/agent-card.json":
                    self.send_response(404)
                    self.end_headers()
                    return
                base_url = f"http://127.0.0.1:{self.server.server_address[1]}"
                payload = {
                    "name": "Custom RPC Agent",
                    "description": "Test agent with a non-default JSON-RPC path.",
                    "supportedInterfaces": [
                        {
                            "url": f"{base_url}/a2a",
                            "protocolBinding": "JSONRPC",
                            "protocolVersion": "1.0",
                        }
                    ],
                    "version": "test",
                    "defaultInputModes": ["text/plain"],
                    "defaultOutputModes": ["text/plain"],
                    "capabilities": {"streaming": True},
                    "skills": [],
                }
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler API
                requests.append(self.path)
                if self.path != "/a2a":
                    self.send_response(404)
                    self.end_headers()
                    return
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                payload = json.loads(body.decode("utf-8"))
                task = {
                    "id": "remote-task",
                    "contextId": "remote-task",
                    "status": {"state": "TASK_STATE_COMPLETED"},
                    "history": [],
                }
                if payload["method"] == "SendStreamingMessage":
                    response = {
                        "jsonrpc": "2.0",
                        "id": payload["id"],
                        "result": {"task": task},
                    }
                    response_body = f"data: {json.dumps(response)}\n\n".encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Content-Length", str(len(response_body)))
                    self.end_headers()
                    self.wfile.write(response_body)
                    return
                if payload["method"] == "SendMessage":
                    result = {"task": task}
                elif payload["method"] in {"GetTask", "CancelTask"}:
                    result = task
                else:
                    result = None
                response = {"jsonrpc": "2.0", "id": payload["id"], "result": result}
                response_body = json.dumps(response).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response_body)))
                self.end_headers()
                self.wfile.write(response_body)

            def log_message(self, format, *args):  # noqa: A003 - inherited name
                del format, args

        custom_server = HTTPServer(("127.0.0.1", 0), CustomRpcHandler)
        custom_thread = threading.Thread(target=custom_server.serve_forever, daemon=True)
        custom_thread.start()
        base_url = f"http://127.0.0.1:{custom_server.server_address[1]}"
        try:
            client = A2AClient(base_url)
            card = client.get_agent_card()
            sent = client.send_message("hello")
            streamed = list(client.stream_message("hello"))
            fetched = client.get_task("remote-task")
            canceled = client.cancel_task("remote-task")
        finally:
            custom_server.shutdown()
            custom_server.server_close()
            custom_thread.join(timeout=2)

        self.assertEqual(card["supportedInterfaces"][0]["url"], f"{base_url}/a2a")
        self.assertEqual(sent["id"], "remote-task")
        self.assertEqual(streamed[0]["task"]["id"], "remote-task")
        self.assertEqual(fetched["id"], "remote-task")
        self.assertEqual(canceled["id"], "remote-task")
        self.assertEqual(requests, ["/a2a", "/a2a", "/a2a", "/a2a"])
