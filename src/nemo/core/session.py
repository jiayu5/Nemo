"""Conversation state that outlives a single run.

A *session* is what a user resumes; a *run* is one turn of it. The split exists
because the two have different lifetimes: a run owns a status, an event stream
and a step budget, while a session owns the messages those runs produced.

Two invariants live here rather than in each caller:

- **History only grows.** ``record()`` adopts the run's messages wholesale; a
  message that was already sent can never be rewritten.
- **Reminders never enter it.** They exist only inside the request payload
  (see :mod:`nemo.core.context.reminder`), so the session stays exactly what the
  user and the model produced.

No file I/O and no persistence: transcripts are the client's job, the same way
config loading and prompt loading are (see ``nemo.cli.transcript``).

One session runs one turn at a time. The Runtime isolates concurrent runs, but
two runs appending into one conversation have no meaningful order, so it is not
offered.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from nemo.core.contracts.types import Message, RunResult


def new_session_id() -> str:
    """A sortable, human-readable id: ``20260918-141530-a1b2c3``."""

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{uuid4().hex[:6]}"


@dataclass
class Session:
    session_id: str = field(default_factory=new_session_id)
    workspace: Path = field(default_factory=Path.cwd)
    messages: list[Message] = field(default_factory=list)

    def history(self) -> tuple[Message, ...]:
        """A snapshot for the next run; ``AgentRuntime`` copies it again."""

        return tuple(self.messages)

    def record(self, result: RunResult) -> list[Message]:
        """Adopt everything the run produced and return only what is new.

        The length check is a guard against a caller wiring a run to a different
        conversation: adopting those messages silently would corrupt history.
        """

        if len(result.state.messages) < len(self.messages):
            raise ValueError(
                "run produced fewer messages than the session already had; "
                "this run was not started from this session's history"
            )
        added = list(result.state.messages[len(self.messages):])
        self.messages = list(result.state.messages)
        return added
