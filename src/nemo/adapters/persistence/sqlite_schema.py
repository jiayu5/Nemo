"""SQLite schema creation and forward-only compatibility migrations."""

import sqlite3


SCHEMA = """
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
    finished_at TEXT,
    owner_id TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_run_per_session
    ON runs(session_id) WHERE status IN ('queued', 'running');
CREATE TABLE IF NOT EXISTS server_instances (
    instance_id TEXT PRIMARY KEY,
    pid INTEGER NOT NULL,
    heartbeat_at TEXT NOT NULL
);
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

RUN_IDENTITY_COLUMNS = {
    "model_selection": "TEXT",
    "model_name": "TEXT",
    "model_id": "TEXT",
    "model_protocol": "TEXT",
    "model_provider": "TEXT",
}
RUN_COORDINATION_COLUMNS = {
    "owner_id": "TEXT",
    "cancel_requested": "INTEGER NOT NULL DEFAULT 0",
}


def initialize_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)
    for column, declaration in (RUN_IDENTITY_COLUMNS | RUN_COORDINATION_COLUMNS).items():
        _ensure_column(connection, "runs", column, declaration)


def _ensure_column(
    connection: sqlite3.Connection, table: str, column: str, declaration: str
) -> None:
    columns = {
        row["name"]
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
