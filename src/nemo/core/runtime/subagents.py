"""Run-owned parallel read-only children using the existing agent loop."""

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Literal
import json
import asyncio
from uuid import uuid4

from pydantic import Field

from nemo.core.context.builder import ContextBuilder
from nemo.core.context.compaction import ContextWindow
from nemo.core.contracts.tools import ExecutionContext, ToolPolicy
from nemo.core.contracts.errors import ToolFailure
from nemo.core.contracts.types import (
    Contract, Event, Model, ModelRequest, ModelResponse, ModelTextDelta, ModelReasoningDelta,
)
from nemo.core.runtime.agent import AgentRuntime
from nemo.core.tools.limits import truncate_text
from nemo.core.tools.registry import Tool, ToolRegistry
from nemo.redaction import redact


class DelegateArguments(Contract):
    task: str = Field(min_length=1, max_length=16000)
    max_steps: int = Field(default=6, ge=1, le=10)


class DelegateTaskTool:
    name = "delegate_task"
    description = (
        "Delegate a self-contained investigation to a read-only subagent. "
        "Include all needed context in task: it cannot see this conversation. "
        "Waits for a bounded result; the subagent cannot edit, run shell or delegate."
    )
    arguments_type = DelegateArguments
    read_only = True

    def __init__(self, model: Model, tools: tuple[Tool, ...], *,
                 context: ContextBuilder, policy: ToolPolicy,
                 context_window: ContextWindow | None = None):
        self.model = model
        # Filter again here: callers cannot accidentally expose writes or recursion.
        self.tools = tuple(tool for tool in tools if tool.read_only
                           and tool.name not in CONTROL_TOOLS | {"run_shell"})
        self.context = context
        self.policy = policy
        self.context_window = context_window

    def summarize(self, arguments: DelegateArguments) -> str:
        return f"read-only subagent, up to {arguments.max_steps} steps"

    async def execute(self, arguments: DelegateArguments, context: ExecutionContext) -> object:
        return await self.execute_with_events(arguments, context, on_event=None)

    async def execute_with_events(self, arguments: DelegateArguments,
                                  context: ExecutionContext, *,
                                  on_event: Callable[[Event], None] | None) -> object:
        child_id = str(uuid4())
        child_context = context.model_copy(update={
            "run_id": child_id, "source_message_index": None,
        })
        registry = ToolRegistry(self.tools, context=child_context, policy=self.policy)
        # New runtime means a fresh compaction engine and no parent's checkpoint/history.
        runtime = AgentRuntime(self.model, registry, self.context,
                               context_window=self.context_window)
        try:
            async with asyncio.timeout(context.max_timeout_seconds):
                result = await runtime.run(arguments.task, max_steps=arguments.max_steps,
                                           run_id=child_id, on_event=on_event, finish_with_summary=True)
        except TimeoutError:
            raise ToolFailure("Subagent exceeded its time limit") from None
        return {
            "run_id": child_id,
            "status": result.state.status.value,
            "steps": result.state.step_count,
            "output": truncate_text(result.state.output or "",
                                    min(context.max_output_chars, 8000)),
            "error": result.state.error,
        }


# Parallel children are owned by one parent run, not by a Session or server singleton.
CONTROL_TOOLS = frozenset({"delegate_task", "spawn_subagents", "get_subagents",
                           "wait_subagents", "cancel_subagent"})


class ModelCallBudget:
    def __init__(self, limit: int = 40):
        if limit < 1:
            raise ValueError("Model call limit must be positive")
        self.limit = limit
        self.used = 0

    def reserve(self) -> None:
        # No await between check and increment: atomic within the run's event loop.
        if self.used >= self.limit:
            raise ToolFailure("Parent run model-call budget exhausted")
        self.used += 1


