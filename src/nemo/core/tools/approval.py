"""Three-level approval, expressed as a ``ToolPolicy``.

The policy only *decides*; it never talks to a terminal. The client supplies an
``Approver`` callable, which keeps core free of I/O -- the same boundary as the
config and prompt loaders -- and makes every decision testable with a scripted
answer. ``ToolPolicy.decide`` was already async in M3 for exactly this reason,
so the Runtime needs no approval branch at all.

The three modes differ in one place only: what an *unclassified* action means.

============  ==========  ========  ========
class         ``ask``     ``auto``  ``full``
============  ==========  ========  ========
read-only     run         run       run
destructive   confirm     confirm   run
unclassified  confirm     run       run
============  ==========  ========  ========

``auto`` therefore equals ``full`` minus the dangers it can recognise. That is
the whole point -- it confirms *only* what it can name -- and it is why the
design document gives ``auto`` no safety promise.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel

from nemo.core.contracts.tools import ExecutionContext, PolicyDecision, ToolCallFacts
from nemo.core.tools.command_rules import CommandClass, classify


class ApprovalMode(StrEnum):
    #: Codex "Read Only": confirm every call that can change something.
    ASK = "ask"
    #: Codex "Auto": run what looks bounded, confirm what does not.
    AUTO = "auto"
    #: Codex "Full Access": never confirm.
    FULL = "full"


class ApprovalOutcome(StrEnum):
    ALLOW_ONCE = "allow_once"
    ALLOW_SESSION = "allow_session"
    DENY = "deny"


@dataclass(frozen=True)
class ApprovalRequest:
    """What the client shows when asking. Never contains raw arguments."""

    tool_name: str
    summary: str
    reason: str


Approver = Callable[[ApprovalRequest], Awaitable[ApprovalOutcome]]


class ApprovalPolicy:
    """Implements :class:`nemo.core.contracts.tools.ToolPolicy`."""

    def __init__(
        self,
        mode: ApprovalMode = ApprovalMode.ASK,
        *,
        approver: Approver | None = None,
    ):
        self.mode = ApprovalMode(mode)
        self._approver = approver
        self._granted: set[str] = set()

    @property
    def session_grants(self) -> frozenset[str]:
        """Tool names the user allowed for the rest of this session."""

        return frozenset(self._granted)

    async def decide(
        self,
        *,
        facts: ToolCallFacts,
        arguments: BaseModel,
        context: ExecutionContext,
    ) -> PolicyDecision:
        if facts.read_only or self.mode is ApprovalMode.FULL:
            return PolicyDecision(allowed=True)
        reason = self._escalation_reason(facts, context)
        if reason is None:
            return PolicyDecision(allowed=True)
        if facts.tool_name in self._granted:
            return PolicyDecision(
                allowed=True, reason=f"{facts.tool_name} is approved for this session"
            )
        if self._approver is None:
            # No one is present to answer, so the safe answer is no.
            return PolicyDecision(
                allowed=False,
                reason=f"{reason}; no approver is attached, so this call was denied",
            )
        outcome = await self._approver(
            ApprovalRequest(tool_name=facts.tool_name, summary=facts.summary, reason=reason)
        )
        if outcome is ApprovalOutcome.ALLOW_SESSION:
            self._granted.add(facts.tool_name)
            return PolicyDecision(
                allowed=True, reason=f"{facts.tool_name} is approved for this session"
            )
        if outcome is ApprovalOutcome.ALLOW_ONCE:
            return PolicyDecision(allowed=True)
        # Anything else -- including an approver returning a value it should
        # not -- is a denial. Fail closed.
        return PolicyDecision(allowed=False, reason="The user denied this call")

    def _escalation_reason(self, facts: ToolCallFacts, context: ExecutionContext) -> str | None:
        """Why this call needs a human, or ``None`` when it does not."""

        if facts.shell_command is not None:
            verdict = classify(facts.shell_command, workspace=context.workspace)
            if verdict.kind is CommandClass.READ_ONLY:
                return None
            if verdict.kind is CommandClass.UNCLASSIFIED and self.mode is ApprovalMode.AUTO:
                return None
            return verdict.reason
        if self.mode is ApprovalMode.AUTO:
            # File tools are already bounded by the workspace path check, so a
            # write inside the workspace is what ``auto`` exists to let through.
            return None
        return f"{facts.tool_name} changes files"
