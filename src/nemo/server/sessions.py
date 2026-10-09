"""Session application service."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from nemo.config.loader import load_config
from nemo.core.contracts.types import Message
from nemo.core.models.registry import ModelRegistry
from nemo.core.session import new_session_id
from nemo.core.skills import SkillCatalog
from nemo.core.tools.approval import ApprovalMode
from nemo.prompts.instructions import load_project_instructions, load_user_instructions
from nemo.server.errors import SessionNotFoundError
from nemo.server.repository import Repository
from nemo.server.views import run_view, session_view


class SessionService:
    def __init__(
        self,
        repository: Repository,
        *,
        config_loader: Callable[[], Any] = load_config,
        skill_catalog_factory: Callable[[Path], SkillCatalog] | None = None,
    ) -> None:
        self.repository = repository
        self._config_loader = config_loader
        self._skill_catalog_factory = skill_catalog_factory
        self._skills: dict[str, SkillCatalog] = {}

    def create(
        self, *, workspace: str, model: str | None, approval_mode: ApprovalMode
    ) -> dict[str, Any]:
        path = Path(workspace).expanduser().resolve()
        if not path.is_dir():
            raise ValueError("workspace must be an existing directory")
        self._validate_model(model)
        catalog = self._skill_catalog_factory(path) if self._skill_catalog_factory else None
        row = self.repository.create_session(
            session_id=new_session_id(),
            workspace=path,
            model=model,
            approval_mode=approval_mode.value,
            user_instructions=load_user_instructions(),
            project_instructions=load_project_instructions(path),
        )
        if catalog is not None:
            self._skills[row["session_id"]] = catalog
        return session_view(self.repository, row)

    def skills(self, session_id: str) -> SkillCatalog | None:
        row = self.require_row(session_id)
        if self._skill_catalog_factory and session_id not in self._skills:
            self._skills[session_id] = self._skill_catalog_factory(Path(row["workspace"]))
        return self._skills.get(session_id)

    def resume(self, session_id: str) -> dict[str, Any]:
        row = self.require_row(session_id)
        active = any(run["status"] in {"queued", "running"}
                     for run in self.repository.list_runs(session_id))
        if self._skill_catalog_factory and not active:
            self._skills[session_id] = self._skill_catalog_factory(Path(row["workspace"]))
        return session_view(self.repository, row)

    def list(self) -> list[dict[str, Any]]:
        return [session_view(self.repository, row) for row in self.repository.list_sessions()]

    def get(self, session_id: str) -> dict[str, Any]:
        return session_view(self.repository, self.require_row(session_id))

    def delete(self, session_id: str) -> None:
        self.repository.delete_session(session_id)
        self._skills.pop(session_id, None)

    def update(
        self,
        session_id: str,
        *,
        model: str | None = None,
        approval_mode: ApprovalMode | None = None,
    ) -> dict[str, Any]:
        self.require_row(session_id)
        self._validate_model(model)
        row = self.repository.update_session(
            session_id,
            model=model,
            approval_mode=approval_mode.value if approval_mode is not None else None,
        )
        if row is None:
            raise SessionNotFoundError(session_id)
        return session_view(self.repository, row)

    def set_model(self, session_id: str, model: str) -> dict[str, Any]:
        return self.update(session_id, model=model)

    def messages(self, session_id: str) -> tuple[Message, ...]:
        self.require_row(session_id)
        return self.repository.messages(session_id)

    def runs(self, session_id: str) -> list[dict[str, Any]]:
        self.require_row(session_id)
        return [run_view(row) for row in self.repository.list_runs(session_id)]

    def require_row(self, session_id: str) -> dict[str, Any]:
        row = self.repository.get_session(session_id)
        if row is None:
            raise SessionNotFoundError(session_id)
        return row

    def _validate_model(self, model: str | None) -> None:
        if model is not None:
            ModelRegistry(self._config_loader()).resolve_name(model)
