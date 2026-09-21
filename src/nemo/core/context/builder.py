from collections.abc import Sequence

from nemo.core.context.reminder import Reminder
from nemo.core.contracts.types import AgentState, Message, ModelRequest
from nemo.core.tools.limits import truncate_text
from nemo.core.tools.registry import ToolRegistry

DEFAULT_MAX_INSTRUCTIONS_CHARS = 8000
USER_INSTRUCTIONS_HEADER = "# User instructions (from ~/.nemo/AGENTS.md)"
PROJECT_INSTRUCTIONS_HEADER = "# Project instructions (from AGENTS.md)"


class ContextBuilder:
    """Assembles the messages sent to the model.

    Three layers, each with a different lifetime:

    - **Stable prefix** (``self.prefix``): system prompt plus the instruction
      files, assembled once at construction and never rebuilt. Same bytes every
      request, which is what makes provider-side prefix caching work. The user's
      instructions come before the project's, so a project can add conventions
      on top of a personal preference rather than the other way round.
    - **Conversation history**: ``AgentState.messages``, deep-copied so a model
      implementation cannot mutate it. Append-only.
    - **Reminders**: per-turn injections appended after the history. They are not
      stored in the state, so they cannot accumulate, and they never modify an
      existing message (see :mod:`nemo.core.context.reminder`).

    The prefix lives here rather than in ``AgentState.messages`` because it is a
    property of how an agent is assembled, not of the conversation: the state
    stays exactly what the user and the model produced.
    """

    def __init__(
        self,
        system_prompt: str | None = None,
        *,
        user_instructions: str | None = None,
        project_instructions: str | None = None,
        max_instructions_chars: int = DEFAULT_MAX_INSTRUCTIONS_CHARS,
    ):
        self.system_prompt = (system_prompt or "").strip()
        self.user_instructions = (user_instructions or "").strip()
        self.project_instructions = (project_instructions or "").strip()
        self.max_instructions_chars = max_instructions_chars
        self.prefix = self._assemble_prefix()

    def build(
        self,
        state: AgentState,
        tools: ToolRegistry,
        reminders: Sequence[Reminder] = (),
    ) -> ModelRequest:
        # Independent snapshots prevent a model implementation from mutating history.
        messages = [m.model_copy(deep=True) for m in state.messages]
        if reminders:
            messages.append(
                Message(role="user", content="\n".join(r.render() for r in reminders))
            )
        if self.prefix:
            messages.insert(0, Message(role="system", content=self.prefix))
        return ModelRequest(messages=tuple(messages), tools=tools.schemas())

    def _assemble_prefix(self) -> str:
        """Build the stable prefix once; nothing here may depend on the run state."""

        parts = [self.system_prompt] if self.system_prompt else []
        # Both instruction files follow the behavioural clauses, whose last one
        # states that neither may relax the boundaries. Generic first, specific
        # last: a project's conventions sit closest to the conversation.
        for header, text in (
            (USER_INSTRUCTIONS_HEADER, self.user_instructions),
            (PROJECT_INSTRUCTIONS_HEADER, self.project_instructions),
        ):
            bounded = self._bounded_instructions(text)
            if bounded:
                parts.append(f"{header}\n{bounded}")
        return "\n\n".join(parts)

    def _bounded_instructions(self, text: str) -> str:
        if not text or len(text) <= self.max_instructions_chars:
            return text
        return truncate_text(text, self.max_instructions_chars)
