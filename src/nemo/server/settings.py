"""Provider Settings application service."""

from __future__ import annotations

import threading
from typing import Any

from pydantic import ValidationError

from nemo.config.editor import ConfigEditor
from nemo.core.contracts.errors import ConfigError
from nemo.core.contracts.model_config import ModelConfig, ProviderConfig
from nemo.server.catalog import CatalogService
from nemo.server.schemas import ProviderSettingsRequest


class SettingsService:
    def __init__(self, editor: ConfigEditor) -> None:
        self.editor = editor
        self._lock = threading.RLock()

    def list(self) -> dict[str, Any]:
        config = self.editor.load_optional()
        if config is None:
            return {"config_exists": False, "default": None, "providers": []}
        return {
            "config_exists": True,
            "default": config.default,
            "providers": [
                self._provider_view(provider_id, config)
                for provider_id in config.providers
            ],
        }

    def validate(self, provider_id: str, request: ProviderSettingsRequest) -> dict[str, Any]:
        with self._lock:
            candidate, changes = self._candidate(provider_id, request)
            self.editor.preview_secret_changes(changes)
            return {
                "status": "valid",
                "provider": self._provider_view(provider_id, candidate),
                "writes": self._writes(changes),
            }

    def save(self, provider_id: str, request: ProviderSettingsRequest) -> dict[str, Any]:
        with self._lock:
            candidate, changes = self._candidate(provider_id, request)
            self.editor.preview_secret_changes(changes)
            self.editor.save(candidate, changes)
            return {
                "status": "saved",
                "provider": self._provider_view(provider_id, candidate),
                "writes": self._writes(changes),
            }

    def delete(self, provider_id: str) -> dict[str, Any]:
        with self._lock:
            candidate, removed_models = self.editor.without_provider(provider_id)
            self.editor.save(candidate, {})
            return {
                "status": "deleted",
                "provider_id": provider_id,
                "removed_models": removed_models,
                "default": candidate.default,
            }

    def _candidate(self, provider_id: str, request: ProviderSettingsRequest):
        if not provider_id or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for character in provider_id):
            raise ConfigError("provider_id may contain only letters, numbers, dot, dash and underscore")
        if request.proxy_env is None and request.proxy.action != "keep":
            raise ConfigError("proxy action requires proxy_env")
        if request.proxy_env == request.api_key_env:
            raise ConfigError("proxy_env and api_key_env must use different variables")
        try:
            provider = ProviderConfig(
                protocol=request.protocol,
                base_url=request.base_url,
                api_key_env=request.api_key_env,
                proxy_env=request.proxy_env,
                timeout_seconds=request.timeout_seconds,
            )
            model = ModelConfig(
                provider=provider_id,
                model_id=request.model_id,
                capabilities=(
                    frozenset({"tool_calling"})
                    if request.tool_calling
                    else frozenset()
                ),
            )
        except ValidationError as exc:
            issue = exc.errors()[0]
            location = ".".join(str(part) for part in issue["loc"])
            raise ConfigError(f"{location}: {issue['msg']}") from None
        candidate = self.editor.candidate(
            provider_id=provider_id,
            provider=provider,
            model_name=request.model_name,
            model=model,
            make_default=request.make_default,
        )
        changes = {
            request.api_key_env: (request.api_key.action, request.api_key.value),
        }
        if request.proxy_env:
            changes[request.proxy_env] = (request.proxy.action, request.proxy.value)
        return candidate, changes

    def _provider_view(self, provider_id: str, config) -> dict[str, Any]:
        provider = config.providers[provider_id]
        return {
            "provider_id": provider_id,
            "protocol": provider.protocol,
            "base_url": CatalogService._public_base_url(provider.base_url),
            "api_key_env": provider.api_key_env,
            "api_key_source": self.editor.secret_source(provider.api_key_env),
            "proxy_env": provider.proxy_env,
            "proxy_source": self.editor.secret_source(provider.proxy_env),
            "timeout_seconds": provider.timeout_seconds,
            "models": [
                {
                    "model_name": name,
                    "model_id": model.model_id,
                    "capabilities": sorted(model.capabilities),
                    "is_default": self._is_default_model(config, name),
                }
                for name, model in config.models.items()
                if model.provider == provider_id
            ],
        }

    @staticmethod
    def _is_default_model(config, model_name: str) -> bool:
        if config.default == model_name:
            return True
        if config.default in config.aliases:
            return config.aliases[config.default] == model_name
        if config.default in config.profiles:
            return config.profiles[config.default].model == model_name
        return False

    @staticmethod
    def _writes(changes) -> list[str]:
        writes = ["config"]
        if any(action != "keep" for action, _ in changes.values()):
            writes.append("secrets")
        return writes
