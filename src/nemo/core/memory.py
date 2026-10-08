"""Memory contracts and append-only catalog delivery; no storage dependencies."""
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from nemo.core.contracts.types import Message

MemoryScope = Literal["user", "project"]
CATALOG_HEADER = "# Saved memory index (application data)\n"


class MemoryDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=3000)
    kind: Literal["preference", "feedback", "project", "reference"] = "project"


class MemoryEntry(MemoryDraft):
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    scope: MemoryScope
    workspace: str | None
    revision: str
    updated_at: str
    source_session_id: str | None = None
    source_message_index: int | None = None
    source_run_id: str | None = None


class MemorySettings(Protocol):
    def memory_enabled(self) -> bool: ...
    def set_memory_enabled(self, enabled: bool) -> None: ...


class MemoryStore(Protocol):
    def enabled(self) -> bool: ...
    def set_enabled(self, enabled: bool) -> None: ...
    def list(self, workspace: Path, scope: MemoryScope) -> list[MemoryEntry]: ...
    def save(self, workspace: Path, scope: MemoryScope, draft: MemoryDraft, *,
             entry_id: str | None = None, revision: str | None = None,
             source_session_id: str | None = None, source_message_index: int | None = None,
             source_run_id: str | None = None) -> MemoryEntry: ...
    def forget(self, workspace: Path, scope: MemoryScope, entry_id: str, revision: str) -> None: ...
    def catalog(self, workspace: Path) -> str: ...


def memory_messages(history: tuple[Message, ...], catalog: str) -> tuple[Message, ...]:
    """Append a catalog only when changed. Old bytes and M7 digests stay intact."""
    previous = next((m.content for m in reversed(history)
                     if m.role == "assistant" and m.content.startswith(CATALOG_HEADER)), None)
    content = CATALOG_HEADER + (catalog or "No active memories. Earlier saved entries are no longer active.")
    if previous == content or (previous is None and not catalog):
        return ()
    return (Message(role="assistant", content=content),)
