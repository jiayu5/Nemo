"""FastAPI transport for the local Nemo application service."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse

from nemo.core.contracts.errors import ConfigError, NemoError
from nemo.core.contracts.types import Message
from nemo.server.errors import (
    ApprovalConflictError,
    ApprovalNotFoundError,
    ActiveRunError,
    RunNotFoundError,
    SessionNotFoundError,
    ProviderNotFoundError,
)
from nemo.server.repository import TERMINAL_STATUSES
from nemo.server.schemas import (
    ActionResponse,
    ApprovalAnswerRequest,
    CreateRunRequest,
    CreateSessionRequest,
    ModelOptionResponse,
    ProviderResponse,
    ProviderDeleteResponse,
    ProviderSettingsRequest,
    ProviderSettingsResponse,
    ProviderSettingsResult,
    ProviderTestRequest,
    ProviderTestResponse,
    RunResponse,
    SessionResponse,
    TraceResponse,
    UpdateSessionModelRequest,
    UpdateSessionRequest,
)
from nemo.server.application import NemoApplication
from nemo.server.desktop_access import install_desktop_access


DEFAULT_DATABASE_PATH = Path.home() / ".nemo" / "nemo.db"


def create_app(
    *,
    database_path: Path | str | None = None,
    service: NemoApplication | None = None,
    desktop_token: str | None = None,
) -> FastAPI:
    owned_service = service is None
    if service is None:
        from nemo.bootstrap import build_server_application

        agent_service = build_server_application(database_path or DEFAULT_DATABASE_PATH)
    else:
        agent_service = service

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.service = agent_service
        await agent_service.startup()
        try:
            yield
        finally:
            if owned_service:
                await agent_service.shutdown()

    app = FastAPI(title="Nemo Server", version="0.1.0", lifespan=lifespan)
    if desktop_token is not None:
        install_desktop_access(app, desktop_token)

    @app.exception_handler(RequestValidationError)
    async def safe_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        del request
        details = [
            {
                "location": ".".join(str(part) for part in error["loc"]),
                "message": error["msg"],
                "type": error["type"],
            }
            for error in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": details})

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(
        "/sessions", response_model=SessionResponse, status_code=status.HTTP_201_CREATED
    )
    async def create_session(body: CreateSessionRequest) -> dict:
        try:
            return agent_service.create_session(
                workspace=body.workspace,
                model=body.model,
                approval_mode=body.approval_mode,
            )
        except ConfigError as exc:
            raise HTTPException(status_code=422, detail=exc.public_message) from None

    @app.get("/sessions", response_model=list[SessionResponse])
    async def list_sessions() -> list[dict]:
        return agent_service.list_sessions()

    @app.get("/sessions/{session_id}", response_model=SessionResponse)
    async def get_session(session_id: str) -> dict:
        try:
            return agent_service.get_session(session_id)
        except SessionNotFoundError:
            raise HTTPException(status_code=404, detail="session not found") from None

    @app.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_session(session_id: str) -> None:
        try:
            agent_service.delete_session(session_id)
        except SessionNotFoundError:
            raise HTTPException(status_code=404, detail="session not found") from None
        except ActiveRunError:
            raise HTTPException(
                status_code=409, detail="session has an active run"
            ) from None

    @app.patch("/sessions/{session_id}", response_model=SessionResponse)
    async def update_session(session_id: str, body: UpdateSessionRequest) -> dict:
        try:
            return agent_service.update_session(
                session_id, model=body.model, approval_mode=body.approval_mode
            )
        except SessionNotFoundError:
            raise HTTPException(status_code=404, detail="session not found") from None
        except ConfigError as exc:
            raise HTTPException(status_code=422, detail=exc.public_message) from None

    @app.put("/sessions/{session_id}/model", response_model=SessionResponse)
    async def set_session_model(
        session_id: str, body: UpdateSessionModelRequest
    ) -> dict:
        try:
            return agent_service.set_session_model(session_id, body.model)
        except SessionNotFoundError:
            raise HTTPException(status_code=404, detail="session not found") from None
        except ConfigError as exc:
            raise HTTPException(status_code=422, detail=exc.public_message) from None

    @app.get("/sessions/{session_id}/messages", response_model=list[Message])
    async def session_messages(session_id: str) -> tuple[Message, ...]:
        try:
            return agent_service.session_messages(session_id)
        except SessionNotFoundError:
            raise HTTPException(status_code=404, detail="session not found") from None

    @app.get("/sessions/{session_id}/runs", response_model=list[RunResponse])
    async def session_runs(session_id: str) -> list[dict]:
        try:
            return agent_service.session_runs(session_id)
        except SessionNotFoundError:
            raise HTTPException(status_code=404, detail="session not found") from None

    @app.get("/models", response_model=list[ModelOptionResponse])
    async def models() -> list[dict]:
        try:
            return agent_service.models()
        except ConfigError as exc:
            raise HTTPException(status_code=503, detail=exc.public_message) from None

    @app.get("/providers", response_model=list[ProviderResponse])
    async def providers() -> list[dict]:
        try:
            return agent_service.providers()
        except ConfigError as exc:
            raise HTTPException(status_code=503, detail=exc.public_message) from None

    @app.post(
        "/providers/{provider_id}/test", response_model=ProviderTestResponse
    )
    async def test_provider(
        provider_id: str, body: ProviderTestRequest
    ) -> dict:
        try:
            return await agent_service.test_provider(provider_id, body.model)
        except ProviderNotFoundError:
            raise HTTPException(status_code=404, detail="provider not found") from None
        except ConfigError as exc:
            raise HTTPException(status_code=422, detail=exc.public_message) from None
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except NemoError as exc:
            raise HTTPException(status_code=502, detail=exc.public_message) from None

    @app.get("/settings/providers", response_model=ProviderSettingsResponse)
    async def provider_settings() -> dict:
        try:
            return agent_service.provider_settings()
        except ConfigError as exc:
            raise HTTPException(status_code=422, detail=exc.public_message) from None

    @app.post(
        "/settings/providers/{provider_id}/validate",
        response_model=ProviderSettingsResult,
    )
    async def validate_provider_settings(
        provider_id: str, body: ProviderSettingsRequest
    ) -> dict:
        try:
            return agent_service.validate_provider_settings(provider_id, body)
        except ConfigError as exc:
            raise HTTPException(status_code=422, detail=exc.public_message) from None

    @app.put(
        "/settings/providers/{provider_id}",
        response_model=ProviderSettingsResult,
    )
    async def save_provider_settings(
        provider_id: str, body: ProviderSettingsRequest
    ) -> dict:
        try:
            return agent_service.save_provider_settings(provider_id, body)
        except ConfigError as exc:
            raise HTTPException(status_code=422, detail=exc.public_message) from None
        except OSError:
            raise HTTPException(status_code=500, detail="could not save configuration") from None

    @app.delete(
        "/settings/providers/{provider_id}",
        response_model=ProviderDeleteResponse,
    )
    async def delete_provider_settings(provider_id: str) -> dict:
        try:
            return agent_service.delete_provider_settings(provider_id)
        except ConfigError as exc:
            status_code = 404 if exc.public_message == "provider not found" else 409
            raise HTTPException(status_code=status_code, detail=exc.public_message) from None
        except OSError:
            raise HTTPException(status_code=500, detail="could not save configuration") from None

    @app.post(
        "/sessions/{session_id}/runs",
        response_model=RunResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def create_run(session_id: str, body: CreateRunRequest) -> dict:
        try:
            return agent_service.start_run(
                session_id, prompt=body.prompt, max_steps=body.max_steps
            )
        except SessionNotFoundError:
            raise HTTPException(status_code=404, detail="session not found") from None
        except ActiveRunError:
            raise HTTPException(
                status_code=409, detail="session already has an active run"
            ) from None

    @app.get("/runs/{run_id}", response_model=RunResponse)
    async def get_run(run_id: str) -> dict:
        try:
            return agent_service.get_run(run_id)
        except RunNotFoundError:
            raise HTTPException(status_code=404, detail="run not found") from None

    @app.get("/runs/{run_id}/trace", response_model=TraceResponse)
    async def trace(run_id: str) -> dict:
        try:
            return agent_service.trace(run_id)
        except RunNotFoundError:
            raise HTTPException(status_code=404, detail="run not found") from None

    @app.post("/runs/{run_id}/cancel", response_model=ActionResponse)
    async def cancel_run(run_id: str) -> dict[str, str]:
        try:
            return {"status": agent_service.cancel_run(run_id)}
        except RunNotFoundError:
            raise HTTPException(status_code=404, detail="run not found") from None

    @app.post(
        "/runs/{run_id}/approvals/{request_id}", response_model=ActionResponse
    )
    async def answer_approval(
        run_id: str, request_id: str, body: ApprovalAnswerRequest
    ) -> dict[str, str]:
        try:
            agent_service.answer_approval(run_id, request_id, body.outcome)
            return {"status": "answered"}
        except ApprovalNotFoundError:
            raise HTTPException(status_code=404, detail="approval not found") from None
        except ApprovalConflictError:
            raise HTTPException(status_code=409, detail="approval is no longer pending") from None

    @app.get("/runs/{run_id}/events")
    async def stream_events(
        run_id: str,
        request: Request,
        after: int = Query(default=0, ge=0),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        try:
            cursor = max(after, int(last_event_id or 0))
        except ValueError:
            raise HTTPException(status_code=400, detail="Last-Event-ID must be an integer")
        try:
            agent_service.get_run(run_id)
        except RunNotFoundError:
            raise HTTPException(status_code=404, detail="run not found") from None
        return StreamingResponse(
            _sse(agent_service, run_id, cursor, request),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


async def _sse(
    service: NemoApplication, run_id: str, cursor: int, request: Request
) -> AsyncIterator[str]:
    idle_ticks = 0
    while True:
        if await request.is_disconnected():
            return
        events = service.events_after(run_id, cursor)
        for event in events:
            cursor = event.seq
            data = event.model_dump(mode="json")
            yield (
                f"id: {event.seq}\n"
                f"event: {event.type}\n"
                f"data: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"
            )
        run = service.get_run(run_id)
        if run["status"] in TERMINAL_STATUSES and not service.events_after(run_id, cursor):
            return
        idle_ticks += 1
        if idle_ticks % 150 == 0:
            yield ": keep-alive\n\n"
        await asyncio.sleep(0.1)
