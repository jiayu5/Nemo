"""The one object the runtime talks to when it needs a model."""

from nemo.core.contracts.errors import CapabilityError
from nemo.core.contracts.model_config import ResolvedModel
from nemo.core.contracts.model_transport import ProtocolAdapter, Transport
from nemo.core.contracts.secrets import SecretValue
from nemo.core.contracts.types import ModelRequest, ModelResponse


class ModelClient:
    """Satisfies the runtime's ``Model`` protocol: ``async generate(request)``."""

    def __init__(
        self,
        *,
        resolved: ResolvedModel,
        adapter: ProtocolAdapter,
        transport: Transport,
        secret: SecretValue,
    ) -> None:
        if adapter.protocol != resolved.protocol:
            raise ValueError(
                f"Adapter speaks '{adapter.protocol}' but the resolved model needs "
                f"'{resolved.protocol}'"
            )
        self.resolved = resolved
        self._adapter = adapter
        self._transport = transport
        self._secret = secret

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self._require_capabilities(request)
        encoded = self._adapter.encode_request(request, self.resolved, self._secret)
        response = await self._transport.post(
            encoded.url,
            headers=encoded.headers,
            json=encoded.body,
            timeout=self.resolved.timeout_seconds,
        )
        if response.status >= 400:
            raise self._adapter.decode_error(response, self._secret)
        return self._adapter.decode_response(response, self._secret)

    async def aclose(self) -> None:
        """Close an owned transport when the outer host ends this client."""

        closer = getattr(self._transport, "aclose", None)
        if closer is not None:
            await closer()

    def _require_capabilities(self, request: ModelRequest) -> None:
        if request.tools and "tool_calling" not in self.resolved.capabilities:
            raise CapabilityError(
                f"Model '{self.resolved.model_name}' does not declare the "
                "'tool_calling' capability, but tools were provided"
            )
