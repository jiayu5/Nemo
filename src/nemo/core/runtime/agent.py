"""Small sequential loop; each run owns its state and cancellation handle."""
import asyncio
from collections.abc import Sequence
from collections.abc import Callable

from nemo.core.context.builder import ContextBuilder
from nemo.core.context.reminder import describe, fit_reminders, slot_open, step_budget
from nemo.core.contracts.events import EventType
from nemo.core.contracts.types import (
    AgentState, Event, Message, Model, ModelUsage, RunResult, RunStatus,
)
from nemo.core.tools.registry import ToolRegistry


class AgentRuntime:
    def __init__(self, model: Model, tools: ToolRegistry,
                 context: ContextBuilder | None = None):
        self.model = model
        self.tools = tools
        self.context = context or ContextBuilder()

    async def run(self, prompt: str, *, max_steps: int = 10,
                  cancel: asyncio.Event | None = None,
                  on_event: Callable[[Event], None] | None = None,
                  history: Sequence[Message] = (),
                  run_id: str | None = None) -> RunResult:
        if isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps < 1:
            raise ValueError("max_steps must be a positive integer")
        # ``history`` is copied, never adopted: a run does not mutate the
        # caller's conversation, so a session can keep handing out the same
        # snapshot. Step numbering restarts at 1 -- ``max_steps`` guards one
        # turn, not the whole session.
        state = AgentState(
            **({"run_id": run_id} if run_id is not None else {}),
            messages=[m.model_copy(deep=True) for m in history]
            + [Message(role="user", content=prompt)]
        )
        events: list[Event] = []

        def emit(kind: str, **payload):
            event = Event(run_id=state.run_id, seq=len(events) + 1,
                          step=state.step_count, type=kind, payload=payload)
            events.append(event)
            if on_event is not None:
                try:
                    on_event(event.model_copy(deep=True))
                except Exception:
                    # Observers cannot change execution or replay side effects.
                    # Failure is visible in the returned trace, without exception text.
                    events.append(Event(run_id=state.run_id, seq=len(events) + 1,
                                        step=state.step_count, type=EventType.OBSERVER_FAILED))

        async def execute_loop():
            state.status = RunStatus.RUNNING
            emit(EventType.RUN_STARTED)
            for step in range(1, max_steps + 1):
                # Also yield when fake implementations return without awaiting.
                await asyncio.sleep(0)
                if cancel is not None and cancel.is_set():
                    raise asyncio.CancelledError
                state.step_count = step
                emit(EventType.STEP_STARTED)
                # Reminders only exist while the model keeps acting. The user's
                # own turn has the full step budget, so nothing is injected there.
                reminders = fit_reminders((step_budget(step, max_steps),)) if slot_open(state) else ()
                request = self.context.build(state, self.tools, reminders)
                emit(EventType.MODEL_STARTED, reminders=describe(reminders))
                response = await self.model.generate(request)
                emit(EventType.MODEL_COMPLETED, tool_call_count=len(response.tool_calls),
                     **_usage_payload(response.usage))
                state.messages.append(Message(role="assistant", content=response.content,
                                              tool_calls=response.tool_calls))
                if not response.tool_calls:
                    state.output = response.content
                    state.status = RunStatus.COMPLETED
                    emit(EventType.STEP_COMPLETED)
                    return
                for call in response.tool_calls:
                    await asyncio.sleep(0)
                    if cancel is not None and cancel.is_set():
                        raise asyncio.CancelledError
                    summary = self.tools.summary_for(call)
                    emit(EventType.TOOL_STARTED, tool_call_id=call.id, name=call.name, summary=summary)
                    result = await self.tools.execute(call)
                    state.messages.append(Message(role="tool", tool_result=result))
                    emit(EventType.TOOL_FAILED if result.error else EventType.TOOL_COMPLETED,
                         tool_call_id=call.id, name=call.name,
                         summary=summary,
                         error_code=result.error.code if result.error else None)
                emit(EventType.STEP_COMPLETED)
            state.status = RunStatus.LIMIT_REACHED

        worker = None
        watcher = None
        try:
            if cancel is not None and cancel.is_set():
                state.status = RunStatus.CANCELLED
            else:
                worker = asyncio.create_task(execute_loop())
                if cancel is not None:
                    watcher = asyncio.create_task(cancel.wait())
                    done, _ = await asyncio.wait((worker, watcher),
                                                 return_when=asyncio.FIRST_COMPLETED)
                    if worker not in done:
                        worker.cancel()
                await worker
        except asyncio.CancelledError:
            state.status = RunStatus.CANCELLED
            # Explicit cancel Event returns a result; task.cancel() retains normal
            # asyncio cancellation semantics after cleanup and terminal emission.
            if asyncio.current_task().cancelling():
                raise
        except Exception as exc:
            state.status = RunStatus.FAILED
            # Only a curated, secret-free message is exposed; the raw exception
            # text never reaches the result or the event stream.
            state.error = getattr(exc, "public_message", None) or "Runtime execution failed"
        finally:
            for task in (worker, watcher):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(*(t for t in (worker, watcher) if t is not None),
                                 return_exceptions=True)
            emit(EventType(f"run.{state.status.value}"), **({"error": state.error} if state.error else {}))
        return RunResult(state=state, events=tuple(events))


def _usage_payload(usage: ModelUsage | None) -> dict[str, int | None]:
    """Keep the event schema stable whether or not the provider reported usage."""

    if usage is None:
        return {"prompt_tokens": None, "completion_tokens": None,
                "cached_prompt_tokens": None}
    return {"prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "cached_prompt_tokens": usage.cached_prompt_tokens}
