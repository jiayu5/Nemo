"""Nemo Server application facade shared by HTTP and tests."""

from collections.abc import Callable
from typing import Any
from pathlib import Path
from nemo.core.skills import SkillCatalog, SkillManagementStore
from nemo.server.skills import SkillsService

from nemo.core.memory import MemoryStore, memory_messages
from nemo.config.loader import load_config
from nemo.config.editor import ConfigEditor
from nemo.config.secrets import SecretLoader
from nemo.core.contracts.types import Event, Message
from nemo.core.tools.approval import ApprovalMode, ApprovalOutcome
from nemo.server.catalog import CatalogService
from nemo.server.memory import MemoryService
from nemo.server.repository import Repository
from nemo.server.runs import RunManager
from nemo.server.sessions import SessionService
from nemo.server.settings import SettingsService
from nemo.server.trace import TraceService


class NemoApplication:
    def __init__(
        self,
        repository: Repository,
        *,
        client_factory: Callable[..., Any],
        tools_factory: Callable[[], tuple[Any, ...]] = tuple,
        memory_store: MemoryStore | None = None,
        skill_catalog_factory: Callable[[Path], SkillCatalog] | None = None,
        skill_management_store: SkillManagementStore | None = None,
        skill_tools_factory: Callable[[SkillCatalog], tuple[Any, ...]] | None = None,
        approval_timeout_seconds: float = 300.0,
        config_loader: Callable[[], Any] = load_config,
        secret_loader_factory: Callable[[], Any] = SecretLoader,
        config_editor: ConfigEditor | None = None,
        mcp_tools_factory=None,
        mcp_probe=None,
        mcp_oauth=None,
    ) -> None:
        self.repository = repository
        from nemo.server.mcp import McpService
        self.mcp = McpService(config_editor or ConfigEditor(), probe=mcp_probe, oauth=mcp_oauth)
        self.memory_store = memory_store
        self.memories = MemoryService(repository, memory_store)
        self.skills = SkillsService(repository, skill_management_store)
        self.sessions = SessionService(repository, config_loader=config_loader,
                                       skill_catalog_factory=skill_catalog_factory)
        self.catalog = CatalogService(
            client_factory=client_factory,
            config_loader=config_loader,
            secret_loader_factory=secret_loader_factory,
        )
        self.runs = RunManager(
            repository,
            client_factory=client_factory,
            tools_factory=tools_factory,
            skill_catalog_loader=self.sessions.skills,
            skill_tools_factory=skill_tools_factory,
            memory_store=memory_store,
            approval_timeout_seconds=approval_timeout_seconds,
            config_loader=config_loader,
            mcp_tools_factory=mcp_tools_factory,
        )
        self.traces = TraceService(repository)
        self.settings = SettingsService(config_editor or ConfigEditor())

    async def startup(self) -> list[str]:
        return await self.runs.startup()

    async def shutdown(self) -> None:
        await self.mcp.shutdown()
        await self.runs.shutdown()

    def create_session(
        self, *, workspace: str, model: str | None, approval_mode: ApprovalMode
    ) -> dict[str, Any]:
        return self.sessions.create(
            workspace=workspace, model=model, approval_mode=approval_mode
        )

    def list_sessions(self) -> list[dict[str, Any]]:
        return self.sessions.list()

    def get_session(self, session_id: str) -> dict[str, Any]:
        return self.sessions.get(session_id)

    def resume_session(self, session_id: str) -> dict[str, Any]:
        return self.sessions.resume(session_id)

    def delete_session(self, session_id: str) -> None:
        self.sessions.delete(session_id)

    def update_session(
        self,
        session_id: str,
        *,
        model: str | None = None,
        approval_mode: ApprovalMode | None = None,
    ) -> dict[str, Any]:
        return self.sessions.update(
            session_id, model=model, approval_mode=approval_mode
        )

    def set_session_model(self, session_id: str, model: str) -> dict[str, Any]:
        return self.sessions.set_model(session_id, model)

    def session_messages(self, session_id: str) -> tuple[Message, ...]:
        return self.sessions.messages(session_id)

    def session_runs(self, session_id: str) -> list[dict[str, Any]]:
        return self.sessions.runs(session_id)

    def models(self) -> list[dict[str, Any]]:
        return self.catalog.models()

    def providers(self) -> list[dict[str, Any]]:
        return self.catalog.providers()

    async def test_provider(
        self, provider_id: str, selection: str | None = None
    ) -> dict[str, Any]:
        return await self.catalog.test_provider(provider_id, selection)

    def provider_settings(self) -> dict[str, Any]:
        return self.settings.list()

    def validate_provider_settings(self, provider_id: str, request) -> dict[str, Any]:
        return self.settings.validate(provider_id, request)

    def save_provider_settings(self, provider_id: str, request) -> dict[str, Any]:
        return self.settings.save(provider_id, request)

    def delete_provider_settings(self, provider_id: str) -> dict[str, Any]:
        return self.settings.delete(provider_id)

    def start_run(
        self, session_id: str, *, prompt: str, max_steps: int
    ) -> dict[str, Any]:
        session = self.sessions.require_row(session_id)
        return self.runs.start(session, prompt=prompt, max_steps=max_steps)

    def get_run(self, run_id: str) -> dict[str, Any]:
        return self.runs.get(run_id)

    def cancel_run(self, run_id: str) -> str:
        return self.runs.cancel(run_id)

    def events_after(self, run_id: str, seq: int) -> list[Event]:
        return self.runs.events_after(run_id, seq)

    def trace(self, run_id: str) -> dict[str, Any]:
        return self.traces.get(run_id)

    def answer_approval(
        self, run_id: str, request_id: str, outcome: ApprovalOutcome
    ) -> None:
        self.runs.answer_approval(run_id, request_id, outcome)
