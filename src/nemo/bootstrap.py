"""The only place that knows every concrete adapter.

Assembly lives here so that ``core/`` can stay vendor-neutral and adapter-free:
configuration decides *what* to use, this module decides *which class* provides it.
"""

from collections.abc import Mapping
from contextlib import asynccontextmanager
from pathlib import Path
import webbrowser

from nemo.adapters.mcp import connect_tools, McpConnection
from nemo.adapters.mcp_oauth import McpOAuthManager
from nemo.adapters.protocols.openai_compatible import OpenAICompatibleAdapter
from nemo.adapters.persistence import SQLiteRepository
from nemo.adapters.tools.filesystem import (
    EditFileTool,
    ListDirTool,
    ReadFileTool,
    WriteFileTool,
)
from nemo.adapters.tools.search import SearchWorkspaceTool
from nemo.adapters.memory import FileMemoryStore
from nemo.adapters.tools.memory import SearchMemoryTool, ReadMemoryTool, SaveMemoryTool, ForgetMemoryTool
from nemo.adapters.tools.history import SearchSessionsTool, ReadSessionTool
from nemo.core.memory import MemoryStore
from nemo.core.skills import SkillCatalog
from nemo.adapters.skills import FileSkillCatalog, FileSkillManagementStore
from nemo.adapters.tools.skills import ListSkillsTool, ReadSkillTool, ReadSkillResourceTool
from nemo.server.repository import Repository
from nemo.adapters.tools.shell import RunShellTool
from nemo.adapters.tools.web import FetchUrlTool, WebSearchTool
from nemo.adapters.httpx_transport import HttpxTransport
from nemo.config.editor import ConfigEditor
from nemo.config.loader import load_config
from nemo.config.secrets import SecretLoader
from nemo.core.contracts.errors import ConfigError
from nemo.core.contracts.model_config import ResolvedModel
from nemo.core.contracts.model_transport import ProtocolAdapter, Transport
from nemo.core.models.client import ModelClient
from nemo.core.models.registry import ModelRegistry
from nemo.core.models.resolver import ModelResolver
from nemo.core.tools.registry import Tool
from nemo.server.application import NemoApplication

#: Protocols implemented as adapters. Presence here is the actual support matrix:
#: the config contract lists every planned protocol so a wrong value fails loudly.
ADAPTERS: dict[str, type[ProtocolAdapter]] = {
    OpenAICompatibleAdapter.protocol: OpenAICompatibleAdapter,
}


BUILTIN_SKILLS_ROOT = Path(__file__).resolve().parent / "prompts" / "skills"


def build_skill_catalog(workspace: Path) -> SkillCatalog:
    return FileSkillCatalog(workspace, Path.home() / ".nemo" / "skills",
                            builtin_root=BUILTIN_SKILLS_ROOT)


def build_skill_tools(catalog: SkillCatalog) -> tuple[Tool, ...]:
    return (ListSkillsTool(catalog), ReadSkillTool(catalog), ReadSkillResourceTool(catalog))


def build_memory_store(config_editor: ConfigEditor | None = None) -> MemoryStore:
    return FileMemoryStore(Path.home() / ".nemo" / "memory", settings=config_editor or ConfigEditor())


def build_local_tools(*, memory_store: MemoryStore | None = None,
                      history_repository: Repository | None = None) -> tuple[Tool, ...]:
    """The Phase 1 tool set, in registration order.

    Order is not cosmetic: the schemas follow it, and the schemas are part of the
    cached prompt prefix (M4). One list, one owner, so the CLI and the example
    cannot drift apart.
    """

    memory_tools = (SearchMemoryTool(memory_store), ReadMemoryTool(memory_store),
                    SaveMemoryTool(memory_store), ForgetMemoryTool(memory_store)) if memory_store else ()
    history_tools = (SearchSessionsTool(history_repository), ReadSessionTool(history_repository)) if history_repository else ()
    return (*memory_tools, *history_tools,
        SearchWorkspaceTool(),
        ReadFileTool(),
        WriteFileTool(),
        EditFileTool(),
        ListDirTool(),
        RunShellTool(),
        WebSearchTool(),
        FetchUrlTool(),
    )


def build_adapter(protocol: str) -> ProtocolAdapter:
    try:
        adapter_class = ADAPTERS[protocol]
    except KeyError:
        implemented = ", ".join(sorted(ADAPTERS)) or "none"
        raise ConfigError(
            f"Protocol '{protocol}' has no adapter yet; implemented: {implemented}"
        ) from None
    return adapter_class()


def resolve_model(
    *,
    config_path: Path | str | None = None,
    run_override: str | None = None,
    session: str | None = None,
    agent: str | None = None,
) -> ResolvedModel:
    registry = ModelRegistry(load_config(config_path))
    return ModelResolver(registry).resolve(
        run_override=run_override, session=session, agent=agent
    )


def build_model_client(
    *,
    config_path: Path | str | None = None,
    run_override: str | None = None,
    session: str | None = None,
    agent: str | None = None,
    transport: Transport | None = None,
    secret_loader: SecretLoader | None = None,
    environ: Mapping[str, str] | None = None,
) -> ModelClient:
    """Assemble a runtime-ready client; raises before any network call if unusable."""

    loader = secret_loader or SecretLoader(environ=environ)
    resolved = resolve_model(
        config_path=config_path,
        run_override=run_override,
        session=session,
        agent=agent,
    )
    return ModelClient(
        resolved=resolved,
        adapter=build_adapter(resolved.protocol),
        transport=transport
        or HttpxTransport(
            proxy=loader.load(resolved.proxy_env) if resolved.proxy_env else None
        ),
        secret=loader.load(resolved.api_key_env),
    )


@asynccontextmanager
async def build_mcp_tools(workspace: Path):
    # No MCP configuration exists before the first model setup.
    config = ConfigEditor().load_optional()
    async with connect_tools(config.mcp_servers if config else {}, workspace, oauth_manager=MCP_OAUTH) as tools:
        yield tools


async def probe_mcp(server_id, config, workspace: Path):
    connection = McpConnection(server_id, config, workspace, oauth_manager=MCP_OAUTH)
    try:
        await connection.start()
        return [{"name": t.remote_name, "model_name": t.name, "description": t.description,
                 "read_only": t.read_only, "subagent_allowed": t.subagent_allowed,
                 "input_schema": t.input_schema} for t in connection.tools]
    finally:
        await connection.close()


MCP_OAUTH = McpOAuthManager(connection_factory=McpConnection, browser_opener=webbrowser.open)


def build_server_application(database_path: Path | str) -> NemoApplication:
    """Compose the production Server application from concrete adapters."""

    repository = SQLiteRepository(database_path)
    config_editor = ConfigEditor()
    memory_store = build_memory_store(config_editor)
    return NemoApplication(
        repository,
        skill_catalog_factory=build_skill_catalog,
        skill_management_store=FileSkillManagementStore(
            Path.home() / ".nemo" / "skills", builtin_root=BUILTIN_SKILLS_ROOT),
        skill_tools_factory=build_skill_tools,
        memory_store=memory_store,
        config_editor=config_editor,
        mcp_tools_factory=build_mcp_tools,
        mcp_probe=probe_mcp,
        mcp_oauth=MCP_OAUTH,
        client_factory=build_model_client,
        tools_factory=lambda: build_local_tools(memory_store=memory_store, history_repository=repository),
    )
