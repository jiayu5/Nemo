"""Execution context and policy contracts shared by every local tool."""

from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field, model_validator

from nemo.core.contracts.types import Contract


class ExecutionContext(Contract):
    """What a tool is allowed to touch, and how much.

    ``workspace`` is a *convention*, not a sandbox: tools must refuse paths
    outside it, but nothing stops the model from asking a shell command to
    reach elsewhere. Phase 1 accepts that and keeps the boundary explicit.
    """

    workspace: Path
    max_output_chars: int = Field(default=8000, gt=0)
    default_timeout_seconds: float = Field(default=30.0, gt=0)
    max_timeout_seconds: float = Field(default=300.0, gt=0)

    @model_validator(mode="after")
    def check_timeouts(self):
        if self.default_timeout_seconds > self.max_timeout_seconds:
            raise ValueError("default_timeout_seconds must not exceed max_timeout_seconds")
        return self


class PolicyDecision(Contract):
    allowed: bool
    reason: str = ""


class ToolCallFacts(Contract):
    """What the registry knows about one call, handed to the policy.

    Grouped into a struct rather than passed as separate keyword arguments: M3
    predicted the ``decide`` signature would not change, and adding
    ``shell_command`` for approval already proved that wrong once. Facts are
    facts the *registry* observes, never a tool's opinion about its own safety.
    """

    tool_name: str
    read_only: bool
    summary: str = ""
    #: The text this call would run, when the tool runs shell commands. Absent
    #: means "this call runs no shell command", not "unknown".
    shell_command: str | None = None


class ToolPolicy(Protocol):
    """Decides whether one tool call may run.

    ``decide`` is async because the M4 CLI will ask the user for approval, which
    needs I/O. Keeping the signature async now avoids re-cutting the seam later.
    """

    async def decide(
        self,
        *,
        facts: ToolCallFacts,
        arguments: BaseModel,
        context: ExecutionContext,
    ) -> PolicyDecision: ...
