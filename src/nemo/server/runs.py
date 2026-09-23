"""Run task, cancellation and remote approval coordination."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from nemo.config.loader import load_config
from nemo.core.context.builder import ContextBuilder
from nemo.core.contracts.events import EventType
from nemo.core.contracts.errors import NemoError
from nemo.core.contracts.tools import ExecutionContext
from nemo.core.contracts.types import Event
from nemo.core.runtime.agent import AgentRuntime
from nemo.core.tools.approval import (
    ApprovalMode,
    ApprovalOutcome,
    ApprovalPolicy,
    ApprovalRequest,
)
from nemo.core.tools.registry import ToolRegistry
from nemo.prompts.system_prompt import build_system_prompt
from nemo.server.errors import (
    ApprovalConflictError,
    ApprovalNotFoundError,
    RunNotFoundError,
)
from nemo.server.repository import Repository, TERMINAL_STATUSES
from nemo.server.views import run_view


class RunManager:
    def __init__(
        self,
        repository: Repository,
        *,
        client_factory: Callable[..., Any],
        tools_factory: Callable[[], tuple[Any, ...]] = tuple,
        approval_timeout_seconds: float = 300.0,
        config_loader: Callable[[], Any] = load_config,
    ) -> None:
        self.repository = repository
        self._client_factory = client_factory
        self._tools_factory = tools_factory
        self._approval_timeout = approval_timeout_seconds
        self._config_loader = config_loader
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._cancel: dict[str, asyncio.Event] = {}
        self._approval_waiters: dict[str, asyncio.Future[ApprovalOutcome]] = {}
        self._steps: dict[str, int] = {}
        self._cancel_monitors: dict[str, asyncio.Task[None]] = {}
        self._reaper: asyncio.Task[None] | None = None

    async def startup(self) -> list[str]:
        recovered = self.repository.recover_interrupted_runs()
        if self._reaper is None:
            self._reaper = asyncio.create_task(self._reap_orphans(), name="nemo-run-reaper")
        return recovered

    async def _reap_orphans(self) -> None:
        while True:
            await asyncio.sleep(2)
            self.repository.recover_interrupted_runs()

    async def shutdown(self) -> None:
        if self._reaper is not None:
            self._reaper.cancel()
            await asyncio.gather(self._reaper, return_exceptions=True)
            self._reaper = None
        tasks = list(self._tasks.items())
        for _, task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*(task for _, task in tasks), return_exceptions=True)
        for run_id, _ in tasks:
            self.repository.interrupt_run(run_id)
        for monitor in self._cancel_monitors.values():
            monitor.cancel()
        if self._cancel_monitors:
            await asyncio.gather(*self._cancel_monitors.values(), return_exceptions=True)
        for future in self._approval_waiters.values():
            if not future.done():
                future.cancel()
        self.repository.close()

    def start(
        self,
        session: dict[str, Any],
        *,
        prompt: str,
        max_steps: int,
    ) -> dict[str, Any]:
        run_id = str(uuid4())
        row = self.repository.create_run(
            run_id=run_id,
            session_id=session["session_id"],
            prompt=prompt,
            max_steps=max_steps,
        )
        cancel = asyncio.Event()
        self._cancel[run_id] = cancel
        task = asyncio.create_task(
            self._execute(run_id, session, prompt, max_steps, cancel),
            name=f"nemo-run-{run_id}",
        )
        self._tasks[run_id] = task
        self._cancel_monitors[run_id] = asyncio.create_task(
            self._monitor_cancel(run_id, cancel, task), name=f"nemo-cancel-{run_id}"
        )
        task.add_done_callback(lambda _: self._release(run_id))
        return run_view(row)

    async def _monitor_cancel(
        self, run_id: str, cancel: asyncio.Event, task: asyncio.Task[None]
    ) -> None:
        while not task.done():
            if self.repository.cancel_requested(run_id):
                cancel.set()
                return
            await asyncio.sleep(0.1)

    def get(self, run_id: str) -> dict[str, Any]:
        row = self.repository.get_run(run_id)
        if row is None:
            raise RunNotFoundError(run_id)
        return run_view(row)

    def cancel(self, run_id: str) -> str:
        row = self.repository.get_run(run_id)
        if row is None:
            raise RunNotFoundError(run_id)
        if row["status"] in TERMINAL_STATUSES:
            return "already_terminal"
        cancel = self._cancel.get(run_id)
        if cancel is None:
            self.repository.request_cancel(run_id)
            return "accepted"
        cancel.set()
        return "accepted"

    def events_after(self, run_id: str, seq: int) -> list[Event]:
        if self.repository.get_run(run_id) is None:
            raise RunNotFoundError(run_id)
        return self.repository.events_after(run_id, seq)

    def answer_approval(
        self, run_id: str, request_id: str, outcome: ApprovalOutcome
    ) -> None:
        approval = self.repository.get_approval(request_id)
        if approval is None or approval["run_id"] != run_id:
            raise ApprovalNotFoundError(request_id)
        future = self._approval_waiters.get(request_id)
        if approval["status"] != "pending" or (future is not None and future.done()):
            raise ApprovalConflictError("approval is no longer pending")
        if not self.repository.answer_approval(request_id, outcome.value):
            raise ApprovalConflictError("approval is no longer pending")
        if future is not None:
            self._record_approval_outcome(run_id, request_id, outcome)
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
            policy = ApprovalPolicy(
                ApprovalMode(session["approval_mode"]), approver=self._approver(run_id)
            )
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
                        workspace=session["workspace"],
                        tools=(tool.name for tool in tools),
                    ),
                    user_instructions=session["user_instructions"],
                    project_instructions=session["project_instructions"],
                ),
            )

            def persist(event: Event) -> None:
                self._steps[run_id] = event.step
                if event.type not in {f"run.{status}" for status in TERMINAL_STATUSES}:
                    self.repository.append_runtime_event(event)

            result = await runtime.run(
                prompt,
                max_steps=max_steps,
                cancel=cancel,
                on_event=persist,
                history=self.repository.messages(session["session_id"]),
                run_id=run_id,
            )
            self.repository.finish_run(result, terminal_event=result.events[-1])
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
                        pass

    def _approver(self, run_id: str):
        async def approve(request: ApprovalRequest) -> ApprovalOutcome:
            request_id = str(uuid4())
            future: asyncio.Future[ApprovalOutcome] = (
                asyncio.get_running_loop().create_future()
            )
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
                deadline = asyncio.get_running_loop().time() + self._approval_timeout
                while True:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        if self.repository.answer_approval(request_id, ApprovalOutcome.DENY.value):
                            self._record_approval_outcome(run_id, request_id, ApprovalOutcome.DENY)
                            return ApprovalOutcome.DENY
                    try:
                        return await asyncio.wait_for(
                            asyncio.shield(future), timeout=max(0.01, min(0.1, remaining))
                        )
                    except asyncio.TimeoutError:
                        row = self.repository.get_approval(request_id)
                        if row is not None and row["status"] == "answered":
                            outcome = ApprovalOutcome(row["outcome"])
                            self._record_approval_outcome(run_id, request_id, outcome)
                            return outcome
            except asyncio.CancelledError:
                if self.repository.answer_approval(request_id, ApprovalOutcome.DENY.value):
                    self._record_approval_outcome(run_id, request_id, ApprovalOutcome.DENY)
                raise
            finally:
                self._approval_waiters.pop(request_id, None)

        return approve

    def _record_approval_outcome(
        self, run_id: str, request_id: str, outcome: ApprovalOutcome
    ) -> None:
        self.repository.append_event(
            run_id=run_id,
            step=self._steps.get(run_id, 0),
            kind=EventType.APPROVAL_RESOLVED,
            payload={"request_id": request_id, "outcome": outcome.value},
        )

    def _release(self, run_id: str) -> None:
        monitor = self._cancel_monitors.pop(run_id, None)
        if monitor is not None:
            monitor.cancel()
        self._tasks.pop(run_id, None)
        self._cancel.pop(run_id, None)
        self._steps.pop(run_id, None)
