"""Turn a selection plus its precedence level into a concrete resolved model."""

from nemo.core.contracts.model_config import ResolvedModel, SelectionSource
from nemo.core.models.registry import ModelRegistry

#: Highest precedence first, matching the architecture document.
_PRECEDENCE: tuple[tuple[str, SelectionSource], ...] = (
    ("run_override", "run"),
    ("session", "session"),
    ("agent", "agent"),
)


class ModelResolver:
    def __init__(self, registry: ModelRegistry) -> None:
        self.registry = registry

    def resolve(
        self,
        *,
        run_override: str | None = None,
        session: str | None = None,
        agent: str | None = None,
    ) -> ResolvedModel:
        """Resolve in the order run override > session > agent > global default."""

        candidates = {"run_override": run_override, "session": session, "agent": agent}
        for field, source in _PRECEDENCE:
            selection = candidates[field]
            if selection:
                return self._build(selection, source)
        return self._build(self.registry.config.default, "default")

    def _build(self, selection: str, source: SelectionSource) -> ResolvedModel:
        model_name, profile = self.registry.resolve_name(selection)
        model = self.registry.model(model_name)
        provider = self.registry.provider(model.provider)
        parameters = dict(model.parameters)
        if profile is not None:
            parameters.update(profile.parameters)
        return ResolvedModel(
            selection=selection,
            source=source,
            model_name=model_name,
            model_id=model.model_id,
            protocol=provider.protocol,
            base_url=provider.base_url,
            api_key_env=provider.api_key_env,
            capabilities=model.capabilities,
            parameters=parameters,
            headers=dict(provider.headers),
            timeout_seconds=provider.timeout_seconds,
        )
