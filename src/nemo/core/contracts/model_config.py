"""Configuration and resolution contracts for the model system.

Provider brand names never appear here; only protocol identifiers, which are
the stable contract between configuration and adapters.
"""

from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator

from nemo.core.contracts.types import Contract
from nemo.core.contracts.mcp import McpServerConfig

ProtocolName = Literal["openai_responses", "openai_compatible", "anthropic_messages"]
Capability = Literal["tool_calling", "streaming", "image_input"]
SelectionSource = Literal["run", "session", "agent", "default"]

NonEmptyStr = Annotated[str, StringConstraints(min_length=1)]
EnvName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]

#: Parameters a caller may not set: the adapter owns them.
RESERVED_MODEL_PARAMETERS = frozenset({"model", "messages", "tools", "stream"})


class ProviderConfig(Contract):
    """Where a service lives and how to speak to it. Never holds the key itself."""

    protocol: ProtocolName
    base_url: NonEmptyStr
    api_key_env: EnvName
    proxy_env: EnvName | None = None
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
    context_window_tokens: int | None = Field(default=None, ge=1024)
    response_reserve_tokens: int = Field(default=2048, ge=1)
    parameters: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reserve_fits_window(self):
        if self.context_window_tokens is not None and self.response_reserve_tokens >= self.context_window_tokens:
            raise ValueError("response_reserve_tokens must be smaller than context_window_tokens")
        return self


class ProfileConfig(Contract):
    """A named model selection with parameter presets."""

    model: NonEmptyStr
    parameters: dict[str, Any] = Field(default_factory=dict)


class SubagentConfig(Contract):
    """User defaults and hard caps for one parent run."""

    default_max_steps: int = Field(default=15, ge=1, strict=True)
    max_steps: int = Field(default=40, ge=1, strict=True)
    max_model_calls: int = Field(default=160, ge=1, strict=True)

    @model_validator(mode="after")
    def validate_limits(self):
        if self.default_max_steps > self.max_steps:
            raise ValueError("default_max_steps must not exceed max_steps")
        if self.max_model_calls <= self.max_steps:
            raise ValueError("max_model_calls must exceed max_steps to leave room for the parent")
        return self


class AppConfig(Contract):
    mcp_servers: dict[str, McpServerConfig] = Field(default_factory=dict)
    memory_enabled: bool = True
    subagents: SubagentConfig = Field(default_factory=SubagentConfig)
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
    proxy_env: str | None = None
    capabilities: frozenset[Capability] = frozenset()
    context_window_tokens: int | None = None
    response_reserve_tokens: int = 2048
    parameters: dict[str, Any] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float = 60.0
