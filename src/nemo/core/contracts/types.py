"""Provider-neutral, validated runtime contracts."""
from enum import StrEnum
from typing import Any, Literal, Protocol
from uuid import uuid4
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ToolCall(Contract):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    arguments: dict[str, Any]


class ToolError(Contract):
    code: Literal["unknown_tool", "invalid_arguments", "denied", "execution_error"]
    message: str


class ToolResult(Contract):
    tool_call_id: str
    output: Any = None
    error: ToolError | None = None


class Message(Contract):
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_result: ToolResult | None = None

    @model_validator(mode="after")
    def validate_role(self):
        if self.tool_calls and self.role != "assistant":
            raise ValueError("Only assistant messages may contain tool calls")
        if (self.role == "tool") != (self.tool_result is not None):
            raise ValueError("Tool messages must contain a tool result exclusively")
        return self


class ToolSpec(Contract):
    name: str
    description: str
    parameters: dict[str, Any]


class ModelRequest(Contract):
    messages: tuple[Message, ...]
    tools: tuple[ToolSpec, ...]


class ModelUsage(Contract):
    """Token accounting reported by a provider.

    ``cached_prompt_tokens`` is the part of the prompt the provider served from
    its prefix cache. Zero means "not reported or nothing cached"; it is not a
    promise that the prefix was actually reusable.
    """

    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    cached_prompt_tokens: int = Field(default=0, ge=0)

    def cache_hit_rate(self) -> float | None:
        """Share of prompt tokens served from cache, or ``None`` if unknown."""

        if self.prompt_tokens == 0:
            return None
        return self.cached_prompt_tokens / self.prompt_tokens


class ModelResponse(Contract):
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    usage: ModelUsage | None = None

    @model_validator(mode="after")
    def unique_call_ids(self):
        ids = [call.id for call in self.tool_calls]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate tool call IDs")
        return self


class Model(Protocol):
    async def generate(self, request: ModelRequest) -> ModelResponse: ...


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    LIMIT_REACHED = "limit_reached"


class AgentState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str = Field(default_factory=lambda: str(uuid4()))
    status: RunStatus = RunStatus.QUEUED
    messages: list[Message] = Field(default_factory=list)
    step_count: int = 0
    output: str | None = None
    error: str | None = None


class Event(Contract):
    run_id: str
    seq: int
    step: int
    type: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    payload: dict[str, Any] = Field(default_factory=dict)


class RunResult(Contract):
    state: AgentState
    events: tuple[Event, ...]
