"""HTTP transport implemented with httpx.

The client is created on first use so importing this module never depends on an
running event loop.
"""

from typing import Any, Mapping

import httpx

from nemo.core.contracts.errors import TransportError
from nemo.core.contracts.model_transport import HttpResponse
from nemo.core.contracts.secrets import SecretValue


class HttpxTransport:
    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        proxy: SecretValue | None = None,
    ) -> None:
        self._client = client
        self._owns_client = client is None
        self._proxy = proxy

    async def post(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        json: Mapping[str, Any],
        timeout: float,
    ) -> HttpResponse:
        client = self._ensure_client()
        try:
            response = await client.post(url, headers=headers, json=json, timeout=timeout)
        except httpx.HTTPError as exc:
            # Exception text can include the URL but never the headers, so it is
            # safe to name the failure type without echoing the request body.
            raise TransportError(
                f"Request to {url} failed: {type(exc).__name__}"
            ) from None
        try:
            body: Any = response.json()
        except ValueError:
            body = None
            return HttpResponse(
                status=response.status_code, body=None, text=response.text[:300]
            )
        return HttpResponse(status=response.status_code, body=body)

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                proxy=self._proxy.reveal() if self._proxy is not None else None
            )
        return self._client
