"""Memory management use cases shared by HTTP clients."""
from pathlib import Path

from nemo.core.contracts.errors import ToolFailure
from nemo.core.memory import MemoryDraft, MemoryScope, MemoryStore
from nemo.server.repository import Repository
from nemo.server.errors import SessionNotFoundError


class MemoryService:
    def __init__(self, repository: Repository, store: MemoryStore | None):
        self.repository = repository
        self.store = store

    def require_store(self) -> MemoryStore:
        if self.store is None:
            raise ToolFailure("memory is not configured")
        return self.store

    def workspace(self, session_id: str) -> Path:
        session = self.repository.get_session(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)
        return Path(session["workspace"])

    def list(self, session_id: str) -> dict:
        workspace = self.workspace(session_id)
        store = self.require_store()
        return {"enabled": store.enabled(), "entries": [entry.model_dump() for scope in ("user", "project")
                                                         for entry in store.list(workspace, scope)]}

    def save(self, session_id: str, scope: MemoryScope, draft: MemoryDraft, *,
             entry_id: str | None, revision: str | None) -> dict:
        # Manual edits are labelled by absent conversational provenance.
        return self.require_store().save(self.workspace(session_id), scope, draft,
                                         entry_id=entry_id, revision=revision).model_dump()

    def forget(self, session_id: str, scope: MemoryScope, entry_id: str, revision: str):
        self.require_store().forget(self.workspace(session_id), scope, entry_id, revision)
