"""Per-turn injections: the only content added after the conversation history.

Three rules keep prompt caching alive, and each of them exists because breaking
it costs real tokens:

1. A reminder is **appended as its own message** and never edits an existing one.
   Editing a message that was already sent changes bytes at a position the
   provider has already cached, invalidating everything behind it.
2. It is **never stored in** ``AgentState``, so it cannot accumulate over a run.
3. It only exists when the model, not the user, is about to speak — which also
   keeps the message list from ever containing two consecutive ``user`` turns.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

from nemo.core.contracts.types import AgentState

REMINDER_OPEN = "<reminder>"
REMINDER_CLOSE = "</reminder>"

MAX_REMINDERS = 3
MAX_REMINDER_CHARS = 200

_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class Reminder:
    """One short status update from the runtime."""

    name: str
    body: str

    def __post_init__(self) -> None:
        if not _NAME_PATTERN.match(self.name):
            raise ValueError(f"reminder name must be snake_case: {self.name!r}")
        if not self.body.strip():
            raise ValueError(f"reminder '{self.name}' has an empty body")
        if len(self.body) > MAX_REMINDER_CHARS:
            raise ValueError(
                f"reminder '{self.name}' exceeds {MAX_REMINDER_CHARS} characters"
            )
        # A body carrying the closing tag could escape its own wrapper and end up
        # looking like conversation text.
        if REMINDER_CLOSE in self.body:
            raise ValueError(f"reminder '{self.name}' must not contain {REMINDER_CLOSE}")

    def render(self) -> str:
        return f"{REMINDER_OPEN}{self.body}{REMINDER_CLOSE}"


def step_budget(step: int, max_steps: int) -> Reminder:
    """How much of the run's step budget is left.

    The model cannot derive this: it does not know ``max_steps`` and cannot count
    its own remaining turns. Without it, a task can be cut off mid-way with no
    warning, which is exactly the failure this reminder prevents.
    """

    return Reminder(name="step_budget", body=f"step {step} of {max_steps}")


def slot_open(state: AgentState) -> bool:
    """True when a reminder may be appended this turn."""

    return bool(state.messages) and state.messages[-1].role != "user"


def fit_reminders(reminders: Iterable[Reminder]) -> tuple[Reminder, ...]:
    """Bound the injection set deterministically.

    Selection is by name order rather than arrival order so that identical input
    produces identical output; a run that drops a reminder must be reproducible.
    The worst case is therefore ``MAX_REMINDERS * MAX_REMINDER_CHARS`` characters,
    so a separate total-size cap would be redundant and is deliberately absent.
    """

    ordered = sorted(reminders, key=lambda item: item.name)
    return tuple(ordered[:MAX_REMINDERS])


def describe(reminders: Iterable[Reminder]) -> list[dict[str, object]]:
    """Trace-friendly description: names and sizes, never the bodies."""

    return [{"name": item.name, "chars": len(item.body)} for item in reminders]
