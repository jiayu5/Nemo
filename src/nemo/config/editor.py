"""Validated, atomic editing for Nemo model configuration and secret references."""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
import threading
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Literal

from nemo.config.loader import DEFAULT_CONFIG_PATH, load_config, validate_config
from nemo.config.secrets import DEFAULT_SECRET_PATH, SecretLoader
from nemo.core.contracts.errors import ConfigError
from nemo.core.contracts.model_config import AppConfig, ModelConfig, ProviderConfig

SecretAction = Literal["keep", "replace", "delete"]
_ENV_ENTRY = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


class ConfigEditor:
    def __init__(
        self,
        *,
        config_path: Path | str = DEFAULT_CONFIG_PATH,
        secret_path: Path | str = DEFAULT_SECRET_PATH,
        environ: dict[str, str] | None = None,
    ) -> None:
        self.config_path = Path(config_path).expanduser().resolve()
        self.secret_path = Path(secret_path).expanduser().resolve()
        self._environ = dict(os.environ if environ is None else environ)
        self._lock = threading.RLock()

    def load_optional(self) -> AppConfig | None:
        if not self.config_path.exists():
            return None
        return load_config(self.config_path)

    def secret_source(self, name: str | None) -> str | None:
        if not name:
            return None
        return SecretLoader(env_file=self.secret_path, environ=self._environ).source(name)

    def candidate(
        self,
        *,
        provider_id: str,
        provider: ProviderConfig,
        model_name: str,
        model: ModelConfig,
        make_default: bool,
    ) -> AppConfig:
        current = self.load_optional()
        raw: dict[str, Any]
        if current is None:
            raw = {
                "providers": {},
                "models": {},
                "aliases": {},
                "profiles": {},
                "default": model_name,
            }
        else:
            raw = current.model_dump(mode="python")

        existing_provider = raw["providers"].get(provider_id, {})
        provider_data = provider.model_dump(mode="python")
        provider_data["headers"] = existing_provider.get("headers", {})
        raw["providers"][provider_id] = provider_data

        existing_model = raw["models"].get(model_name, {})
        if existing_model and existing_model.get("provider") != provider_id:
            raise ConfigError(
                f"model '{model_name}' already belongs to provider "
                f"'{existing_model['provider']}'"
            )
        model_data = model.model_dump(mode="python")
        model_data["parameters"] = existing_model.get("parameters", {})
        raw["models"][model_name] = model_data
        if make_default or current is None:
            raw["default"] = model_name
        return validate_config(raw, source="Provider Settings draft")

    def without_provider(self, provider_id: str) -> tuple[AppConfig, list[str]]:
        """Build a valid configuration without one provider and its references."""

        current = self.load_optional()
        if current is None or provider_id not in current.providers:
            raise ConfigError("provider not found")
        raw = current.model_dump(mode="python")
        removed_models = [
            name for name, model in current.models.items() if model.provider == provider_id
        ]
        removed = set(removed_models)
        del raw["providers"][provider_id]
        raw["models"] = {
            name: model for name, model in raw["models"].items() if name not in removed
        }
        raw["aliases"] = {
            name: target for name, target in raw["aliases"].items() if target not in removed
        }
        raw["profiles"] = {
            name: profile
            for name, profile in raw["profiles"].items()
            if profile["model"] not in removed
        }
        if not raw["models"]:
            raise ConfigError("cannot delete the last configured model; add another provider first")
        selectable = set(raw["models"]) | set(raw["aliases"]) | set(raw["profiles"])
        if raw["default"] not in selectable:
            raw["default"] = next(iter(raw["models"]))
        return validate_config(raw, source="Provider Settings deletion"), removed_models

    def save(
        self,
        config: AppConfig,
        secret_changes: dict[str, tuple[SecretAction, str | None]],
    ) -> None:
        """Write a validated config and selected secret changes as one operation."""

        config_text = render_toml(config)
        env_text = self._updated_env(secret_changes)
        with self._lock:
            old_config = self._snapshot(self.config_path)
            old_env = self._snapshot(self.secret_path)
            try:
                _atomic_write(self.config_path, config_text, 0o600)
                if env_text is not None:
                    _atomic_write(self.secret_path, env_text, 0o600)
                elif self.secret_path.exists():
                    os.chmod(self.secret_path, 0o600)
            except Exception:
                self._restore(self.config_path, old_config)
                self._restore(self.secret_path, old_env)
                raise

    def preview_secret_changes(
        self, changes: dict[str, tuple[SecretAction, str | None]]
    ) -> bool:
        """Validate secret actions without writing and report whether the file changes."""

        return self._updated_env(changes) is not None

    def _updated_env(
        self, changes: dict[str, tuple[SecretAction, str | None]]
    ) -> str | None:
        actionable = {name: change for name, change in changes.items() if change[0] != "keep"}
        if not actionable:
            return None
        for name, (action, value) in actionable.items():
            if self.secret_source(name) == "environment":
                raise ConfigError(f"{name} comes from the process environment and is read-only")
            if action == "replace":
                if value is None or not value.strip():
                    raise ConfigError(f"{name} requires a non-empty replacement value")
                if "\n" in value or "\r" in value or "\0" in value:
                    raise ConfigError(f"{name} contains unsupported control characters")

        lines = (
            self.secret_path.read_text(encoding="utf-8").splitlines()
            if self.secret_path.exists()
            else []
        )
        output: list[str] = []
        handled: set[str] = set()
        for line in lines:
            match = _ENV_ENTRY.match(line)
            name = match.group(1) if match else None
            if name not in actionable:
                output.append(line)
                continue
            if name in handled:
                continue
            handled.add(name)
            action, value = actionable[name]
            if action == "replace":
                output.append(f"{name}={json.dumps(value, ensure_ascii=False)}")
        for name, (action, value) in actionable.items():
            if name not in handled and action == "replace":
                output.append(f"{name}={json.dumps(value, ensure_ascii=False)}")
        return "\n".join(output) + ("\n" if output else "")

    @staticmethod
    def _snapshot(path: Path) -> tuple[bytes, int] | None:
        if not path.exists():
            return None
        return path.read_bytes(), path.stat().st_mode & 0o777

    @staticmethod
    def _restore(path: Path, snapshot: tuple[bytes, int] | None) -> None:
        if snapshot is None:
            if path.exists():
                path.unlink()
            return
        data, mode = snapshot
        _atomic_write(path, data.decode("utf-8"), mode)


