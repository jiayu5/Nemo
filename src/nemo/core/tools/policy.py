"""Policy implementations available in Phase 1.

Interactive approval needs a client to ask, so it arrives with the CLI in M4.
Until then a policy can only allow or deny, which is already enough to keep
read-only tools available while withholding everything that writes.
"""

from pydantic import BaseModel

from nemo.core.contracts.tools import ExecutionContext, PolicyDecision, ToolCallFacts


class AllowAllPolicy:
    """Default: every registered tool may run. The workspace still applies."""

    async def decide(
        self,
        *,
        facts: ToolCallFacts,
        arguments: BaseModel,
        context: ExecutionContext,
    ) -> PolicyDecision:
        return PolicyDecision(allowed=True)


class ReadOnlyPolicy:
    """Deny anything that can modify the machine."""

    async def decide(
        self,
        *,
        facts: ToolCallFacts,
        arguments: BaseModel,
        context: ExecutionContext,
    ) -> PolicyDecision:
        if facts.read_only:
            return PolicyDecision(allowed=True)
        return PolicyDecision(
            allowed=False,
            reason=f"{facts.tool_name} can modify the machine and read-only mode is on",
        )
