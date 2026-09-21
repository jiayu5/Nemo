"""Tool validation, policy enforcement and errors are separate from generation."""
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ValidationError

from nemo.core.contracts.errors import ToolFailure
from nemo.core.contracts.tools import ExecutionContext, ToolCallFacts, ToolPolicy
from nemo.core.contracts.types import ToolCall, ToolError, ToolResult, ToolSpec
from nemo.core.tools.policy import AllowAllPolicy

MAX_SUMMARY_CHARS = 200


class Tool(Protocol):
    """A single, narrowly-scoped capability.

    ``read_only`` lets a policy separate inspection from mutation, and
    ``summarize`` is how a tool states what it is about to do *without* dumping
    raw arguments into the trace.

   """

    name: str
    description: str
    arguments_type: type[BaseModel]
    read_only: bool

    async def execute(self, arguments: BaseModel, context: ExecutionContext) -> object: ...

    def summarize(self, arguments: BaseModel) -> str: ...

    # Optional member: ``shell_command(arguments) -> str | None``. A tool that
    # runs shell commands declares the exact text here so the approval layer can
    # classify it without importing adapter code (see RunShellTool). Optional
    # rather than required so a tool that runs no commands needs no stub.


class ToolRegistry:
    def __init__(
        self,
        tools: tuple[Tool, ...] = (),
        *,
        context: ExecutionContext | None = None,
        policy: ToolPolicy | None = None,
        max_result_chars: int = 20000,
    ):
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            if tool.name in self._tools:
                raise ValueError(f"Duplicate tool: {tool.name}")
            self._tools[tool.name] = tool
        self.context = context or ExecutionContext(workspace=Path.cwd())
        self.policy = policy or AllowAllPolicy()
        self.max_result_chars = max_result_chars

    def schemas(self) -> tuple[ToolSpec, ...]:
        return tuple(ToolSpec(name=t.name, description=t.description,
                              parameters=t.arguments_type.model_json_schema())
                     for t in self._tools.values())

    def summary_for(self, call: ToolCall) -> str | None:
        """A short, tool-authored description of the call, safe for the trace.

        Summaries are still model-influenced text, so they are length-bounded and
        collapsed to one line. Redacting sensitive fragments is the client's job
        (see M4).
        """

        tool = self._tools.get(call.name)
        if tool is None:
            return None
        try:
            arguments = tool.arguments_type.model_validate(call.arguments, strict=True)
        except Exception:
            # A tool that cannot describe its own call must not break the trace.
            return None
        return self._describe(tool, arguments) or None

    async def execute(self, call: ToolCall) -> ToolResult:
        tool = self._tools.get(call.name)
        if tool is None:
            return self._error(call, "unknown_tool", "Tool is not registered")
        try:
            arguments = tool.arguments_type.model_validate(call.arguments, strict=True)
        except ValidationError:
            # Do not echo arguments: they can contain secrets.
            return self._error(call, "invalid_arguments", "Arguments do not match tool schema")
        decision = await self.policy.decide(
            facts=self._facts(tool, arguments), arguments=arguments, context=self.context
        )
        if not decision.allowed:
            return self._error(call, "denied", decision.reason or "Denied by policy")
        try:
            output = await tool.execute(arguments, self.context)
        except ToolFailure as exc:
            # Expected failure: the message is authored by our own tool code and
            # is exactly what the model needs to correct itself.
            return self._error(call, "execution_error", exc.public_message)
        except Exception:
            # Cancellation is BaseException and must propagate to the Runtime.
            return self._error(call, "execution_error", "Tool execution failed")
        result = ToolResult(tool_call_id=call.id, output=output)
        try:
            payload = result.model_dump_json()  # Reject non-serializable results.
        except Exception:
            return self._error(call, "execution_error", "Tool returned a non-serializable result")
        if len(payload) > self.max_result_chars:
            return self._error(
                call,
                "execution_error",
                f"Tool result exceeded {self.max_result_chars} characters; narrow the request",
            )
        return result

    @staticmethod
    def _error(call, code, message):
        return ToolResult(tool_call_id=call.id, error=ToolError(code=code, message=message))

    def _facts(self, tool: Tool, arguments: BaseModel) -> ToolCallFacts:
        return ToolCallFacts(
            tool_name=tool.name,
            read_only=bool(tool.read_only),
            summary=self._describe(tool, arguments),
            shell_command=self._shell_command(tool, arguments),
        )

    @staticmethod
    def _describe(tool: Tool, arguments: BaseModel) -> str:
        try:
            summary = " ".join(str(tool.summarize(arguments)).split())
        except Exception:
            return ""
        return summary[:MAX_SUMMARY_CHARS] if summary else ""

    @staticmethod
    def _shell_command(tool: Tool, arguments: BaseModel) -> str | None:
        getter = getattr(tool, "shell_command", None)
        if getter is None:
            return None
        try:
            value = getter(arguments)
        except Exception:
            # A declaration that cannot be produced must not break execution;
            # the policy then sees "no shell command", which its own defaults
            # already handle conservatively.
            return None
        return value if isinstance(value, str) and value.strip() else None
