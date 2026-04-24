"""Durable SQLite-backed storage for A2A task state."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from .mapping import utc_timestamp
from .protocol import push_config_name


class SQLiteTaskStore:
    """Persist protocol-facing state outside the transport layer.

    Task snapshots are optimized for official `GetTask` and `ListTasks`
    responses. The event journal stores StreamResponse payloads for
    `SubscribeToTask`, and remote delegation mappings stay outside snapshots so
    local task IDs can remain the lookup key for Hermes tools.
    """

    def __init__(self, path: str) -> None:
        self.path = str(Path(path).expanduser())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    direction TEXT NOT NULL,
                    context_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS push_configs (
                    name TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    config_id TEXT NOT NULL,
                    url TEXT NOT NULL,
                    token TEXT,
                    config_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS remote_tasks (
                    task_id TEXT PRIMARY KEY,
                    agent_url TEXT NOT NULL,
                    remote_task_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

    def upsert_task(self, task: dict, direction: str) -> None:
        with self._lock, self._conn:
            now = utc_timestamp()
            created_at = task.get("status", {}).get("timestamp", now)
            self._conn.execute(
                """
                INSERT INTO tasks (task_id, direction, context_id, state, snapshot_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    direction = excluded.direction,
                    context_id = excluded.context_id,
                    state = excluded.state,
                    snapshot_json = excluded.snapshot_json,
                    updated_at = excluded.updated_at
                """,
                (
                    task["id"],
                    direction,
                    task.get("contextId", task["id"]),
                    task.get("status", {}).get("state", "TASK_STATE_SUBMITTED"),
                    json.dumps(task, sort_keys=True),
                    created_at,
                    now,
                ),
            )

    def get_task(self, task_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT snapshot_json FROM tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            return None
        return json.loads(row["snapshot_json"])

    def list_tasks(self, direction: str | None = None) -> list[dict]:
        if direction:
            rows = self._conn.execute(
                "SELECT snapshot_json FROM tasks WHERE direction = ? ORDER BY updated_at DESC",
                (direction,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT snapshot_json FROM tasks ORDER BY updated_at DESC"
            ).fetchall()
        return [json.loads(row["snapshot_json"]) for row in rows]

    def append_event(self, task_id: str, payload: dict) -> int:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO events (task_id, event_type, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (task_id, next(iter(payload.keys()), "task"), json.dumps(payload, sort_keys=True), utc_timestamp()),
            )
            return int(cursor.lastrowid)

    def list_events(self, task_id: str) -> list[dict]:
        rows = self._conn.execute(
            """
            SELECT seq, event_type, payload_json, created_at
            FROM events
            WHERE task_id = ?
            ORDER BY seq ASC
            """,
            (task_id,),
        ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def set_push_config(
        self,
        task_id: str,
        config_id: str,
        config: dict,
    ) -> dict:
        now = utc_timestamp()
        name = push_config_name(task_id, config_id)
        stored = dict(config)
        stored["taskId"] = task_id
        stored["id"] = config_id
        stored.setdefault("url", "")
        stored.setdefault("token", "")
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO push_configs
                    (name, task_id, config_id, url, token, config_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    url = excluded.url,
                    token = excluded.token,
                    config_json = excluded.config_json,
                    updated_at = excluded.updated_at
                """,
                (
                    name,
                    task_id,
                    config_id,
                    str(stored.get("url", "")),
                    str(stored.get("token", "")),
                    json.dumps(stored, sort_keys=True),
                    now,
                    now,
                ),
            )
        return stored

    def get_push_config(self, name: str) -> dict | None:
        row = self._conn.execute(
            "SELECT config_json FROM push_configs WHERE name = ?",
            (name,),
        ).fetchone()
        if row is None:
            return None
        return json.loads(row["config_json"])

    def list_push_configs(self, task_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT config_json FROM push_configs WHERE task_id = ? ORDER BY config_id ASC",
            (task_id,),
        ).fetchall()
        return [json.loads(row["config_json"]) for row in rows]

    def delete_push_config(self, name: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM push_configs WHERE name = ?", (name,))

    def list_push_configs_for_task(self, task_id: str) -> list[dict]:
        return self.list_push_configs(task_id)

    def set_remote_task(self, task_id: str, agent_url: str, remote_task_id: str) -> None:
        now = utc_timestamp()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO remote_tasks (task_id, agent_url, remote_task_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    agent_url = excluded.agent_url,
                    remote_task_id = excluded.remote_task_id,
                    updated_at = excluded.updated_at
                """,
                (task_id, agent_url, remote_task_id, now, now),
            )

    def get_remote_task(self, task_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT task_id, agent_url, remote_task_id FROM remote_tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "taskId": row["task_id"],
            "agentUrl": row["agent_url"],
            "remoteTaskId": row["remote_task_id"],
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()
