"""Load and validate ``~/.nemo/config.toml``.

Validation happens in two passes: Pydantic rejects malformed fields, then this
module checks that every reference between sections resolves. A configuration
that loads is a configuration the runtime can actually assemble.
"""

import tomllib
from pathlib import Path

from pydantic import ValidationError

from nemo.core.contracts.errors import ConfigError, UnknownReferenceError
from nemo.core.contracts.model_config import (
    AppConfig,
    ModelConfig,
    RESERVED_MODEL_PARAMETERS,
)

DEFAULT_CONFIG_PATH = Path.home() / ".nemo" / "config.toml"


def load_config(path: Path | str | None = None) -> AppConfig:
    config_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not config_path.exists():
        raise ConfigError(f"Config file not found: {config_path}")
    try:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid TOML in {config_path}: {exc}") from None
    return validate_config(raw, source=str(config_path))


def validate_config(raw: object, *, source: str = "configuration") -> AppConfig:
    """Validate parsed configuration data and all cross references."""

    try:
        config = AppConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"Invalid config in {source}: {_first_issue(exc)}") from None
    _check_references(config)
    return config


def _first_issue(exc: ValidationError) -> str:
    issue = exc.errors()[0]
    location = ".".join(str(part) for part in issue["loc"]) or "<root>"
    return f"{location}: {issue['msg']}"


def _check_references(config: AppConfig) -> None:
    for name, model in config.models.items():
        _require_provider(config, name, model)
        _reject_reserved_parameters(f"models.{name}.parameters", model.parameters)
    for name, target in config.aliases.items():
        if target not in config.models:
            raise UnknownReferenceError(f"aliases.{name} points at unknown model: {target}")
        if name in config.profiles:
            raise UnknownReferenceError(
                f"'{name}' is defined as both an alias and a profile; names must be unambiguous"
            )
    for name, profile in config.profiles.items():
        if profile.model not in config.models:
            raise UnknownReferenceError(
                f"profiles.{name}.model points at unknown model: {profile.model}"
            )
        _reject_reserved_parameters(f"profiles.{name}.parameters", profile.parameters)
    if not _is_selectable(config, config.default):
        raise UnknownReferenceError(
            f"default points at unknown profile, alias or model: {config.default}"
        )


def _require_provider(config: AppConfig, model_name: str, model: ModelConfig) -> None:
    if model.provider not in config.providers:
        raise UnknownReferenceError(
            f"models.{model_name}.provider points at unknown provider: {model.provider}"
        )


def _reject_reserved_parameters(location: str, parameters: dict[str, object]) -> None:
    clashes = sorted(RESERVED_MODEL_PARAMETERS.intersection(parameters))
    if clashes:
        raise ConfigError(f"{location} must not set adapter-owned keys: {', '.join(clashes)}")


def _is_selectable(config: AppConfig, name: str) -> bool:
    return name in config.profiles or name in config.aliases or name in config.models


__all__ = ["DEFAULT_CONFIG_PATH", "load_config", "validate_config"]
