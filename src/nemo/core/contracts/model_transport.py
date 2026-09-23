"""The boundary between the model client and whatever performs the call.

``ProtocolAdapter`` turns unified runtime messages into one wire format and back.
``Transport`` performs the I/O. Keeping both behind protocols is what allows the
core to stay ignorant of vendors, HTTP and fixtures alike.
"""

from dataclasses import dataclass
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from typing import Any, Mapping, Protocol

from nemo.core.contracts.errors import NemoError
from nemo.core.contracts.model_config import ResolvedModel
from nemo.core.contracts.secrets import SecretValue
from nemo.core.contracts.types import ModelReasoningDelta, ModelRequest, ModelResponse, ModelTextDelta


@dataclass(frozen=True)
class EncodedRequest:
    """A protocol-ready request. Headers carry credentials, so repr is redacted."""

    url: str
    headers: Mapping[str, str]
    body: Mapping[str, Any]

    def __repr__(self) -> str:
        return f"EncodedRequest(url={self.url!r}, headers=<redacted>, body=<redacted>)"


@dataclass(frozen=True)
class HttpResponse:
    """Status plus decoded JSON body.

    ``body`` is ``None`` when the payload was not JSON; ``text`` then keeps a
    short excerpt so error paths can still explain themselves. Providers are not
    required to answer errors in JSON — some answer with plain prose.
    """

    status: int
    body: Any = None
    text: str = ""


@dataclass(frozen=True)
class HttpStream:
    status: int
    lines: AsyncIterator[str]


class Transport(Protocol):
    async def post(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        json: Mapping[str, Any],
        timeout: float,
    ) -> HttpResponse: ...

    def stream_post(
        self, url: str, *, headers: Mapping[str, str],
        json: Mapping[str, Any], timeout: float,
    ) -> AbstractAsyncContextManager[HttpStream]: ...


class ProtocolAdapter(Protocol):
    protocol: str

    def encode_request(
        self, request: ModelRequest, resolved: ResolvedModel, secret: SecretValue
    ) -> EncodedRequest: ...

    def decode_response(self, response: HttpResponse, secret: SecretValue) -> ModelResponse: ...

    def decode_stream(
        self, lines: AsyncIterator[str]
    ) -> AsyncIterator[ModelTextDelta | ModelReasoningDelta | ModelResponse]: ...

    def decode_error(self, response: HttpResponse, secret: SecretValue) -> NemoError: ...