class BudgetedModel:
    """Counts parent, child and compression requests against one run budget."""
    def __init__(self, model: Model, budget: ModelCallBudget):
        self.model = model
        self.budget = budget

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.budget.reserve()
        return await self.model.generate(request)

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelTextDelta | ModelReasoningDelta | ModelResponse]:
        self.budget.reserve()
        streamer = getattr(self.model, "stream", None)
        if streamer is None:
            yield await self.model.generate(request)
        else:
            async for part in streamer(request):
                yield part


class SpawnArguments(Contract):
    tasks: list[DelegateArguments] = Field(min_length=1, max_length=8)


class GetArguments(Contract):
    run_ids: list[str] = Field(default_factory=list, max_length=8)
    output_chars: int = Field(default=1000, ge=0, le=4000)


class WaitArguments(Contract):
    run_ids: list[str] = Field(min_length=1, max_length=8)
    timeout_seconds: float = Field(default=30.0, ge=0, le=60)
    mode: Literal["any", "all"] = "any"
    output_chars: int = Field(default=1000, ge=0, le=4000)


class CancelArguments(Contract):
    run_id: str = Field(min_length=1, max_length=80)


@dataclass
class ChildJob:
    run_id: str
    task_spec: DelegateArguments
    context: ExecutionContext
    observer: Callable[[Event], None] | None
    status: str = "queued"
    steps: int = 0
    output: str = ""
    error: str | None = None
    seq: int = 0
    worker: asyncio.Task | None = None


