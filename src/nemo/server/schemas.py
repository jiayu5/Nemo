"""HTTP request and response contracts."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from nemo.core.contracts.types import Event
from nemo.core.tools.approval import ApprovalMode, ApprovalOutcome


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateSessionRequest(ApiModel):
    workspace: str = "."
    model: str | None = None
    approval_mode: ApprovalMode = ApprovalMode.ASK

    @field_validator("workspace")
    @classmethod
    def workspace_must_be_directory(cls, value: str) -> str:
        path = Path(value).expanduser().resolve()
        if not path.is_dir():
            raise ValueError("workspace must be an existing directory")
        return str(path)


class CreateRunRequest(ApiModel):
    prompt: str = Field(min_length=1)
    max_steps: int = Field(default=10, ge=1, le=100)


class UpdateSessionRequest(ApiModel):
    model: str | None = None
    approval_mode: ApprovalMode | None = None

    @field_validator("model")
    @classmethod
    def model_may_not_be_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("model may not be blank")
        return value


class UpdateSessionModelRequest(ApiModel):
    model: str = Field(min_length=1)


class ApprovalAnswerRequest(ApiModel):
    outcome: ApprovalOutcome


class SessionResponse(ApiModel):
    session_id: str
    workspace: str
    model: str | None
    approval_mode: str
    created_at: str
    updated_at: str
    message_count: int = 0


class RunResponse(ApiModel):
    run_id: str
    session_id: str
    status: str
    max_steps: int
    output: str | None = None
    error: str | None = None
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    model_selection: str | None = None
    model_name: str | None = None
    model_id: str | None = None
    model_protocol: str | None = None
    model_provider: str | None = None


class ActionResponse(ApiModel):
    status: Literal["accepted", "already_terminal", "answered"]


class ModelOptionResponse(ApiModel):
    selection: str
    kind: Literal["profile", "alias", "model"]
    model_name: str
    model_id: str
    provider_id: str
    protocol: str
    capabilities: list[str]
    is_default: bool


class ProviderResponse(ApiModel):
    provider_id: str
    protocol: str
    base_url: str
    api_key_env: str
    secret_configured: bool
    timeout_seconds: float
    models: list[str]


class ProviderTestRequest(ApiModel):
    model: str | None = None


class ProviderTestResponse(ApiModel):
    status: Literal["ok"]
    provider_id: str
    selection: str
    model_name: str
    model_id: str
    protocol: str
    latency_ms: float


class TraceStepResponse(ApiModel):
    step: int
    started_at: str | None
    finished_at: str | None
    duration_ms: float | None


class TraceModelCallResponse(ApiModel):
    step: int
    started_at: str
    finished_at: str | None
    duration_ms: float | None
    status: Literal["completed", "incomplete"]
    prompt_tokens: int | None = None
    cached_prompt_tokens: int | None = None
    completion_tokens: int | None = None


class TraceToolCallResponse(ApiModel):
    tool_call_id: str
    step: int
    name: str
    summary: str | None
    status: str
    error_code: str | None
    started_at: str
    finished_at: str | None
    duration_ms: float | None


class TraceResponse(ApiModel):
    run: RunResponse
    duration_ms: float | None
    steps: list[TraceStepResponse]
    model_calls: list[TraceModelCallResponse]
    tool_calls: list[TraceToolCallResponse]
    events: list[Event]
