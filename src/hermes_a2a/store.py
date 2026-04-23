"""Durable SQLite-backed storage for A2A task state."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from .mapping import utc_timestamp


class SQLiteTaskStore:
    """Persist task snapshots, event journals, and push configs."""

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
                    task_id TEXT PRIMARY KEY,
                    url TEXT NOT NULL,
                    token TEXT,
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
            created_at = task.get("createdAt", now)
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
                    task.get("status", {}).get("state", "submitted"),
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

    def append_event(self, task_id: str, event_type: str, payload: dict) -> int:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO events (task_id, event_type, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (task_id, event_type, json.dumps(payload, sort_keys=True), utc_timestamp()),
            )
            return int(cursor.lastrowid)

    def list_events(self, task_id: str, after_seq: int = 0) -> list[dict]:
        rows = self._conn.execute(
            """
            SELECT seq, event_type, payload_json, created_at
            FROM events
            WHERE task_id = ? AND seq > ?
            ORDER BY seq ASC
            """,
            (task_id, after_seq),
        ).fetchall()
        results: list[dict] = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            results.append(
                {
                    "sequence": row["seq"],
                    "event": row["event_type"],
                    "data": payload,
                    "createdAt": row["created_at"],
                }
            )
        return results

    def set_push_config(self, task_id: str, url: str, token: str = "") -> dict:
        now = utc_timestamp()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO push_configs (task_id, url, token, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    url = excluded.url,
                    token = excluded.token,
                    updated_at = excluded.updated_at
                """,
                (task_id, url, token, now, now),
            )
        return {"taskId": task_id, "pushNotificationConfig": {"url": url, "token": token}}

    def get_push_config(self, task_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT task_id, url, token FROM push_configs WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "taskId": row["task_id"],
            "pushNotificationConfig": {"url": row["url"], "token": row["token"] or ""},
        }

    def list_push_configs(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT task_id, url, token FROM push_configs ORDER BY task_id ASC"
        ).fetchall()
        return [
            {
                "taskId": row["task_id"],
                "pushNotificationConfig": {"url": row["url"], "token": row["token"] or ""},
            }
            for row in rows
        ]

    def delete_push_config(self, task_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM push_configs WHERE task_id = ?", (task_id,))

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
