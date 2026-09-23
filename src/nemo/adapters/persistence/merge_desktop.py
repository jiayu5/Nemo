"""One-time, non-destructive merge of the former desktop history into nemo.db."""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from nemo.adapters.persistence.sqlite_schema import initialize_schema


TABLES = (
    "sessions",
    "runs",
    "messages",
    "events",
    "steps",
    "tool_calls",
    "approval_requests",
)


def _backup(path: Path, suffix: str) -> Path:
    destination = path.with_name(f"{path.name}.backup-{suffix}")
    with closing(sqlite3.connect(path)) as source, closing(sqlite3.connect(destination)) as target:
        source.backup(target)
    os.chmod(destination, 0o600)
    return destination


def _has_live_server(connection: sqlite3.Connection, database: str) -> bool:
    table = connection.execute(
        f"SELECT 1 FROM {database}.sqlite_master WHERE type = 'table' AND name = 'server_instances'"
    ).fetchone()
    if table is None:
        return False
    for row in connection.execute(f"SELECT pid, heartbeat_at FROM {database}.server_instances"):
        try:
            os.kill(row["pid"], 0)
        except ProcessLookupError:
            continue
        except PermissionError:
            pass
        if datetime.now(timezone.utc).timestamp() - datetime.fromisoformat(
            row["heartbeat_at"]
        ).timestamp() < 15:
            return True
    return False


def merge_desktop_history(main_path: Path, desktop_path: Path) -> tuple[Path, Path] | None:
    """Merge whole sessions atomically; return backup paths, or None if already merged."""
    main_path = main_path.expanduser().resolve()
    desktop_path = desktop_path.expanduser().resolve()
    if not main_path.is_file() or not desktop_path.is_file():
        raise FileNotFoundError("both existing database files are required")
    with closing(sqlite3.connect(main_path, timeout=10)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        initialize_schema(connection)
        connection.execute("ATTACH DATABASE ? AS source", (str(desktop_path),))
        try:
            if _has_live_server(connection, "main") or _has_live_server(connection, "source"):
                raise RuntimeError("stop both Server processes before merging history")
            active = connection.execute(
                """SELECT
                   (SELECT count(*) FROM main.runs WHERE status IN ('queued', 'running')) +
                   (SELECT count(*) FROM source.runs WHERE status IN ('queued', 'running'))"""
            ).fetchone()[0]
            if active:
                raise RuntimeError("stop or finish active runs before merging history")
            source_sessions = {
                row[0] for row in connection.execute("SELECT session_id FROM source.sessions")
            }
            source_runs = {row[0] for row in connection.execute("SELECT run_id FROM source.runs")}
            main_sessions = {row[0] for row in connection.execute("SELECT session_id FROM main.sessions")}
            main_runs = {row[0] for row in connection.execute("SELECT run_id FROM main.runs")}
            if source_sessions <= main_sessions and source_runs <= main_runs:
                return None
            if source_sessions & main_sessions or source_runs & main_runs:
                raise RuntimeError("history IDs overlap; merge requires manual review")

            suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            backups = (_backup(main_path, suffix), _backup(desktop_path, suffix))
            connection.execute("BEGIN IMMEDIATE")
            try:
                for table in TABLES:
                    target_columns = {
                        row["name"] for row in connection.execute(f"PRAGMA main.table_info({table})")
                    }
                    columns = [
                        row["name"] for row in connection.execute(f"PRAGMA source.table_info({table})")
                        if row["name"] in target_columns
                    ]
                    names = ", ".join(f'"{column}"' for column in columns)
                    connection.execute(
                        f"INSERT INTO main.{table} ({names}) SELECT {names} FROM source.{table}"
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            return backups
        finally:
            connection.execute("DETACH DATABASE source")


def main() -> None:
    home = Path.home() / ".nemo"
    result = merge_desktop_history(home / "nemo.db", home / "desktop.db")
    if result is None:
        print("Desktop history is already present in nemo.db")
    else:
        print(f"Merged desktop history; backups: {result[0]} and {result[1]}")


if __name__ == "__main__":
    main()