def render_toml(config: AppConfig) -> str:
    """Render the supported AppConfig shape without adding a TOML dependency."""

    lines = [f"default = {_toml_value(config.default)}", ""]
    for name, provider in config.providers.items():
        lines.extend(
            [
                f"[providers.{_toml_key(name)}]",
                f"protocol = {_toml_value(provider.protocol)}",
                f"base_url = {_toml_value(provider.base_url)}",
                f"api_key_env = {_toml_value(provider.api_key_env)}",
            ]
        )
        if provider.proxy_env is not None:
            lines.append(f"proxy_env = {_toml_value(provider.proxy_env)}")
        if provider.headers:
            lines.append(f"headers = {_toml_value(provider.headers)}")
        lines.extend([f"timeout_seconds = {_toml_value(provider.timeout_seconds)}", ""])

    for name, model in config.models.items():
        lines.extend(
            [
                f"[models.{_toml_key(name)}]",
                f"provider = {_toml_value(model.provider)}",
                f"model_id = {_toml_value(model.model_id)}",
            ]
        )
        if model.capabilities:
            lines.append(f"capabilities = {_toml_value(sorted(model.capabilities))}")
        lines.append("")
        if model.parameters:
            lines.append(f"[models.{_toml_key(name)}.parameters]")
            lines.extend(
                f"{_toml_key(key)} = {_toml_value(value)}"
                for key, value in model.parameters.items()
            )
            lines.append("")

    if config.aliases:
        lines.append("[aliases]")
        lines.extend(
            f"{_toml_key(name)} = {_toml_value(target)}"
            for name, target in config.aliases.items()
        )
        lines.append("")

    for name, profile in config.profiles.items():
        lines.extend(
            [
                f"[profiles.{_toml_key(name)}]",
                f"model = {_toml_value(profile.model)}",
                "",
            ]
        )
        if profile.parameters:
            lines.append(f"[profiles.{_toml_key(name)}.parameters]")
            lines.extend(
                f"{_toml_key(key)} = {_toml_value(value)}"
                for key, value in profile.parameters.items()
            )
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _toml_key(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _toml_value(value: Any) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ConfigError("configuration contains a non-finite number")
        return repr(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (list, tuple, set, frozenset)):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        body = ", ".join(
            f"{_toml_key(str(key))} = {_toml_value(item)}" for key, item in value.items()
        )
        return "{ " + body + " }"
    raise ConfigError(f"configuration contains unsupported value type: {type(value).__name__}")


def _atomic_write(path: Path, content: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, mode)
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()
