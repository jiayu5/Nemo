"""Application service owning Session and Run lifecycles."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from nemo.adapters.sqlite import ActiveRunError, SQLiteRepository, TERMINAL_STATUSES
from nemo.bootstrap import build_local_tools, build_model_client
from nemo.config.loader import load_config
from nemo.config.secrets import SecretLoader
from nemo.core.context.builder import ContextBuilder
from nemo.core.contracts.errors import NemoError
from nemo.core.contracts.tools import ExecutionContext
from nemo.core.contracts.types import Event, Message, ModelRequest
from nemo.core.models.registry import ModelRegistry
from nemo.core.models.resolver import ModelResolver
from nemo.core.runtime.agent import AgentRuntime
from nemo.core.session import new_session_id
from nemo.core.tools.approval import (
    ApprovalMode,
    ApprovalOutcome,
    ApprovalPolicy,
    ApprovalRequest,
)
from nemo.core.tools.registry import ToolRegistry
from nemo.prompts.instructions import load_project_instructions, load_user_instructions
from nemo.prompts.local_agent import build_system_prompt
from nemo.server.errors import (
    ApprovalConflictError,
    ApprovalNotFoundError,
    ProviderNotFoundError,
    RunNotFoundError,
    SessionNotFoundError,
)


class AgentService:
    def __init__(
        self,
        repository: SQLiteRepository,
        *,
        client_factory: Callable[..., Any] = build_model_client,
        tools_factory: Callable[[], tuple[Any, ...]] = build_local_tools,
        approval_timeout_seconds: float = 300.0,
        config_loader: Callable[[], Any] = load_config,
        secret_loader_factory: Callable[[], Any] = SecretLoader,
    ) -> None:
        self.repository = repository
        self._client_factory = client_factory
        self._tools_factory = tools_factory
        self._approval_timeout = approval_timeout_seconds
        self._config_loader = config_loader
        self._secret_loader_factory = secret_loader_factory
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._cancel: dict[str, asyncio.Event] = {}
        self._approval_waiters: dict[str, asyncio.Future[ApprovalOutcome]] = {}
        self._steps: dict[str, int] = {}

    async def startup(self) -> list[str]:
        return self.repository.recover_interrupted_runs()

    async def shutdown(self) -> None:
        tasks = list(self._tasks.items())
        for _, task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*(task for _, task in tasks), return_exceptions=True)
        for run_id, _ in tasks:
            self.repository.interrupt_run(run_id)
        for future in self._approval_waiters.values():
            if not future.done():
                future.cancel()
        self.repository.close()

    def create_session(
        self, *, workspace: str, model: str | None, approval_mode: ApprovalMode
    ) -> dict[str, Any]:
        path = Path(workspace).expanduser().resolve()
        if not path.is_dir():
            raise ValueError("workspace must be an existing directory")
        if model is not None:
            ModelRegistry(self._config_loader()).resolve_name(model)
        return self._public_session(
            self.repository.create_session(
                session_id=new_session_id(),
                workspace=path,
                model=model,
                approval_mode=approval_mode.value,
                user_instructions=load_user_instructions(),
                project_instructions=load_project_instructions(path),
            )
        )

    def list_sessions(self) -> list[dict[str, Any]]:
        return [self._public_session(row) for row in self.repository.list_sessions()]

    def get_session(self, session_id: str) -> dict[str, Any]:
        row = self.repository.get_session(session_id)
        if row is None:
            raise SessionNotFoundError(session_id)
        return self._public_session(row)

    def update_session(
        self,
        session_id: str,
        *,
        model: str | None = None,
        approval_mode: ApprovalMode | None = None,
    ) -> dict[str, Any]:
        if self.repository.get_session(session_id) is None:
            raise SessionNotFoundError(session_id)
        if model is not None:
            ModelRegistry(self._config_loader()).resolve_name(model)
        row = self.repository.update_session(
            session_id,
            model=model,
            approval_mode=approval_mode.value if approval_mode is not None else None,
        )
        return self._public_session(row)  # type: ignore[arg-type]

    def set_session_model(self, session_id: str, model: str) -> dict[str, Any]:
        if self.repository.get_session(session_id) is None:
            raise SessionNotFoundError(session_id)
        ModelRegistry(self._config_loader()).resolve_name(model)
        row = self.repository.update_session(session_id, model=model)
        return self._public_session(row)  # type: ignore[arg-type]

    def session_messages(self, session_id: str) -> tuple[Message, ...]:
        if self.repository.get_session(session_id) is None:
            raise SessionNotFoundError(session_id)
        return self.repository.messages(session_id)

    def session_runs(self, session_id: str) -> list[dict[str, Any]]:
        if self.repository.get_session(session_id) is None:
            raise SessionNotFoundError(session_id)
        return [self._public_run(row) for row in self.repository.list_runs(session_id)]

    def models(self) -> list[dict[str, Any]]:
        config = self._config_loader()
        registry = ModelRegistry(config)
        resolver = ModelResolver(registry)
        selections: list[tuple[str, str]] = []
        selections.extend((name, "profile") for name in config.profiles)
        selections.extend(
            (name, "alias") for name in config.aliases if name not in config.profiles
        )
        selections.extend(
            (name, "model")
            for name in config.models
            if name not in config.profiles and name not in config.aliases
        )
        result = []
        for selection, kind in selections:
            resolved = resolver.resolve(run_override=selection)
            result.append(
                {
                    "selection": selection,
                    "kind": kind,
                    "model_name": resolved.model_name,
                    "model_id": resolved.model_id,
                    "provider_id": config.models[resolved.model_name].provider,
                    "protocol": resolved.protocol,
                    "capabilities": sorted(resolved.capabilities),
                    "is_default": selection == config.default,
                }
            )
        return result

    def providers(self) -> list[dict[str, Any]]:
        config = self._config_loader()
        secret_loader = self._secret_loader_factory()
        return [
            {
                "provider_id": provider_id,
                "protocol": provider.protocol,
                "base_url": self._public_base_url(provider.base_url),
                "api_key_env": provider.api_key_env,
                "secret_configured": secret_loader.source(provider.api_key_env) is not None,
                "timeout_seconds": provider.timeout_seconds,
                "models": sorted(
                    name
                    for name, model in config.models.items()
                    if model.provider == provider_id
                ),
            }
            for provider_id, provider in config.providers.items()
        ]

    async def test_provider(
        self, provider_id: str, selection: str | None = None
    ) -> dict[str, Any]:
        config = self._config_loader()
        if provider_id not in config.providers:
            raise ProviderNotFoundError(provider_id)
        registry = ModelRegistry(config)
        resolver = ModelResolver(registry)
        chosen = selection or self._selection_for_provider(provider_id, registry)
        resolved = resolver.resolve(run_override=chosen)
        if registry.model(resolved.model_name).provider != provider_id:
            raise ValueError("selected model does not belong to this provider")
        client = self._client_factory(run_override=chosen)
        started = perf_counter()
        try:
            await client.generate(
                ModelRequest(
                    messages=(Message(role="user", content="Reply with OK."),),
                    tools=(),
                )
            )
        finally:
            closer = getattr(client, "aclose", None)
            if closer is not None:
                await closer()
        return {
            "status": "ok",
            "provider_id": provider_id,
            "selection": chosen,
            "model_name": resolved.model_name,
            "model_id": resolved.model_id,
            "protocol": resolved.protocol,
            "latency_ms": round((perf_counter() - started) * 1000, 3),
        }

    def start_run(self, session_id: str, *, prompt: str, max_steps: int) -> dict[str, Any]:
        session = self.repository.get_session(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)
        run_id = str(uuid4())
        try:
            row = self.repository.create_run(
                run_id=run_id,
                session_id=session_id,
                prompt=prompt,
                max_steps=max_steps,
            )
        except ActiveRunError:
            raise
        cancel = asyncio.Event()
        self._cancel[run_id] = cancel
        task = asyncio.create_task(
            self._execute(run_id, session, prompt, max_steps, cancel),
            name=f"nemo-run-{run_id}",
        )
        self._tasks[run_id] = task
        task.add_done_callback(lambda _: self._release_run(run_id))
        return self._public_run(row)

    def get_run(self, run_id: str) -> dict[str, Any]:
        row = self.repository.get_run(run_id)
        if row is None:
            raise RunNotFoundError(run_id)
        return self._public_run(row)

    def cancel_run(self, run_id: str) -> str:
        row = self.repository.get_run(run_id)
        if row is None:
            raise RunNotFoundError(run_id)
        if row["status"] in TERMINAL_STATUSES:
            return "already_terminal"
        cancel = self._cancel.get(run_id)
        if cancel is None:
            self.repository.interrupt_run(run_id)
            return "already_terminal"
        cancel.set()
        return "accepted"

    def events_after(self, run_id: str, seq: int) -> list[Event]:
        if self.repository.get_run(run_id) is None:
            raise RunNotFoundError(run_id)
        return self.repository.events_after(run_id, seq)

    def trace(self, run_id: str) -> dict[str, Any]:
        run = self.repository.get_run(run_id)
        if run is None:
            raise RunNotFoundError(run_id)
        events = self.repository.events_after(run_id, 0)
        steps = [
            {
                "step": row["step"],
                "started_at": row["started_at"],
                "finished_at": row["finished_at"],
                "duration_ms": self._duration_ms(
                    row["started_at"], row["finished_at"]
                ),
            }
            for row in self.repository.steps(run_id)
        ]
        tool_calls = [
            {
                key: row[key]
                for key in (
                    "tool_call_id",
                    "step",
                    "name",
                    "summary",
                    "status",
                    "error_code",
                    "started_at",
                    "finished_at",
                )
            }
            | {
                "duration_ms": self._duration_ms(
                    row["started_at"], row["finished_at"]
                )
            }
            for row in self.repository.tool_calls(run_id)
        ]
        return {
            "run": self._public_run(run),
            "duration_ms": self._duration_ms(run.get("started_at"), run.get("finished_at")),
            "steps": steps,
            "model_calls": self._model_calls(events),
            "tool_calls": tool_calls,
            "events": events,
        }

    def answer_approval(
        self, run_id: str, request_id: str, outcome: ApprovalOutcome
    ) -> None:
        approval = self.repository.get_approval(request_id)
        if approval is None or approval["run_id"] != run_id:
            raise ApprovalNotFoundError(request_id)
        future = self._approval_waiters.get(request_id)
        if approval["status"] != "pending" or future is None or future.done():
            raise ApprovalConflictError("approval is no longer pending")
        if not self.repository.answer_approval(request_id, outcome.value):
            raise ApprovalConflictError("approval is no longer pending")
        self.repository.append_event(
            run_id=run_id,
            step=self._steps.get(run_id, 0),
            kind="approval.resolved",
            payload={"request_id": request_id, "outcome": outcome.value},
        )
        future.set_result(outcome)

    async def _execute(
        self,
        run_id: str,
        session: dict[str, Any],
        prompt: str,
        max_steps: int,
        cancel: asyncio.Event,
    ) -> None:
        client = None
        try:
            self.repository.set_run_running(run_id)
            client = self._client_factory(run_override=session["model"])
            resolved = getattr(client, "resolved", None)
            if resolved is not None:
                provider = self._config_loader().models[resolved.model_name].provider
                self.repository.set_run_model(
                    run_id,
                    selection=resolved.selection,
                    model_name=resolved.model_name,
                    model_id=resolved.model_id,
                    protocol=resolved.protocol,
                    provider=provider,
                )
            tools = self._tools_factory()
            approver = self._approver(run_id)
            policy = ApprovalPolicy(ApprovalMode(session["approval_mode"]), approver=approver)
            registry = ToolRegistry(
                tools,
                context=ExecutionContext(workspace=Path(session["workspace"])),
                policy=policy,
            )
            runtime = AgentRuntime(
                client,
                registry,
                ContextBuilder(
                    system_prompt=build_system_prompt(
                        workspace=session["workspace"], tools=(tool.name for tool in tools)
                    ),
                    user_instructions=session["user_instructions"],
                    project_instructions=session["project_instructions"],
                ),
            )

            def persist(event: Event) -> None:
                self._steps[run_id] = event.step
                self.repository.append_runtime_event(event)

            result = await runtime.run(
                prompt,
                max_steps=max_steps,
                cancel=cancel,
                on_event=persist,
                history=self.repository.messages(session["session_id"]),
                run_id=run_id,
            )
            self.repository.finish_run(result)
        except asyncio.CancelledError:
            self.repository.interrupt_run(run_id)
            raise
        except Exception as exc:
            message = (
                exc.public_message
                if isinstance(exc, NemoError)
                else "Server could not start or finish this run"
            )
            self.repository.fail_run(run_id, message)
        finally:
            if client is not None:
                closer = getattr(client, "aclose", None)
                if closer is not None:
                    try:
                        await closer()
                    except Exception:
                        # The run already has a persisted terminal state. A transport
                        # cleanup failure must not turn it into an unhandled task error.
                        pass

    def _approver(self, run_id: str):
        async def approve(request: ApprovalRequest) -> ApprovalOutcome:
            request_id = str(uuid4())
            future: asyncio.Future[ApprovalOutcome] = asyncio.get_running_loop().create_future()
            self._approval_waiters[request_id] = future
            self.repository.create_approval(
                request_id=request_id,
                run_id=run_id,
                tool_name=request.tool_name,
                summary=request.summary,
                reason=request.reason,
                step=self._steps.get(run_id, 0),
            )
            try:
                return await asyncio.wait_for(future, timeout=self._approval_timeout)
            except asyncio.TimeoutError:
                self.repository.answer_approval(request_id, ApprovalOutcome.DENY.value)
                self.repository.append_event(
                    run_id=run_id,
                    step=self._steps.get(run_id, 0),
                    kind="approval.resolved",
                    payload={"request_id": request_id, "outcome": "deny"},
                )
                return ApprovalOutcome.DENY
            except asyncio.CancelledError:
                self.repository.answer_approval(request_id, ApprovalOutcome.DENY.value)
                self.repository.append_event(
                    run_id=run_id,
                    step=self._steps.get(run_id, 0),
                    kind="approval.resolved",
                    payload={"request_id": request_id, "outcome": "deny"},
                )
                raise
            finally:
                self._approval_waiters.pop(request_id, None)

        return approve

    def _release_run(self, run_id: str) -> None:
        self._tasks.pop(run_id, None)
        self._cancel.pop(run_id, None)
        self._steps.pop(run_id, None)

    def _public_session(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "session_id": row["session_id"],
            "workspace": row["workspace"],
            "model": row["model"],
            "approval_mode": row["approval_mode"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "message_count": len(self.repository.messages(row["session_id"])),
        }

    @staticmethod
    def _public_run(row: dict[str, Any]) -> dict[str, Any]:
        return {
            key: row[key]
            for key in (
                "run_id",
                "session_id",
                "status",
                "max_steps",
                "output",
                "error",
                "created_at",
                "started_at",
                "finished_at",
                "model_selection",
                "model_name",
                "model_id",
                "model_protocol",
                "model_provider",
            )
        }

    @staticmethod
    def _selection_for_provider(provider_id: str, registry: ModelRegistry) -> str:
        config = registry.config
        default_name, _ = registry.resolve_name(config.default)
        if registry.model(default_name).provider == provider_id:
            return config.default
        candidates = sorted(
            name for name, model in config.models.items() if model.provider == provider_id
        )
        if not candidates:
            raise ValueError("provider has no configured model")
        return candidates[0]

    @staticmethod
    def _duration_ms(started_at: str | None, finished_at: str | None) -> float | None:
        if not started_at or not finished_at:
            return None
        delta = datetime.fromisoformat(finished_at) - datetime.fromisoformat(started_at)
        return round(delta.total_seconds() * 1000, 3)

    @staticmethod
    def _public_base_url(value: str) -> str:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        if parsed.port is not None:
            host = f"{host}:{parsed.port}"
        return urlunsplit((parsed.scheme, host, parsed.path, "", ""))

    @classmethod
    def _model_calls(cls, events: list[Event]) -> list[dict[str, Any]]:
        pending: dict[int, Event] = {}
        calls: list[dict[str, Any]] = []
        for event in events:
            if event.type == "model.started":
                pending[event.step] = event
            elif event.type == "model.completed" and event.step in pending:
                started = pending.pop(event.step)
                calls.append(
                    {
                        "step": event.step,
                        "started_at": started.timestamp.isoformat(),
                        "finished_at": event.timestamp.isoformat(),
                        "duration_ms": round(
                            (event.timestamp - started.timestamp).total_seconds() * 1000,
                            3,
                        ),
                        "status": "completed",
                        "prompt_tokens": event.payload.get("prompt_tokens"),
                        "cached_prompt_tokens": event.payload.get(
                            "cached_prompt_tokens"
                        ),
                        "completion_tokens": event.payload.get("completion_tokens"),
                    }
                )
        for step, started in sorted(pending.items()):
            calls.append(
                {
                    "step": step,
                    "started_at": started.timestamp.isoformat(),
                    "finished_at": None,
                    "duration_ms": None,
                    "status": "incomplete",
                }
            )
        return sorted(calls, key=lambda item: item["step"])