class SubagentManager:
    def __init__(self, model: Model, tools: tuple[Tool, ...], *, context: ContextBuilder,
                 policy: ToolPolicy, context_window: ContextWindow | None = None,
                 max_concurrent: int = 3, max_tasks: int = 8, max_model_calls: int = 40):
        if not 1 <= max_concurrent <= max_tasks <= 8:
            raise ValueError("Invalid subagent limits")
        self.budget = ModelCallBudget(max_model_calls)
        self.model = BudgetedModel(model, self.budget)
        self.child_tools = tuple(t for t in tools if t.read_only
                                 and t.name not in CONTROL_TOOLS | {"run_shell"})
        self.context = context
        self.policy = policy
        self.context_window = context_window
        self.max_concurrent = max_concurrent
        self.max_tasks = max_tasks
        self.jobs: dict[str, ChildJob] = {}
        self.closed = True
        self._slots = asyncio.Semaphore(max_concurrent)
        self._owner: ExecutionContext | None = None

    def begin_run(self) -> None:
        if not self.closed:
            raise RuntimeError("Subagent manager is already running")
        self.jobs.clear()
        self.budget.used = 0
        self._slots = asyncio.Semaphore(self.max_concurrent)
        self._owner = None
        self.closed = False

    def _check(self, context: ExecutionContext) -> None:
        if self.closed:
            raise ToolFailure("Subagents are only available during the parent run")
        if self._owner is None:
            self._owner = context
        elif (context.workspace.resolve(), context.run_id, context.session_id) != (
                self._owner.workspace.resolve(), self._owner.run_id, self._owner.session_id):
            raise ToolFailure("Subagent belongs to a different parent run")

    def _emit(self, job: ChildJob, kind: str, payload: dict | None = None) -> None:
        job.seq += 1
        if job.observer:
            try:
                job.observer(Event(run_id=job.run_id, seq=job.seq, step=job.steps,
                                   type=kind, payload=payload or {}))
            except Exception:
                pass  # Observers cannot abandon children or change task execution.

    def spawn(self, tasks: list[DelegateArguments], context: ExecutionContext,
              observer: Callable[[Event], None] | None, *,
              check_output: bool = True) -> list[ChildJob]:
        self._check(context)
        if len(self.jobs) + len(tasks) > self.max_tasks:
            raise ToolFailure("Parent run subagent task limit reached")
        if self.budget.used >= self.budget.limit:
            raise ToolFailure("Parent run model-call budget exhausted")
        added = []
        for spec in tasks:
            child_id = str(uuid4())
            job = ChildJob(child_id, spec, context.model_copy(update={
                "run_id": child_id, "source_message_index": None}), observer)
            added.append(job)
        # Refuse before publication: callers must receive the IDs of started work.
        if check_output:
            self.snapshots(added, context, 0)
        for job in added:
            child_id = job.run_id
            self.jobs[child_id] = job
            self._emit(job, "subagent.queued", {"max_steps": job.task_spec.max_steps,
                        "task": truncate_text(redact(job.task_spec.task), 2000)})
            job.worker = asyncio.create_task(self._run(job), name=f"nemo-subagent-{child_id}")
        return added

    async def _run(self, job: ChildJob) -> None:
        try:
            # Queue waiting counts toward the child's overall deadline.
            async with asyncio.timeout(job.context.max_timeout_seconds):
                async with self._slots:
                    job.status = "running"
                    def forward(event: Event) -> None:
                        job.steps = event.step
                        self._emit(job, event.type, event.payload)
                    registry = ToolRegistry(self.child_tools, context=job.context, policy=self.policy)
                    runtime = AgentRuntime(self.model, registry, self.context,
                                           context_window=self.context_window)
                    result = await runtime.run(job.task_spec.task, max_steps=job.task_spec.max_steps,
                                               run_id=job.run_id, on_event=forward, finish_with_summary=True,
                                               include_execution_details=True)
                    job.status = result.state.status.value
                    job.output = truncate_text(result.state.output or "", min(job.context.max_output_chars, 8000))
                    job.error = result.state.error
        except TimeoutError:
            job.status, job.error = "failed", "Subagent exceeded its time limit"
            self._emit(job, "subagent.finished", {"status": job.status, "error": job.error, "output": redact(job.output)})
        except asyncio.CancelledError:
            job.status = "cancelled"
            self._emit(job, "subagent.finished", {"status": job.status, "output": redact(job.output), "error": job.error})
        except Exception:
            job.status, job.error = "failed", "Subagent execution failed"
            self._emit(job, "subagent.finished", {"status": job.status, "error": job.error, "output": redact(job.output)})
        else:
            self._emit(job, "subagent.finished", {"status": job.status, "output": redact(job.output), "error": job.error})

    def select(self, ids: tuple[str, ...], context: ExecutionContext) -> list[ChildJob]:
        self._check(context)
        if len(ids) != len(set(ids)):
            raise ToolFailure("Subagent IDs must be unique")
        if any(key not in self.jobs for key in ids):
            raise ToolFailure("Subagent is not owned by this parent run")
        return [self.jobs[key] for key in ids] if ids else list(self.jobs.values())

    def snapshots(self, jobs: list[ChildJob], context: ExecutionContext, output_chars: int = 1000,
                  *, pending: list[str] | None = None) -> dict:
        # Bound the aggregate, not just each result; clients can request smaller batches.
        per_job = min(output_chars, max(0, min(context.max_output_chars, 16000) // max(1, len(jobs)) - 400))
        while True:
            result = {"tasks": [{"run_id": j.run_id, "status": j.status, "steps": j.steps,
                                "output": truncate_text(j.output, per_job) if per_job else "",
                                "error": j.error} for j in jobs],
                      "model_calls": {"used": self.budget.used, "limit": self.budget.limit},
                      "limits": {"concurrent": self.max_concurrent, "tasks": self.max_tasks}}
            if pending is not None:
                result["pending"] = pending
            if len(json.dumps(result, ensure_ascii=False)) <= context.max_output_chars:
                return result
            if not per_job:
                raise ToolFailure("Subagent results exceed the output limit; request fewer IDs")
            per_job //= 2

    async def cancel(self, jobs: list[ChildJob]) -> None:
        active = [j for j in jobs if j.worker and not j.worker.done()]
        for job in active:
            if not job.worker.cancelling():
                job.worker.cancel()
        drain = asyncio.gather(*(j.worker for j in active), return_exceptions=True)
        cancelled = False
        while not drain.done():
            try:
                await asyncio.shield(drain)
            except asyncio.CancelledError:
                # Cancelling the control call must not cancel resource cleanup again.
                cancelled = True
        drain.result()
        for job in jobs:
            # A task cancelled before its first coroutine step never enters _run.
            if job.worker and job.worker.done() and job.status in {"queued", "running"}:
                job.status = "cancelled"
                self._emit(job, "subagent.finished", {"status": job.status, "output": redact(job.output), "error": job.error})
        if cancelled:
            raise asyncio.CancelledError

    async def end_run(self) -> None:
        self.closed = True  # Reject new children before draining existing work.
        await self.cancel(list(self.jobs.values()))

    def tools(self) -> tuple[Tool, ...]:
        return tuple(SubagentControlTool(self, name) for name in (
            "spawn_subagents", "get_subagents", "wait_subagents", "cancel_subagent", "delegate_task"))


class SubagentControlTool:
    read_only = True
    _contracts = {"spawn_subagents": SpawnArguments, "get_subagents": GetArguments,
                  "wait_subagents": WaitArguments, "cancel_subagent": CancelArguments,
                  "delegate_task": DelegateArguments}
    _descriptions = {
        "spawn_subagents": "Start independent read-only tasks concurrently; returns IDs immediately. Include all necessary background in each task.",
        "get_subagents": "Inspect this run's child tasks and bounded results. Empty IDs lists all. Narrow IDs or output_chars to reduce output.",
        "wait_subagents": "Wait up to timeout_seconds for any/all selected child tasks. Timeout keeps tasks running. Read returned statuses before using results.",
        "cancel_subagent": "Cancel one child task owned by this run and wait for cleanup.",
        "delegate_task": "Compatibility: start one read-only child and synchronously wait for its result. Prefer spawn_subagents for parallel work.",
    }

    def __init__(self, manager: SubagentManager, name: str):
        self.runtime_scope = manager
        self.name = name
        self.arguments_type = self._contracts[name]
        self.description = self._descriptions[name]

    def summarize(self, arguments: Contract) -> str:
        return f"{self.name}: read-only child task control"

    async def execute(self, arguments: Contract, context: ExecutionContext) -> object:
        return await self.execute_with_events(arguments, context, on_event=None)

    async def execute_with_events(self, arguments: Contract, context: ExecutionContext, *,
                                  on_event: Callable[[Event], None] | None) -> object:
        manager = self.runtime_scope
        if self.name in {"spawn_subagents", "delegate_task"}:
            specs = arguments.tasks if self.name == "spawn_subagents" else (arguments,)
            jobs = manager.spawn(specs, context, on_event,
                                 check_output=self.name == "spawn_subagents")
            if self.name == "delegate_task":
                await asyncio.shield(jobs[0].worker)
                job = jobs[0]
                if job.error == "Subagent exceeded its time limit":
                    raise ToolFailure(job.error)
                return {"run_id": job.run_id, "status": job.status, "steps": job.steps,
                        "output": redact(job.output), "error": job.error}
            return manager.snapshots(jobs, context, 0)
        if self.name == "cancel_subagent":
            jobs = manager.select((arguments.run_id,), context)
            await manager.cancel(jobs)
            return manager.snapshots(jobs, context)
        jobs = manager.select(arguments.run_ids, context)
        if self.name == "wait_subagents":
            workers = [j.worker for j in jobs if j.worker is not None]
            if workers and arguments.timeout_seconds:
                await asyncio.wait(workers, timeout=min(arguments.timeout_seconds, context.max_timeout_seconds),
                                   return_when=asyncio.ALL_COMPLETED if arguments.mode == "all" else asyncio.FIRST_COMPLETED)
            return manager.snapshots(jobs, context, arguments.output_chars,
                pending=[j.run_id for j in jobs if j.worker and not j.worker.done()])
        return manager.snapshots(jobs, context, arguments.output_chars)
