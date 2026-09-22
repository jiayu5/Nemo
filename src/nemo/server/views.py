"""Safe response projections shared by Server application services."""

from typing import Any

from nemo.server.repository import Repository


RUN_FIELDS = (
    "run_id",
    "session_id",
    "status",
    "max_steps",
    "output",
    "error",
    "created_at",
    "started_at",
    "finished_at",
    "model_selection",
    "model_name",
    "model_id",
    "model_protocol",
    "model_provider",
)


def session_view(repository: Repository, row: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": row["session_id"],
        "workspace": row["workspace"],
        "model": row["model"],
        "approval_mode": row["approval_mode"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "message_count": len(repository.messages(row["session_id"])),
    }


def run_view(row: dict[str, Any]) -> dict[str, Any]:
    return {key: row[key] for key in RUN_FIELDS}
