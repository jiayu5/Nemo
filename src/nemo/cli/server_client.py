"""HTTP/SSE client used by the CLI after M4c."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from nemo.core.contracts.types import Event
from nemo.core.tools.approval import ApprovalMode, ApprovalOutcome


class ServerClientError(RuntimeError):
    pass


class ServerClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(30.0, read=None),
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self._client.aclose()

    async def list_sessions(self) -> list[dict]:
        return await self._json("GET", "/sessions")

    async def get_session(self, session_id: str) -> dict:
        return await self._json("GET", f"/sessions/{session_id}")

    async def create_session(
        self, *, workspace: str, model: str | None, approval_mode: ApprovalMode
    ) -> dict:
        return await self._json(
            "POST",
            "/sessions",
            json={
                "workspace": workspace,
                "model": model,
                "approval_mode": approval_mode.value,
            },
        )

    async def update_mode(self, session_id: str, mode: ApprovalMode) -> dict:
        return await self._json(
            "PATCH",
            f"/sessions/{session_id}",
            json={"approval_mode": mode.value},
        )

    async def start_run(self, session_id: str, *, prompt: str, max_steps: int) -> dict:
        return await self._json(
            "POST",
            f"/sessions/{session_id}/runs",
            json={"prompt": prompt, "max_steps": max_steps},
        )

    async def get_run(self, run_id: str) -> dict:
        return await self._json("GET", f"/runs/{run_id}")

    async def cancel_run(self, run_id: str) -> dict:
        return await self._json("POST", f"/runs/{run_id}/cancel")

    async def answer_approval(
        self, run_id: str, request_id: str, outcome: ApprovalOutcome
    ) -> dict:
        return await self._json(
            "POST",
            f"/runs/{run_id}/approvals/{request_id}",
            json={"outcome": outcome.value},
        )

    async def events(self, run_id: str, *, after: int = 0) -> AsyncIterator[Event]:
        try:
            async with self._client.stream(
                "GET", f"/runs/{run_id}/events", params={"after": after}
            ) as response:
                await self._raise(response)
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        yield Event.model_validate_json(line[6:])
        except httpx.HTTPError as exc:
            raise ServerClientError(f"event stream failed: {type(exc).__name__}") from None

    async def _json(self, method: str, path: str, **kwargs):
        try:
            response = await self._client.request(method, path, **kwargs)
            await self._raise(response)
            return response.json()
        except ServerClientError:
            raise
        except httpx.HTTPError as exc:
            raise ServerClientError(
                f"cannot reach Nemo Server at {self.base_url}: {type(exc).__name__}"
            ) from None

    @staticmethod
    async def _raise(response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        try:
            detail = response.json().get("detail")
        except Exception:
            detail = None
        raise ServerClientError(detail or f"Server returned HTTP {response.status_code}")
