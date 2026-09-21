"""Read-only lookup over a validated ``AppConfig``."""

from nemo.core.contracts.errors import UnknownReferenceError
from nemo.core.contracts.model_config import AppConfig, ModelConfig, ProfileConfig, ProviderConfig


class ModelRegistry:
    """Answers "what does this name mean" without touching the network or secrets."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config

    @property
    def config(self) -> AppConfig:
        return self._config

    def provider(self, name: str) -> ProviderConfig:
        try:
            return self._config.providers[name]
        except KeyError:
            raise UnknownReferenceError(f"Unknown provider: {name}") from None

    def model(self, name: str) -> ModelConfig:
        try:
            return self._config.models[name]
        except KeyError:
            raise UnknownReferenceError(f"Unknown model: {name}") from None

    def profile(self, name: str) -> ProfileConfig | None:
        return self._config.profiles.get(name)

    def alias(self, name: str) -> str | None:
        return self._config.aliases.get(name)

    def resolve_name(self, selection: str) -> tuple[str, ProfileConfig | None]:
        """Resolve a profile, alias or model name to ``(model name, profile)``.

        Order is profile, then alias, then literal model name. The loader already
        rejects a name that is both an alias and a profile, so this order can
        never silently pick a different meaning than the user wrote.
        """

        profile = self.profile(selection)
        if profile is not None:
            return profile.model, profile
        alias_target = self.alias(selection)
        if alias_target is not None:
            return alias_target, None
        if selection in self._config.models:
            return selection, None
        raise UnknownReferenceError(f"Unknown profile, alias or model: {selection}")
