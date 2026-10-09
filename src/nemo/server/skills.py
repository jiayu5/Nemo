"""Installed skill queries and manual Markdown imports."""

from pathlib import Path

from nemo.core.contracts.errors import ToolFailure
from nemo.core.skills import SkillManagementStore
from nemo.server.errors import SessionNotFoundError
from nemo.server.repository import Repository


class SkillsService:
    def __init__(self, repository: Repository, store: SkillManagementStore | None):
        self.repository = repository
        self.store = store

    def _workspace(self, session_id: str | None) -> Path | None:
        if session_id is None:
            return None
        session = self.repository.get_session(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)
        return Path(session["workspace"])

    def _store(self) -> SkillManagementStore:
        if self.store is None:
            raise ToolFailure("Skill management is not configured")
        return self.store

    def list(self, session_id: str | None) -> dict:
        return self._store().list_installed(self._workspace(session_id))

    def detail(self, session_id: str | None, skill_id: str) -> dict:
        return self._store().detail(self._workspace(session_id), skill_id)

    def preview(self, session_id: str | None, scope: str, content: str) -> dict:
        return self._store().preview(self._workspace(session_id), scope, content)

    def install(self, session_id: str | None, scope: str, content: str) -> dict:
        return self._store().install(self._workspace(session_id), scope, content)
