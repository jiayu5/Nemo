"""Stable domain event names shared by Runtime and application adapters."""

from enum import StrEnum


class EventType(StrEnum):
    RUN_STARTED = "run.started"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"
    RUN_CANCELLED = "run.cancelled"
    RUN_INTERRUPTED = "run.interrupted"
    RUN_LIMIT_REACHED = "run.limit_reached"
    STEP_STARTED = "step.started"
    STEP_COMPLETED = "step.completed"
    MODEL_STARTED = "model.started"
    MODEL_COMPLETED = "model.completed"
    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    TOOL_FAILED = "tool.failed"
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_RESOLVED = "approval.resolved"
    OBSERVER_FAILED = "observer.failed"


TERMINAL_RUN_EVENT_TYPES = frozenset(
    {
        EventType.RUN_COMPLETED,
        EventType.RUN_FAILED,
        EventType.RUN_CANCELLED,
        EventType.RUN_INTERRUPTED,
        EventType.RUN_LIMIT_REACHED,
    }
)
