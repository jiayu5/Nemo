"""Pure Trace projection from persisted Run data and domain events."""

from datetime import datetime
from typing import Any

from nemo.core.contracts.events import EventType
from nemo.core.contracts.types import Event
from nemo.server.errors import RunNotFoundError
from nemo.server.repository import Repository
from nemo.server.views import run_view


class TraceService:
    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    def get(self, run_id: str) -> dict[str, Any]:
        run = self.repository.get_run(run_id)
        if run is None:
            raise RunNotFoundError(run_id)
        events = self.repository.events_after(run_id, 0)
        return {
            "run": run_view(run),
            "duration_ms": duration_ms(run.get("started_at"), run.get("finished_at")),
            "steps": [
                {
                    "step": row["step"],
                    "started_at": row["started_at"],
                    "finished_at": row["finished_at"],
                    "duration_ms": duration_ms(row["started_at"], row["finished_at"]),
                }
                for row in self.repository.steps(run_id)
            ],
            "model_calls": model_calls(events),
            "tool_calls": [tool_call_view(row) for row in self.repository.tool_calls(run_id)],
            "events": events,
        }


def duration_ms(started_at: str | None, finished_at: str | None) -> float | None:
    if not started_at or not finished_at:
        return None
    delta = datetime.fromisoformat(finished_at) - datetime.fromisoformat(started_at)
    return round(delta.total_seconds() * 1000, 3)


def tool_call_view(row: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "tool_call_id",
        "step",
        "name",
        "summary",
        "status",
        "error_code",
        "started_at",
        "finished_at",
    )
    return {key: row[key] for key in fields} | {
        "duration_ms": duration_ms(row["started_at"], row["finished_at"])
    }


def model_calls(events: list[Event]) -> list[dict[str, Any]]:
    pending: dict[int, Event] = {}
    calls: list[dict[str, Any]] = []
    for event in events:
        if event.type == EventType.MODEL_STARTED:
            pending[event.step] = event
        elif event.type == EventType.MODEL_COMPLETED and event.step in pending:
            started = pending.pop(event.step)
            calls.append(
                {
                    "step": event.step,
                    "started_at": started.timestamp.isoformat(),
                    "finished_at": event.timestamp.isoformat(),
                    "duration_ms": round(
                        (event.timestamp - started.timestamp).total_seconds() * 1000, 3
                    ),
                    "status": "completed",
                    "prompt_tokens": event.payload.get("prompt_tokens"),
                    "cached_prompt_tokens": event.payload.get("cached_prompt_tokens"),
                    "completion_tokens": event.payload.get("completion_tokens"),
                }
            )
    for step, started in sorted(pending.items()):
        calls.append(
            {
                "step": step,
                "started_at": started.timestamp.isoformat(),
                "finished_at": None,
                "duration_ms": None,
                "status": "incomplete",
            }
        )
    return sorted(calls, key=lambda item: item["step"])
