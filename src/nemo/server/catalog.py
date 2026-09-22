"""Model and Provider catalog application service."""

from collections.abc import Callable
from time import perf_counter
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from nemo.config.loader import load_config
from nemo.config.secrets import SecretLoader
from nemo.core.contracts.types import Message, ModelRequest
from nemo.core.models.registry import ModelRegistry
from nemo.core.models.resolver import ModelResolver
from nemo.server.errors import ProviderNotFoundError


class CatalogService:
    def __init__(
        self,
        *,
        client_factory: Callable[..., Any],
        config_loader: Callable[[], Any] = load_config,
        secret_loader_factory: Callable[[], Any] = SecretLoader,
    ) -> None:
        self._client_factory = client_factory
        self._config_loader = config_loader
        self._secret_loader_factory = secret_loader_factory

    def models(self) -> list[dict[str, Any]]:
        config = self._config_loader()
        registry = ModelRegistry(config)
        resolver = ModelResolver(registry)
        selections: list[tuple[str, str]] = []
        selections.extend((name, "profile") for name in config.profiles)
        selections.extend(
            (name, "alias") for name in config.aliases if name not in config.profiles
        )
        selections.extend(
            (name, "model")
            for name in config.models
            if name not in config.profiles and name not in config.aliases
        )
        return [
            self._model_view(selection, kind, config, resolver)
            for selection, kind in selections
        ]

    def providers(self) -> list[dict[str, Any]]:
        config = self._config_loader()
        secret_loader = self._secret_loader_factory()
        return [
            {
                "provider_id": provider_id,
                "protocol": provider.protocol,
                "base_url": self._public_base_url(provider.base_url),
                "api_key_env": provider.api_key_env,
                "secret_configured": secret_loader.source(provider.api_key_env) is not None,
                "timeout_seconds": provider.timeout_seconds,
                "models": sorted(
                    name
                    for name, model in config.models.items()
                    if model.provider == provider_id
                ),
            }
            for provider_id, provider in config.providers.items()
        ]

    async def test_provider(
        self, provider_id: str, selection: str | None = None
    ) -> dict[str, Any]:
        config = self._config_loader()
        if provider_id not in config.providers:
            raise ProviderNotFoundError(provider_id)
        registry = ModelRegistry(config)
        resolver = ModelResolver(registry)
        chosen = selection or self._selection_for_provider(provider_id, registry)
        resolved = resolver.resolve(run_override=chosen)
        if registry.model(resolved.model_name).provider != provider_id:
            raise ValueError("selected model does not belong to this provider")
        client = self._client_factory(run_override=chosen)
        started = perf_counter()
        try:
            await client.generate(
                ModelRequest(
                    messages=(Message(role="user", content="Reply with OK."),),
                    tools=(),
                )
            )
        finally:
            closer = getattr(client, "aclose", None)
            if closer is not None:
                await closer()
        return {
            "status": "ok",
            "provider_id": provider_id,
            "selection": chosen,
            "model_name": resolved.model_name,
            "model_id": resolved.model_id,
            "protocol": resolved.protocol,
            "latency_ms": round((perf_counter() - started) * 1000, 3),
        }

    @staticmethod
    def _model_view(selection, kind, config, resolver) -> dict[str, Any]:
        resolved = resolver.resolve(run_override=selection)
        return {
            "selection": selection,
            "kind": kind,
            "model_name": resolved.model_name,
            "model_id": resolved.model_id,
            "provider_id": config.models[resolved.model_name].provider,
            "protocol": resolved.protocol,
            "capabilities": sorted(resolved.capabilities),
            "is_default": selection == config.default,
        }

    @staticmethod
    def _selection_for_provider(provider_id: str, registry: ModelRegistry) -> str:
        config = registry.config
        default_name, _ = registry.resolve_name(config.default)
        if registry.model(default_name).provider == provider_id:
            return config.default
        candidates = sorted(
            name for name, model in config.models.items() if model.provider == provider_id
        )
        if not candidates:
            raise ValueError("provider has no configured model")
        return candidates[0]

    @staticmethod
    def _public_base_url(value: str) -> str:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        if parsed.port is not None:
            host = f"{host}:{parsed.port}"
        return urlunsplit((parsed.scheme, host, parsed.path, "", ""))
