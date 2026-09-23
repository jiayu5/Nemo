"""SQLite repository for local Server sessions and runs."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from nemo.core.contracts.types import Event, Message, RunResult
from nemo.core.contracts.events import EventType
from nemo.redaction import redact
from nemo.adapters.persistence.event_projection import materialize_event
from nemo.adapters.persistence.sqlite_schema import initialize_schema
from nemo.server.errors import ActiveRunError, SessionNotFoundError


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


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
        self._connection = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
        os.chmod(self.path, 0o600)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA busy_timeout = 10000")
        self.instance_id = str(uuid4())
        self._stop_heartbeat = threading.Event()
        self._closed = False
        with self._connection:
            initialize_schema(self._connection)
            self._connection.execute(
                "INSERT INTO server_instances(instance_id, pid, heartbeat_at) VALUES (?, ?, ?)",
                (self.instance_id, os.getpid(), _now()),
            )
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat, name="nemo-db-heartbeat", daemon=True
        )
        self._heartbeat_thread.start()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop_heartbeat.set()
        self._heartbeat_thread.join(timeout=3)
        with self._lock:
            with self._connection:
                self._connection.execute(
                    "DELETE FROM server_instances WHERE instance_id = ?", (self.instance_id,)
                )
            self._connection.close()

    def _heartbeat(self) -> None:
        while not self._stop_heartbeat.wait(2):
            try:
                with self._lock, self._connection:
                    self._connection.execute(
                        "UPDATE server_instances SET heartbeat_at = ? WHERE instance_id = ?",
                        (_now(), self.instance_id),
                    )
            except sqlite3.Error:
                # A transient busy database must not end the liveness thread.
                continue

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

    def delete_session(self, session_id: str) -> None:
        """Delete a quiet session atomically; foreign keys remove its history."""
        with self._lock, self._connection:
            deleted = self._connection.execute(
                """DELETE FROM sessions
                   WHERE session_id = ? AND NOT EXISTS (
                       SELECT 1 FROM runs
                       WHERE runs.session_id = sessions.session_id
                         AND runs.status IN ('queued', 'running')
                   )""",
                (session_id,),
            )
            if deleted.rowcount:
                return
            exists = self._connection.execute(
                "SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if exists:
                raise ActiveRunError("the session has an active run")
            raise SessionNotFoundError(session_id)

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
                       (run_id, session_id, status, prompt, max_steps, created_at, owner_id)
                       VALUES (?, ?, 'queued', ?, ?, ?, ?)""",
                    (run_id, session_id, redact(prompt), max_steps, now, self.instance_id),
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

    def owns_run(self, run_id: str) -> bool:
        row = self.get_run(run_id)
        return row is not None and row["owner_id"] == self.instance_id

    def request_cancel(self, run_id: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE runs SET cancel_requested = 1 WHERE run_id = ? AND status IN ('queued', 'running')",
                (run_id,),
            )

    def cancel_requested(self, run_id: str) -> bool:
        row = self.get_run(run_id)
        return bool(row and row["cancel_requested"])

    def list_runs(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """SELECT * FROM runs WHERE session_id = ?
                   ORDER BY created_at DESC""",
                (session_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def set_run_model(
        self,
        run_id: str,
        *,
        selection: str,
        model_name: str,
        model_id: str,
        protocol: str,
        provider: str,
    ) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """UPDATE runs
                   SET model_selection = ?, model_name = ?, model_id = ?,
                       model_protocol = ?, model_provider = ?
                   WHERE run_id = ?""",
                (selection, model_name, model_id, protocol, provider, run_id),
            )

    def set_run_running(self, run_id: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE runs SET status = 'running', started_at = ? WHERE run_id = ? AND owner_id = ? AND status = 'queued'",
                (_now(), run_id, self.instance_id),
            )

    def finish_run(self, result: RunResult, *, terminal_event: Event) -> None:
        run_id = result.state.run_id
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT session_id FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            session_id = row["session_id"]
            changed = self._connection.execute(
                """UPDATE runs SET status = ?, output = ?, error = ?, finished_at = ?
                   WHERE run_id = ? AND owner_id = ? AND status IN ('queued', 'running')""",
                (
                    result.state.status.value,
                    redact(result.state.output) if result.state.output else None,
                    redact(result.state.error) if result.state.error else None,
                    _now(),
                    run_id,
                    self.instance_id,
                ),
            ).rowcount
            if not changed:
                return
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
            self._append_event_locked(
                run_id=run_id, step=terminal_event.step, kind=terminal_event.type,
                payload=terminal_event.payload, timestamp=terminal_event.timestamp.isoformat(),
            )

    def fail_run(self, run_id: str, message: str) -> None:
        with self._lock, self._connection:
            changed = self._connection.execute(
                """UPDATE runs SET status = 'failed', error = ?, finished_at = ?
                   WHERE run_id = ? AND owner_id = ? AND status IN ('queued', 'running')""",
                (redact(message), _now(), run_id, self.instance_id),
            ).rowcount
            if changed:
                self._append_event_locked(
                    run_id=run_id, step=0, kind=EventType.RUN_FAILED,
                    payload={"error": message},
                )

    def interrupt_run(self, run_id: str) -> None:
        with self._lock, self._connection:
            changed = self._connection.execute(
                """UPDATE runs SET status = 'interrupted', finished_at = ?
                   WHERE run_id = ? AND owner_id = ? AND status IN ('queued', 'running')""",
                (_now(), run_id, self.instance_id),
            ).rowcount
            if changed:
                self._append_event_locked(
                    run_id=run_id, step=0, kind=EventType.RUN_INTERRUPTED, payload={},
                )

    def append_event(
        self, *, run_id: str, step: int, kind: str, payload: dict[str, Any], timestamp: str | None = None
    ) -> Event:
        with self._lock, self._connection:
            return self._append_event_locked(
                run_id=run_id, step=step, kind=kind, payload=payload, timestamp=timestamp,
            )

    def _append_event_locked(
        self, *, run_id: str, step: int, kind: str,
        payload: dict[str, Any], timestamp: str | None = None,
    ) -> Event:
        safe_payload = json.loads(_json(payload))
        when = timestamp or _now()
        seq = self._connection.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS seq FROM events WHERE run_id = ?",
            (run_id,),
        ).fetchone()["seq"]
        self._connection.execute(
            """INSERT INTO events(run_id, seq, step, type, timestamp, payload_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (run_id, seq, step, kind, when, _json(safe_payload)),
        )
        materialize_event(
            self._connection, run_id=run_id, seq=seq, step=step,
            kind=kind, payload=safe_payload, when=when,
        )
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

    def steps(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM steps WHERE run_id = ? ORDER BY step", (run_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def tool_calls(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """SELECT * FROM tool_calls WHERE run_id = ?
                   ORDER BY started_at, tool_call_id""",
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

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
            kind=EventType.APPROVAL_REQUESTED,
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
            cutoff = datetime.now(timezone.utc) - timedelta(seconds=15)
            instances = self._connection.execute(
                "SELECT instance_id, pid, heartbeat_at FROM server_instances"
            ).fetchall()
            for instance in instances:
                if not _pid_alive(instance["pid"]) or datetime.fromisoformat(
                    instance["heartbeat_at"]
                ) < cutoff:
                    self._connection.execute(
                        "DELETE FROM server_instances WHERE instance_id = ?",
                        (instance["instance_id"],),
                    )
            ids = [
                row["run_id"] for row in self._connection.execute(
                    """SELECT r.run_id FROM runs r
                       LEFT JOIN server_instances i ON i.instance_id = r.owner_id
                       WHERE r.status IN ('queued', 'running') AND i.instance_id IS NULL"""
                )
            ]
            for run_id in ids:
                self._connection.execute(
                    """UPDATE runs SET status = 'interrupted', finished_at = ?
                       WHERE run_id = ?""",
                    (_now(), run_id),
                )
                self._append_event_locked(
                    run_id=run_id, step=0, kind=EventType.RUN_INTERRUPTED, payload={},
                )
        return ids
