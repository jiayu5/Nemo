"""Run-owned parallel read-only children using the existing agent loop."""

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field, replace
from typing import Literal
import json
import asyncio
from uuid import uuid4

from pydantic import Field, create_model

from nemo.core.contracts.model_config import SubagentConfig
from nemo.core.context.builder import ContextBuilder
from nemo.core.context.compaction import ContextWindow
from nemo.core.contracts.tools import ExecutionContext, ToolPolicy
from nemo.core.contracts.errors import ToolFailure
from nemo.core.contracts.types import (
    Contract, Event, Message, Model, ModelRequest, ModelResponse, ModelTextDelta, ModelReasoningDelta,
)
from nemo.core.runtime.agent import AgentRuntime
from nemo.core.tools.limits import truncate_text
from nemo.core.tools.registry import Tool, ToolRegistry
from nemo.redaction import redact


class DelegateArguments(Contract):
    task: str = Field(min_length=1, max_length=16000)
    max_steps: int = Field(default=15, ge=1, le=40, strict=True)


class DelegateTaskTool:
    """Legacy constructor backed by the same run-owned manager as production."""

    name = "delegate_task"
    read_only = True

    def __init__(self, model: Model, tools: tuple[Tool, ...], *,
                 context: ContextBuilder, policy: ToolPolicy,
                 context_window: ContextWindow | None = None,
                 limits: SubagentConfig | None = None):
        self.runtime_scope = SubagentManager(model, tools, context=context, policy=policy,
                                            context_window=context_window, limits=limits)
        self._tool = SubagentControlTool(self.runtime_scope, self.name)
        self.arguments_type = self._tool.arguments_type
        self.description = self._tool.description

    def summarize(self, arguments: DelegateArguments) -> str:
        return f"read-only subagent, up to {arguments.max_steps} steps"

    async def execute(self, arguments: DelegateArguments, context: ExecutionContext) -> object:
        return await self.execute_with_events(arguments, context, on_event=None)

    async def execute_with_events(self, arguments: DelegateArguments,
                                  context: ExecutionContext, *,
                                  on_event: Callable[[Event], None] | None) -> object:
        standalone = self.runtime_scope.closed
        if standalone:
            self.runtime_scope.begin_run()
        try:
            return await self._tool.execute_with_events(arguments, context, on_event=on_event)
        finally:
            if standalone:
                await self.runtime_scope.end_run()


# Parallel children are owned by one parent run, not by a Session or server singleton.
CONTROL_TOOLS = frozenset({"delegate_task", "spawn_subagents", "get_subagents",
                           "wait_subagents", "cancel_subagent", "read_subagent_report", "resume_subagent"})


class ModelCallBudget:
    def __init__(self, limit: int = 160):
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


class ResumeArguments(Contract):
    run_id: str = Field(min_length=1, max_length=80)
    prompt: str = Field(default="Continue the unfinished work and report confirmed findings and remaining gaps.",
                        min_length=1, max_length=16000)
    max_steps: int = Field(default=15, ge=1, le=40, strict=True)


class ReadReportArguments(Contract):
    run_id: str = Field(min_length=1, max_length=80)
    round: int | None = Field(default=None, ge=1, strict=True)
    offset: int = Field(default=0, ge=0, strict=True)
    limit: int = Field(default=4000, ge=1, le=8000, strict=True)


class CancelArguments(Contract):
    run_id: str = Field(min_length=1, max_length=80)


@dataclass(frozen=True)
class ChildReport:
    status: str
    output: str
    steps: int
    error: str | None


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
    round: int = 1
    max_steps: int = 0
    history: tuple[Message, ...] = ()
    runtime: AgentRuntime | None = None
    reports: dict[int, ChildReport] = field(default_factory=dict)


