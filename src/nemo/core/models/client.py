"""The one object the runtime talks to when it needs a model."""

import json
import time
from collections.abc import AsyncIterator

from nemo.core.contracts.errors import CapabilityError
from nemo.core.contracts.model_config import ResolvedModel
from nemo.core.contracts.model_transport import HttpResponse, ProtocolAdapter, Transport
from nemo.core.contracts.secrets import SecretValue
from nemo.core.contracts.types import ModelReasoningDelta, ModelRequest, ModelResponse, ModelTextDelta
from nemo.redaction import StreamingTextRedactor


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

    async def stream(
        self, request: ModelRequest
    ) -> AsyncIterator[ModelTextDelta | ModelReasoningDelta | ModelResponse]:
        """Yield redacted, bounded answer/reasoning batches and one final response."""

        self._require_capabilities(request)
        stream_post = getattr(self._transport, "stream_post", None)
        decode_stream = getattr(self._adapter, "decode_stream", None)
        if stream_post is None or decode_stream is None:
            yield await self.generate(request)
            return
        encoded = self._adapter.encode_request(request, self.resolved, self._secret)
        body = {**encoded.body, "stream": True}
        headers = {**encoded.headers, "Accept": "text/event-stream"}
        pending: list[str] = []
        pending_type: type[ModelTextDelta] | type[ModelReasoningDelta] | None = None
        redactor = StreamingTextRedactor()
        last_emit = time.monotonic()
        async with stream_post(
            encoded.url, headers=headers, json=body,
            timeout=self.resolved.timeout_seconds,
        ) as response:
            if response.status >= 400:
                excerpt: list[str] = []
                length = 0
                async for line in response.lines:
                    excerpt.append(line[:300 - length])
                    length += len(excerpt[-1])
                    if length >= 300:
                        break
                raw = "".join(excerpt)
                try:
                    error_body = json.loads(raw)
                except ValueError:
                    error_body = None
                raise self._adapter.decode_error(
                    HttpResponse(response.status, error_body, raw), self._secret
                )
            async for part in decode_stream(response.lines):
                if isinstance(part, (ModelTextDelta, ModelReasoningDelta)):
                    if pending_type is not None and type(part) is not pending_type:
                        tail = redactor.finish()
                        if tail:
                            pending.append(tail)
                        if pending:
                            yield pending_type(text="".join(pending))
                            pending.clear()
                        redactor = StreamingTextRedactor()
                        last_emit = time.monotonic()
                    pending_type = type(part)
                    safe_text = redactor.feed(part.text)
                    if safe_text:
                        pending.append(safe_text)
                    now = time.monotonic()
                    if pending and (now - last_emit >= 0.08 or sum(map(len, pending)) >= 80):
                        yield pending_type(text="".join(pending))
                        pending.clear()
                        last_emit = now
                else:
                    tail = redactor.finish()
                    if tail:
                        pending.append(tail)
                    if pending:
                        yield pending_type(text="".join(pending))
                        pending.clear()
                    yield part

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
