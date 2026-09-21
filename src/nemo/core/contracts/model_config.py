"""Configuration and resolution contracts for the model system.

Provider brand names never appear here; only protocol identifiers, which are
the stable contract between configuration and adapters.
"""

from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, field_validator

from nemo.core.contracts.types import Contract

ProtocolName = Literal["openai_responses", "openai_compatible", "anthropic_messages"]
Capability = Literal["tool_calling", "streaming"]
SelectionSource = Literal["run", "session", "agent", "default"]

NonEmptyStr = Annotated[str, StringConstraints(min_length=1)]

#: Parameters a caller may not set: the adapter owns them.
RESERVED_MODEL_PARAMETERS = frozenset({"model", "messages", "tools", "stream"})


class ProviderConfig(Contract):
    """Where a service lives and how to speak to it. Never holds the key itself."""

    protocol: ProtocolName
    base_url: NonEmptyStr
    api_key_env: NonEmptyStr
    headers: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=60.0, gt=0)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")
        return value.rstrip("/")


class ModelConfig(Contract):
    """A model identity inside a provider, plus the capabilities it declares."""

    provider: NonEmptyStr
    model_id: NonEmptyStr
    capabilities: frozenset[Capability] = frozenset()
    parameters: dict[str, Any] = Field(default_factory=dict)


class ProfileConfig(Contract):
    """A named model selection with parameter presets."""

    model: NonEmptyStr
    parameters: dict[str, Any] = Field(default_factory=dict)


class AppConfig(Contract):
    providers: dict[str, ProviderConfig]
    models: dict[str, ModelConfig]
    aliases: dict[str, NonEmptyStr] = Field(default_factory=dict)
    profiles: dict[str, ProfileConfig] = Field(default_factory=dict)
    default: NonEmptyStr


class ResolvedModel(Contract):
    """Everything the runtime-facing client needs, with the key reference only.

    ``api_key_env`` is the *name* of the secret, never its value, so this object
    stays safe to serialize into events or traces.
    """

    selection: str
    source: SelectionSource
    model_name: str
    model_id: str
    protocol: ProtocolName
    base_url: str
    api_key_env: str
    capabilities: frozenset[Capability] = frozenset()
    parameters: dict[str, Any] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float = 60.0
