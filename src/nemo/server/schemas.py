"""HTTP request and response contracts."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
    secret_source: Literal["environment", "file"] | None = None
    proxy_env: str | None = None
    proxy_configured: bool = False
    proxy_source: Literal["environment", "file"] | None = None
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


class SecretMutation(ApiModel):
    action: Literal["keep", "replace", "delete"] = "keep"
    value: str | None = Field(default=None, max_length=8192)

    @model_validator(mode="after")
    def value_matches_action(self):
        if self.action == "replace" and (self.value is None or not self.value.strip()):
            raise ValueError("replacement value may not be blank")
        if self.action != "replace" and self.value is not None:
            raise ValueError("value is only accepted for replace")
        return self


class ProviderSettingsRequest(ApiModel):
    protocol: Literal["openai_compatible", "openai_responses", "anthropic_messages"]
    base_url: str = Field(min_length=1)
    api_key_env: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    proxy_env: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    model_name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    model_id: str = Field(min_length=1)
    tool_calling: bool = True
    make_default: bool = False
    api_key: SecretMutation = Field(default_factory=SecretMutation)
    proxy: SecretMutation = Field(default_factory=SecretMutation)


class SettingsModelResponse(ApiModel):
    model_name: str
    model_id: str
    capabilities: list[str]
    is_default: bool


class SettingsProviderResponse(ApiModel):
    provider_id: str
    protocol: str
    base_url: str
    api_key_env: str
    api_key_source: Literal["environment", "file"] | None
    proxy_env: str | None
    proxy_source: Literal["environment", "file"] | None
    timeout_seconds: float
    models: list[SettingsModelResponse]


class ProviderSettingsResponse(ApiModel):
    config_exists: bool
    default: str | None
    providers: list[SettingsProviderResponse]


class ProviderSettingsResult(ApiModel):
    status: Literal["valid", "saved"]
    provider: SettingsProviderResponse
    writes: list[Literal["config", "secrets"]]


class ProviderDeleteResponse(ApiModel):
    status: Literal["deleted"]
    provider_id: str
    removed_models: list[str]
    default: str


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