class SubagentManager:
    def __init__(self, model: Model, tools: tuple[Tool, ...], *, context: ContextBuilder,
                 policy: ToolPolicy, context_window: ContextWindow | None = None,
                 max_concurrent: int = 3, max_tasks: int = 8, max_model_calls: int | None = None,
                 limits: SubagentConfig | None = None):
        if not 1 <= max_concurrent <= max_tasks <= 8:
            raise ValueError("Invalid subagent limits")
        self.limits = limits or SubagentConfig()
        self.delegate_arguments = create_model(
            "DelegateArguments", __base__=DelegateArguments,
            max_steps=(int, Field(default=self.limits.default_max_steps, ge=1,
                                  le=self.limits.max_steps, strict=True)),
        )
        self.resume_arguments = create_model(
            "ResumeArguments", __base__=ResumeArguments,
            max_steps=(int, Field(default=self.limits.default_max_steps, ge=1,
                                  le=self.limits.max_steps, strict=True)),
        )
        self.spawn_arguments = create_model(
            "SpawnArguments", __base__=Contract,
            tasks=(list[self.delegate_arguments], Field(min_length=1, max_length=8)),
        )
        self.budget = ModelCallBudget(self.limits.max_model_calls if max_model_calls is None else max_model_calls)
        self.model = BudgetedModel(model, self.budget)
        self.child_tools = tuple(t for t in tools if t.read_only and getattr(t, "subagent_allowed", True)
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
                                   type=kind, payload={**(payload or {}), "round": job.round}))
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
            if not 1 <= spec.max_steps <= self.limits.max_steps:
                raise ToolFailure("Subagent step budget exceeds configured limits")
            child_id = str(uuid4())
            job = ChildJob(child_id, spec, context.model_copy(update={
                "run_id": child_id, "source_message_index": None}), observer, max_steps=spec.max_steps)
            added.append(job)
        # Refuse before publication: callers must receive the IDs of started work.
        if check_output:
            self.snapshots(added, context, 0)
        for job in added:
            child_id = job.run_id
            self.jobs[child_id] = job
            self._emit(job, "subagent.queued", {"max_steps": job.task_spec.max_steps,
                        "task": truncate_text(redact(job.task_spec.task), 2000)})
            job.worker = asyncio.create_task(self._run(job, job.task_spec.task), name=f"nemo-subagent-{child_id}")
        return added

    def resume(self, arguments: ResumeArguments, context: ExecutionContext,
               observer: Callable[[Event], None] | None) -> ChildJob:
        job = self.select((arguments.run_id,), context)[0]
        if job.status not in {"completed", "limit_reached"} or not job.worker or not job.worker.done() or not job.history:
            raise ToolFailure("Only completed or step-limited children with saved context can resume")
        if not 1 <= arguments.max_steps <= self.limits.max_steps:
            raise ToolFailure("Subagent step budget exceeds configured limits")
        if self.budget.used >= self.budget.limit:
            raise ToolFailure("Parent run model-call budget exhausted")
        # Preflight before mutation or scheduling, just like a new batch admission.
        proposed = replace(job, round=job.round + 1, max_steps=arguments.max_steps,
                           status="queued", output="", error=None)
        self.snapshots([proposed], context, 0)
        job.round, job.max_steps = proposed.round, proposed.max_steps
        job.status, job.output, job.error = "queued", "", None
        job.observer = observer
        self._emit(job, "subagent.resumed", {"prompt": truncate_text(redact(arguments.prompt), 2000),
                   "max_steps": job.max_steps})
        job.worker = asyncio.create_task(self._run(job, arguments.prompt), name=f"nemo-subagent-{job.run_id}")
        return job

    def _finish(self, job: ChildJob) -> None:
        job.reports[job.round] = ChildReport(job.status, job.output, job.steps, job.error)
        for offset in range(0, len(job.output), 4000):
            self._emit(job, "subagent.report", {"offset": offset,
                       "text": job.output[offset:offset + 4000], "total_chars": len(job.output)})
        self._emit(job, "subagent.finished", {"status": job.status, "error": job.error,
                   "output": truncate_text(job.output, 1000), "report_chars": len(job.output)})

    async def _run(self, job: ChildJob, prompt: str) -> None:
        step_base = job.steps
        try:
            # Each execution round includes queue waiting in its deadline.
            async with asyncio.timeout(job.context.max_timeout_seconds):
                async with self._slots:
                    job.status = "running"
                    def forward(event: Event) -> None:
                        job.steps = step_base + event.step
                        self._emit(job, event.type, event.payload)
                    if job.runtime is None:
                        registry = ToolRegistry(self.child_tools, context=job.context, policy=self.policy)
                        job.runtime = AgentRuntime(self.model, registry, self.context,
                                                   context_window=self.context_window)
                    result = await job.runtime.run(prompt, history=job.history, max_steps=job.max_steps,
                                                   run_id=job.run_id, on_event=forward, finish_with_summary=True,
                                                   include_execution_details=True)
                    job.history = tuple(result.state.messages)
                    job.status = result.state.status.value
                    job.output = redact(result.state.output or "")
                    job.error = result.state.error
        except TimeoutError:
            job.status, job.error = "failed", "Subagent exceeded its time limit"
        except asyncio.CancelledError:
            job.status = "cancelled"
        except Exception:
            job.status, job.error = "failed", "Subagent execution failed"
        self._finish(job)

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
                                "report_chars": len(j.output), "output_truncated": len(j.output) > per_job,
                                "round": j.round, "max_steps": j.max_steps,
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

    def read_report(self, arguments: ReadReportArguments, context: ExecutionContext) -> dict:
        job = self.select((arguments.run_id,), context)[0]
        round_number = arguments.round if arguments.round is not None else job.round
        report = job.reports.get(round_number)
        if report is None:
            raise ToolFailure("Subagent report is not ready or the requested round does not exist")
        if arguments.offset > len(report.output):
            raise ToolFailure("Subagent report offset is beyond the text")
        end = min(len(report.output), arguments.offset + arguments.limit)
        while True:
            result = {"run_id": job.run_id, "status": report.status, "round": round_number, "offset": arguments.offset,
                      "text": report.output[arguments.offset:end], "total_chars": len(report.output),
                      "next_offset": end if end < len(report.output) else None}
            if len(json.dumps(result, ensure_ascii=False)) <= context.max_output_chars:
                return result
            if end == arguments.offset:
                raise ToolFailure("Subagent report metadata exceeds the output limit")
            end = arguments.offset + (end - arguments.offset) // 2
            if end == arguments.offset and arguments.offset < len(report.output):
                raise ToolFailure("Subagent report output limit is too small for progress")

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
                self._finish(job)
        if cancelled:
            raise asyncio.CancelledError

    async def end_run(self) -> None:
        self.closed = True  # Reject new children before draining existing work.
        await self.cancel(list(self.jobs.values()))

    def tools(self) -> tuple[Tool, ...]:
        return tuple(SubagentControlTool(self, name) for name in (
            "spawn_subagents", "get_subagents", "wait_subagents", "cancel_subagent", "delegate_task",
            "read_subagent_report", "resume_subagent"))


