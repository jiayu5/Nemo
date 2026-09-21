"""SQLite persistence adapter for local Server sessions and runs."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nemo.core.contracts.types import Event, Message, RunResult, RunStatus
from nemo.redaction import redact


TERMINAL_STATUSES = frozenset(
    {
        RunStatus.COMPLETED.value,
        RunStatus.FAILED.value,
        RunStatus.CANCELLED.value,
        RunStatus.INTERRUPTED.value,
        RunStatus.LIMIT_REACHED.value,
    }
)


class ActiveRunError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(_redact_value(value), ensure_ascii=False, separators=(",", ":"))


def _redact_value(value: Any) -> Any:
    """Redact string leaves without touching JSON syntax or value types."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {key: _redact_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(item) for item in value)
    return value


class SQLiteRepository:
    """Small synchronous repository serialized by one re-entrant lock.

    SQLite calls are short and local. Keeping the adapter synchronous avoids an
    extra database dependency; the service never holds the lock while awaiting
    a model, tool, approval, or client.
    """

    def __init__(self, path: Path | str):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        os.chmod(self.path, 0o600)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._create_schema()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _create_schema(self) -> None:
        with self._lock, self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    workspace TEXT NOT NULL,
                    model TEXT,
                    approval_mode TEXT NOT NULL,
                    user_instructions TEXT,
                    project_instructions TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
                    position INTEGER NOT NULL,
                    message_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (session_id, position)
                );
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
                    status TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    max_steps INTEGER NOT NULL,
                    output TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_run_per_session
                    ON runs(session_id) WHERE status IN ('queued', 'running');
                CREATE TABLE IF NOT EXISTS events (
                    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                    seq INTEGER NOT NULL,
                    step INTEGER NOT NULL,
                    type TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, seq)
                );
                CREATE TABLE IF NOT EXISTS steps (
                    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                    step INTEGER NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    PRIMARY KEY (run_id, step)
                );
                CREATE TABLE IF NOT EXISTS tool_calls (
                    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                    tool_call_id TEXT NOT NULL,
                    step INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    summary TEXT,
                    status TEXT NOT NULL,
                    error_code TEXT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    PRIMARY KEY (run_id, tool_call_id)
                );
                CREATE TABLE IF NOT EXISTS approval_requests (
                    request_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                    tool_name TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    status TEXT NOT NULL,
                    outcome TEXT,
                    created_at TEXT NOT NULL,
                    answered_at TEXT
                );
                """
            )

    def create_session(
        self,
        *,
        session_id: str,
        workspace: Path,
        model: str | None,
        approval_mode: str,
        user_instructions: str | None,
        project_instructions: str | None,
    ) -> dict[str, Any]:
        now = _now()
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO sessions
                   (session_id, workspace, model, approval_mode, user_instructions,
                    project_instructions, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    str(workspace),
                    model,
                    approval_mode,
                    redact(user_instructions) if user_instructions else None,
                    redact(project_instructions) if project_instructions else None,
                    now,
                    now,
                ),
            )
        return self.get_session(session_id)  # type: ignore[return-value]

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM sessions ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def update_session(
        self, session_id: str, *, model: str | None = None, approval_mode: str | None = None
    ) -> dict[str, Any] | None:
        assignments = []
        values: list[Any] = []
        if model is not None:
            assignments.append("model = ?")
            values.append(model)
        if approval_mode is not None:
            assignments.append("approval_mode = ?")
            values.append(approval_mode)
        if assignments:
            assignments.append("updated_at = ?")
            values.append(_now())
            values.append(session_id)
            with self._lock, self._connection:
                self._connection.execute(
                    f"UPDATE sessions SET {', '.join(assignments)} WHERE session_id = ?",
                    values,
                )
        return self.get_session(session_id)

    def messages(self, session_id: str) -> tuple[Message, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT message_json FROM messages WHERE session_id = ? ORDER BY position",
                (session_id,),
            ).fetchall()
        return tuple(Message.model_validate_json(row["message_json"]) for row in rows)

    def create_run(
        self, *, run_id: str, session_id: str, prompt: str, max_steps: int
    ) -> dict[str, Any]:
        now = _now()
        try:
            with self._lock, self._connection:
                self._connection.execute(
                    """INSERT INTO runs
                       (run_id, session_id, status, prompt, max_steps, created_at)
                       VALUES (?, ?, 'queued', ?, ?, ?)""",
                    (run_id, session_id, redact(prompt), max_steps, now),
                )
        except sqlite3.IntegrityError as exc:
            if "one_active_run_per_session" in str(exc) or "runs.session_id" in str(exc):
                raise ActiveRunError("the session already has an active run") from None
            raise
        return self.get_run(run_id)  # type: ignore[return-value]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def set_run_running(self, run_id: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE runs SET status = 'running', started_at = ? WHERE run_id = ?",
                (_now(), run_id),
            )

    def finish_run(self, result: RunResult) -> None:
        run_id = result.state.run_id
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT session_id FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            session_id = row["session_id"]
            self._connection.execute(
                """UPDATE runs SET status = ?, output = ?, error = ?, finished_at = ?
                   WHERE run_id = ?""",
                (
                    result.state.status.value,
                    redact(result.state.output) if result.state.output else None,
                    redact(result.state.error) if result.state.error else None,
                    _now(),
                    run_id,
                ),
            )
            existing = self._connection.execute(
                "SELECT COUNT(*) AS count FROM messages WHERE session_id = ?", (session_id,)
            ).fetchone()["count"]
            for position, message in enumerate(result.state.messages[existing:], start=existing):
                self._connection.execute(
                    """INSERT INTO messages(session_id, position, message_json, created_at)
                       VALUES (?, ?, ?, ?)""",
                    (session_id, position, _json(message.model_dump(mode="json")), _now()),
                )
            self._connection.execute(
                "UPDATE sessions SET updated_at = ? WHERE session_id = ?",
                (_now(), session_id),
            )

    def fail_run(self, run_id: str, message: str) -> None:
        with self._lock:
            with self._connection:
                self._connection.execute(
                    """UPDATE runs SET status = 'failed', error = ?, finished_at = ?
                       WHERE run_id = ?""",
                    (redact(message), _now(), run_id),
                )
            # Keep the repository lock until the terminal event is visible so
            # an SSE reader cannot observe a terminal status without its event.
            self.append_event(
                run_id=run_id, step=0, kind="run.failed", payload={"error": message}
            )

    def interrupt_run(self, run_id: str) -> None:
        with self._lock:
            with self._connection:
                changed = self._connection.execute(
                    """UPDATE runs SET status = 'interrupted', finished_at = ?
                       WHERE run_id = ? AND status IN ('queued', 'running')""",
                    (_now(), run_id),
                ).rowcount
            if changed:
                self.append_event(
                    run_id=run_id, step=0, kind="run.interrupted", payload={}
                )

    def append_event(
        self, *, run_id: str, step: int, kind: str, payload: dict[str, Any], timestamp: str | None = None
    ) -> Event:
        safe_payload = json.loads(_json(payload))
        when = timestamp or _now()
        with self._lock, self._connection:
            seq = self._connection.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 AS seq FROM events WHERE run_id = ?",
                (run_id,),
            ).fetchone()["seq"]
            self._connection.execute(
                """INSERT INTO events(run_id, seq, step, type, timestamp, payload_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (run_id, seq, step, kind, when, _json(safe_payload)),
            )
            self._materialize_event(run_id, seq, step, kind, safe_payload, when)
        return Event(
            run_id=run_id,
            seq=seq,
            step=step,
            type=kind,
            timestamp=datetime.fromisoformat(when),
            payload=safe_payload,
        )

    def append_runtime_event(self, event: Event) -> Event:
        return self.append_event(
            run_id=event.run_id,
            step=event.step,
            kind=event.type,
            payload=event.payload,
            timestamp=event.timestamp.isoformat(),
        )

    def events_after(self, run_id: str, seq: int = 0) -> list[Event]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM events WHERE run_id = ? AND seq > ? ORDER BY seq",
                (run_id, seq),
            ).fetchall()
        return [
            Event(
                run_id=row["run_id"],
                seq=row["seq"],
                step=row["step"],
                type=row["type"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
                payload=json.loads(row["payload_json"]),
            )
            for row in rows
        ]

    def create_approval(
        self,
        *,
        request_id: str,
        run_id: str,
        tool_name: str,
        summary: str,
        reason: str,
        step: int,
    ) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO approval_requests
                   (request_id, run_id, tool_name, summary, reason, status, created_at)
                   VALUES (?, ?, ?, ?, ?, 'pending', ?)""",
                (request_id, run_id, tool_name, redact(summary), redact(reason), _now()),
            )
        self.append_event(
            run_id=run_id,
            step=step,
            kind="approval.requested",
            payload={
                "request_id": request_id,
                "tool_name": tool_name,
                "summary": summary,
                "reason": reason,
            },
        )

    def answer_approval(self, request_id: str, outcome: str) -> bool:
        with self._lock, self._connection:
            changed = self._connection.execute(
                """UPDATE approval_requests
                   SET status = 'answered', outcome = ?, answered_at = ?
                   WHERE request_id = ? AND status = 'pending'""",
                (outcome, _now(), request_id),
            ).rowcount
        return bool(changed)

    def get_approval(self, request_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM approval_requests WHERE request_id = ?", (request_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def recover_interrupted_runs(self) -> list[str]:
        with self._lock, self._connection:
            rows = self._connection.execute(
                "SELECT run_id FROM runs WHERE status IN ('queued', 'running')"
            ).fetchall()
            ids = [row["run_id"] for row in rows]
            for run_id in ids:
                self._connection.execute(
                    """UPDATE runs SET status = 'interrupted', finished_at = ?
                       WHERE run_id = ?""",
                    (_now(), run_id),
                )
        for run_id in ids:
            self.append_event(run_id=run_id, step=0, kind="run.interrupted", payload={})
        return ids

    def _materialize_event(
        self, run_id: str, seq: int, step: int, kind: str, payload: dict[str, Any], when: str
    ) -> None:
        if kind == "step.started":
            self._connection.execute(
                """INSERT OR IGNORE INTO steps(run_id, step, started_at)
                   VALUES (?, ?, ?)""",
                (run_id, step, when),
            )
        elif kind == "step.completed":
            self._connection.execute(
                "UPDATE steps SET finished_at = ? WHERE run_id = ? AND step = ?",
                (when, run_id, step),
            )
        elif kind == "tool.started":
            self._connection.execute(
                """INSERT OR REPLACE INTO tool_calls
                   (run_id, tool_call_id, step, name, summary, status, started_at)
                   VALUES (?, ?, ?, ?, ?, 'running', ?)""",
                (
                    run_id,
                    payload.get("tool_call_id", f"event-{seq}"),
                    step,
                    payload.get("name", "unknown"),
                    payload.get("summary"),
                    when,
                ),
            )
        elif kind in {"tool.completed", "tool.failed"}:
            self._connection.execute(
                """UPDATE tool_calls SET status = ?, error_code = ?, finished_at = ?
                   WHERE run_id = ? AND tool_call_id = ?""",
                (
                    "failed" if kind == "tool.failed" else "completed",
                    payload.get("error_code"),
                    when,
                    run_id,
                    payload.get("tool_call_id"),
                ),
            )
