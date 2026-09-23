"""The only place that knows every concrete adapter.

Assembly lives here so that ``core/`` can stay vendor-neutral and adapter-free:
configuration decides *what* to use, this module decides *which class* provides it.
"""

from collections.abc import Mapping
from pathlib import Path

from nemo.adapters.protocols.openai_compatible import OpenAICompatibleAdapter
from nemo.adapters.persistence import SQLiteRepository
from nemo.adapters.tools.filesystem import (
    EditFileTool,
    ListDirTool,
    ReadFileTool,
    WriteFileTool,
)
from nemo.adapters.tools.shell import RunShellTool
from nemo.adapters.tools.web import FetchUrlTool, WebSearchTool
from nemo.adapters.httpx_transport import HttpxTransport
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


def build_local_tools() -> tuple[Tool, ...]:
    """The Phase 1 tool set, in registration order.

    Order is not cosmetic: the schemas follow it, and the schemas are part of the
    cached prompt prefix (M4). One list, one owner, so the CLI and the example
    cannot drift apart.
    """

    return (
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


def build_server_application(database_path: Path | str) -> NemoApplication:
    """Compose the production Server application from concrete adapters."""

    return NemoApplication(
        SQLiteRepository(database_path),
        client_factory=build_model_client,
        tools_factory=build_local_tools,
    )
