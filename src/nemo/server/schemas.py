"""HTTP request and response contracts."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

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


class ActionResponse(ApiModel):
    status: Literal["accepted", "already_terminal", "answered"]