class SubagentControlTool:
    read_only = True
    _contracts = {"spawn_subagents": SpawnArguments, "get_subagents": GetArguments,
                  "wait_subagents": WaitArguments, "cancel_subagent": CancelArguments,
                  "delegate_task": DelegateArguments, "read_subagent_report": ReadReportArguments, "resume_subagent": ResumeArguments}
    _descriptions = {
        "spawn_subagents": "Start independent read-only tasks concurrently; returns IDs immediately. Include all necessary background in each task.",
        "get_subagents": "Inspect child states, report lengths and short previews. Empty IDs lists all; use read_subagent_report for full reports.",
        "wait_subagents": "Wait up to timeout_seconds for any/all selected child tasks. Timeout keeps tasks running. Read returned statuses before using results.",
        "read_subagent_report": "Read a finished report by character offset; follow next_offset until null. Omitted round uses the current round; specify round for earlier reports.",
        "resume_subagent": "Continue a completed or step-limited child with saved context in this parent run; prompt adds instructions, max_steps grants another bounded round. Shared budget never resets.",
        "cancel_subagent": "Cancel one child task owned by this run and wait for cleanup.",
        "delegate_task": "Compatibility: start one read-only child and synchronously wait for its result. Prefer spawn_subagents for parallel work.",
    }

    def __init__(self, manager: SubagentManager, name: str):
        self.runtime_scope = manager
        self.name = name
        self.arguments_type = (manager.spawn_arguments if name == "spawn_subagents" else
                               manager.delegate_arguments if name == "delegate_task" else
                               manager.resume_arguments if name == "resume_subagent" else
                               self._contracts[name])
        self.description = self._descriptions[name]
        if name in {"spawn_subagents", "delegate_task", "resume_subagent"}:
            self.description += (
                " Choose max_steps by task complexity, including the final report;"
                f" all agents and compression share {manager.budget.limit} model calls."
            )

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
                return manager.snapshots([job], context)["tasks"][0]
            return manager.snapshots(jobs, context, 0)
        if self.name == "resume_subagent":
            job = manager.resume(arguments, context, on_event)
            return manager.snapshots([job], context, 0)
        if self.name == "read_subagent_report":
            return manager.read_report(arguments, context)
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
