"""Materialize query-friendly Step and Tool Call rows from domain events."""

import sqlite3
from typing import Any

from nemo.core.contracts.events import EventType


def materialize_event(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    seq: int,
    step: int,
    kind: str,
    payload: dict[str, Any],
    when: str,
) -> None:
    if kind == EventType.STEP_STARTED:
        connection.execute(
            """INSERT OR IGNORE INTO steps(run_id, step, started_at)
               VALUES (?, ?, ?)""",
            (run_id, step, when),
        )
    elif kind == EventType.STEP_COMPLETED:
        connection.execute(
            "UPDATE steps SET finished_at = ? WHERE run_id = ? AND step = ?",
            (when, run_id, step),
        )
    elif kind == EventType.TOOL_STARTED:
        connection.execute(
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
    elif kind in {EventType.TOOL_COMPLETED, EventType.TOOL_FAILED}:
        connection.execute(
            """UPDATE tool_calls SET status = ?, error_code = ?, finished_at = ?
               WHERE run_id = ? AND tool_call_id = ?""",
            (
                "failed" if kind == EventType.TOOL_FAILED else "completed",
                payload.get("error_code"),
                when,
                run_id,
                payload.get("tool_call_id"),
            ),
        )
