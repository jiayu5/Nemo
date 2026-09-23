import asyncio
import unittest

from nemo.core.contracts.types import Message, ModelReasoningDelta, ModelResponse, ModelTextDelta, RunStatus, ToolCall
from nemo.core.runtime.agent import AgentRuntime
from nemo.core.tools.registry import ToolRegistry
from nemo.testing.fakes import AddTool, AdditionModel, FakeModel


def call(name="add", arguments=None, id="c1"):
    return ToolCall(id=id, name=name, arguments=arguments if arguments is not None else {"a": 12, "b": 30})


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    def assert_terminal(self, result, status):
        self.assertEqual(result.state.status, status)
        terminals = [e for e in result.events if e.type.startswith("run.") and e.type != "run.started"]
        self.assertEqual([e.type for e in terminals], [f"run.{status.value}"])
        self.assertEqual([e.seq for e in result.events], list(range(1, len(result.events) + 1)))

    async def test_complete_loop(self):
        result = await AgentRuntime(AdditionModel(), ToolRegistry((AddTool(),))).run("计算 12 + 30")
        self.assert_terminal(result, RunStatus.COMPLETED)
        self.assertEqual(result.state.output, "12 + 30 = 42")
        self.assertEqual(result.state.step_count, 2)
        self.assertEqual(sum(e.type == "step.completed" for e in result.events), 2)
        self.assertEqual([m.role for m in result.state.messages], ["user", "assistant", "tool", "assistant"])
        self.assertEqual(result.state.messages[2].tool_result.output, 42)

    async def test_schema_and_result_reach_next_model_call(self):
        model = FakeModel([ModelResponse(tool_calls=(call(),)), ModelResponse(content="done")])
        await AgentRuntime(model, ToolRegistry((AddTool(),))).run("add")
        self.assertEqual(model.requests[0].tools[0].name, "add")
        messages = model.requests[1].messages
        # Reminders may follow the tool result, so locate it by role instead of
        # assuming it is the final message.
        assistant = next(m for m in reversed(messages) if m.tool_calls)
        tool_message = next(m for m in reversed(messages) if m.role == "tool")
        self.assertEqual(assistant.tool_calls[0].id, tool_message.tool_result.tool_call_id)
        self.assertEqual(tool_message.tool_result.output, 42)
        self.assertEqual(len(model.requests[0].messages), 1)

    async def test_recoverable_tool_errors(self):
        class BrokenTool(AddTool):
            async def execute(self, arguments, context):
                raise RuntimeError("secret must not appear")
        cases = [(call(name="missing"), AddTool(), "unknown_tool"),
                 (call(arguments={"a": "12", "b": 30}), AddTool(), "invalid_arguments"),
                 (call(arguments={"a": 12, "b": 30, "extra": 1}), AddTool(), "invalid_arguments"),
                 (call(), BrokenTool(), "execution_error")]
        for invocation, tool, code in cases:
            with self.subTest(code=code, arguments=invocation.arguments):
                model = FakeModel([ModelResponse(tool_calls=(invocation,)), ModelResponse(content="recovered")])
                result = await AgentRuntime(model, ToolRegistry((tool,))).run("task")
                self.assert_terminal(result, RunStatus.COMPLETED)
                tool_message = next(
                    m for m in reversed(model.requests[1].messages) if m.role == "tool"
                )
                self.assertEqual(tool_message.tool_result.error.code, code)
                self.assertNotIn("secret must not appear", result.model_dump_json())

    async def test_multiple_calls_keep_order(self):
        model = FakeModel([ModelResponse(tool_calls=(call(id="a"), call(id="b"))), ModelResponse(content="done")])
        result = await AgentRuntime(model, ToolRegistry((AddTool(),))).run("task")
        self.assertEqual([m.tool_result.tool_call_id for m in result.state.messages if m.role == "tool"], ["a", "b"])

    async def test_max_steps(self):
        model = FakeModel([ModelResponse(tool_calls=(call(),))])
        result = await AgentRuntime(model, ToolRegistry((AddTool(),))).run("task", max_steps=1)
        self.assert_terminal(result, RunStatus.LIMIT_REACHED)
        self.assertIsNone(result.state.output)
        self.assertEqual(len(model.requests), 1)

    async def test_model_error(self):
        result = await AgentRuntime(FakeModel([]), ToolRegistry()).run("task")
        self.assert_terminal(result, RunStatus.FAILED)

    async def test_streamed_text_precedes_final_message(self):
        class StreamingModel:
            async def stream(self, request):
                yield ModelTextDelta(text="Hel")
                yield ModelTextDelta(text="lo")
                yield ModelResponse(content="Hello")

        result = await AgentRuntime(StreamingModel(), ToolRegistry()).run("task")
        self.assertEqual(result.state.output, "Hello")
        self.assertEqual(
            [event.payload["text"] for event in result.events if event.type == "model.delta"],
            ["Hel", "lo"],
        )
        self.assertEqual(result.state.messages[-1].content, "Hello")

    async def test_reasoning_uses_separate_event_and_message_field(self):
        class StreamingModel:
            async def stream(self, request):
                yield ModelReasoningDelta(text="Check the facts")
                yield ModelTextDelta(text="Answer")
                yield ModelResponse(content="Answer", reasoning_content="Check the facts")

        result = await AgentRuntime(StreamingModel(), ToolRegistry()).run("task")
        self.assertEqual([e.payload["text"] for e in result.events
                          if e.type == "model.reasoning_delta"], ["Check the facts"])
        self.assertEqual(result.state.messages[-1].reasoning_content, "Check the facts")
        self.assertEqual(result.state.output, "Answer")

    async def test_cancelled_stream_keeps_visible_partial_text(self):
        entered = asyncio.Event()
        cancel = asyncio.Event()

        class StreamingModel:
            async def stream(self, request):
                yield ModelTextDelta(text="Partial reply")
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(
            AgentRuntime(StreamingModel(), ToolRegistry()).run("task", cancel=cancel)
        )
        await asyncio.wait_for(entered.wait(), 1)
        cancel.set()
        result = await asyncio.wait_for(task, 1)
        self.assertEqual(result.state.status, RunStatus.CANCELLED)
        self.assertEqual(result.state.output, "Partial reply")
        self.assertEqual(result.state.messages[-1].content, "Partial reply")

    async def test_failed_stream_keeps_visible_partial_text(self):
        class StreamingModel:
            async def stream(self, request):
                yield ModelTextDelta(text="Partial reply")
                raise ValueError("provider disconnected")

        result = await AgentRuntime(StreamingModel(), ToolRegistry()).run("task")
        self.assertEqual(result.state.status, RunStatus.FAILED)
        self.assertEqual(result.state.output, "Partial reply")
        self.assertEqual(result.state.messages[-1].content, "Partial reply")
        self.assertEqual(result.state.error, "Runtime execution failed")

    async def test_cancel_before_start(self):
        cancel = asyncio.Event()
        cancel.set()
        model = FakeModel([])
        result = await AgentRuntime(model, ToolRegistry()).run("task", cancel=cancel)
        self.assert_terminal(result, RunStatus.CANCELLED)
        self.assertFalse(model.requests)

    async def test_cancel_inflight_model_and_tool(self):
        for stage in ("model", "tool"):
            with self.subTest(stage=stage):
                entered, cleaned, cancel = asyncio.Event(), asyncio.Event(), asyncio.Event()
                class BlockingModel:
                    async def generate(self, request):
                        entered.set()
                        try:
                            await asyncio.Event().wait()
                        finally:
                            cleaned.set()
                class BlockingTool(AddTool):
                    async def execute(self, arguments, context):
                        entered.set()
                        try:
                            await asyncio.Event().wait()
                        finally:
                            cleaned.set()
                model = BlockingModel() if stage == "model" else FakeModel([ModelResponse(tool_calls=(call(),))])
                runtime = AgentRuntime(model, ToolRegistry((BlockingTool(),)))
                task = asyncio.create_task(runtime.run("task", cancel=cancel))
                await asyncio.wait_for(entered.wait(), 1)
                cancel.set()
                result = await asyncio.wait_for(task, 1)
                self.assert_terminal(result, RunStatus.CANCELLED)
                self.assertTrue(cleaned.is_set())

    async def test_external_task_cancellation_propagates(self):
        entered, cleaned = asyncio.Event(), asyncio.Event()
        events = []
        class BlockingModel:
            async def generate(self, request):
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cleaned.set()
        task = asyncio.create_task(AgentRuntime(BlockingModel(), ToolRegistry()).run("task", on_event=events.append))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(cleaned.is_set())
        self.assertEqual(sum(e.type == "run.cancelled" for e in events), 1)

    async def test_observer_failure_is_visible_and_does_not_break_execution(self):
        def broken(event):
            raise RuntimeError("observer failure")
        result = await AgentRuntime(AdditionModel(), ToolRegistry((AddTool(),))).run("task", on_event=broken)
        self.assert_terminal(result, RunStatus.COMPLETED)
        self.assertTrue(any(e.type == "observer.failed" for e in result.events))

    async def test_concurrent_runs_are_isolated(self):
        runtime = AgentRuntime(AdditionModel(), ToolRegistry((AddTool(),)))
        a, b = await asyncio.gather(runtime.run("a"), runtime.run("b"))
        self.assertNotEqual(a.state.run_id, b.state.run_id)
        self.assertEqual(a.state.messages[0].content, "a")
        self.assertEqual(b.state.messages[0].content, "b")

    async def test_history_is_prepended_and_copied(self):
        history = [Message(role="user", content="earlier"),
                   Message(role="assistant", content="sure")]
        model = FakeModel([ModelResponse(content="done")])

        result = await AgentRuntime(model, ToolRegistry()).run("next", history=history)

        self.assertEqual([m.content for m in model.requests[0].messages],
                         ["earlier", "sure", "next"])
        self.assertEqual(len(history), 2)                       # caller's list untouched
        self.assertIsNot(result.state.messages[0], history[0])  # and deep-copied
        self.assertEqual(result.state.step_count, 1)            # steps restart per run

    async def test_invalid_limits(self):
        runtime = AgentRuntime(FakeModel([]), ToolRegistry())
        for value in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                await runtime.run("task", max_steps=value)

    def test_contract_guards(self):
        with self.assertRaises(ValueError):
            ToolRegistry((AddTool(), AddTool()))
        with self.assertRaises(ValueError):
            ModelResponse(tool_calls=(call(), call()))
        with self.assertRaises(ValueError):
            Message(role="tool")
