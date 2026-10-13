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
    MODEL_DELTA = "model.delta"
    MODEL_REASONING_DELTA = "model.reasoning_delta"
    MODEL_COMPLETED = "model.completed"
    CONTEXT_COMPACTED = "context.compacted"
    CONTEXT_COMPRESSION_STARTED = "context.compression_started"
    CONTEXT_COMPRESSION_COMPLETED = "context.compression_completed"
    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    TOOL_FAILED = "tool.failed"
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_RESOLVED = "approval.resolved"
    MCP_CONNECTION_SKIPPED = "mcp.connection_skipped"
    OBSERVER_FAILED = "observer.failed"
    SUBAGENT_EVENT = "subagent.event"


TERMINAL_RUN_EVENT_TYPES = frozenset(
    {
        EventType.RUN_COMPLETED,
        EventType.RUN_FAILED,
        EventType.RUN_CANCELLED,
        EventType.RUN_INTERRUPTED,
        EventType.RUN_LIMIT_REACHED,
    }
)
