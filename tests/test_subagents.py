import asyncio
from pathlib import Path
import unittest

from nemo.core.context.builder import ContextBuilder
from nemo.core.contracts.events import EventType
from nemo.core.contracts.tools import ExecutionContext, PolicyDecision
from nemo.core.contracts.types import Message, ModelResponse, RunStatus, ToolCall
from nemo.core.runtime.agent import AgentRuntime
from nemo.core.runtime.subagents import DelegateTaskTool
from nemo.core.tools.policy import ReadOnlyPolicy
from nemo.core.tools.registry import ToolRegistry
from nemo.testing.fakes import AddTool, BlockingModel, FakeModel


def delegate(**arguments):
    return ToolCall(id="delegate-1", name="delegate_task",
                    arguments={"task": "Investigate", **arguments})


class SubagentTests(unittest.IsolatedAsyncioTestCase):
    def registry(self, model, *, tools=(), policy=None, context=None):
        tool = DelegateTaskTool(model, tuple(tools), policy=policy or ReadOnlyPolicy(),
                                context=ContextBuilder(system_prompt="Inherited instructions"))
        return ToolRegistry((tool,), context=context or ExecutionContext(workspace=Path.cwd()))

    async def test_parent_receives_only_result_and_child_has_fresh_history(self):
        child = FakeModel([ModelResponse(content="Evidence")])
        parent = FakeModel([ModelResponse(tool_calls=(delegate(),)), ModelResponse(content="Summary")])
        result = await AgentRuntime(parent, self.registry(child)).run(
            "Main task", history=(Message(role="user", content="Private parent context"),),
        )
        request = child.requests[0]
        self.assertEqual([m.content for m in request.messages], ["Inherited instructions", "Investigate"])
        self.assertEqual(result.state.output, "Summary")
        output = next(m.tool_result.output for m in result.state.messages if m.role == "tool")
        self.assertEqual(output["output"], "Evidence")
        self.assertEqual(output["status"], "completed")
        self.assertNotEqual(output["run_id"], result.state.run_id)
        nested = [e for e in result.events if e.type == EventType.SUBAGENT_EVENT]
        self.assertEqual(nested[-1].payload["child_type"], "subagent.finished")
        self.assertTrue(all(e.run_id == result.state.run_id for e in result.events))
        self.assertEqual([e.seq for e in result.events], list(range(1, len(result.events) + 1)))
        self.assertEqual(sum(e.type == EventType.RUN_COMPLETED for e in result.events), 1)

    async def test_tools_filter_writes_shell_and_recursive_delegation(self):
        # Only metadata differs: these tools must never execute or reach the child schema.
        write = AddTool()
        write.name, write.read_only = "write", False
        shell = AddTool()
        shell.name = "run_shell"
        recursive = AddTool()
        recursive.name = "delegate_task"
        child = FakeModel([ModelResponse(content="done")])
        await self.registry(child, tools=(AddTool(), write, shell, recursive)).execute(delegate())
        self.assertEqual([t.name for t in child.requests[0].tools], ["add"])

    async def test_child_policy_and_execution_context_are_inherited(self):
        decisions = []

        class DenyPolicy:
            async def decide(self, *, facts, arguments, context):
                decisions.append((facts, context))
                return PolicyDecision(allowed=False, reason="Denied by parent policy")

        child = FakeModel([
            ModelResponse(tool_calls=(ToolCall(id="a", name="add", arguments={"a": 1, "b": 2}),)),
            ModelResponse(content="Denied"),
        ])
        context = ExecutionContext(workspace=Path.cwd(), session_id="parent-session", run_id="parent",
                                   source_message_index=2, max_output_chars=800)
        output = await self.registry(child, tools=(AddTool(),), policy=DenyPolicy(), context=context).execute(delegate())
        self.assertEqual(output.output["status"], "completed")
        self.assertEqual(decisions[0][1].workspace, context.workspace)
        self.assertEqual(decisions[0][1].session_id, "parent-session")
        self.assertNotEqual(decisions[0][1].run_id, "parent")
        self.assertIsNone(decisions[0][1].source_message_index)
        tool_result = next(m.tool_result for m in child.requests[1].messages if m.role == "tool")
        self.assertEqual(tool_result.error.code, "denied")

    async def test_each_call_starts_fresh_with_unique_run_and_step_budget(self):
        child = FakeModel([
            ModelResponse(tool_calls=(ToolCall(id="a", name="add", arguments={"a": 1, "b": 2}),)),
            ModelResponse(content="Second child"),
        ])
        registry = self.registry(child, tools=(AddTool(),))
        first = await registry.execute(delegate(max_steps=1))
        second = await registry.execute(delegate())
        self.assertEqual(first.output["status"], "limit_reached")
        self.assertEqual(first.output["steps"], 1)
        self.assertEqual(second.output["status"], "completed")
        self.assertNotEqual(first.output["run_id"], second.output["run_id"])
        self.assertEqual(len(child.requests[1].messages), 2)

    async def test_invalid_budget_and_task_do_not_run_child(self):
        child = FakeModel([])
        registry = self.registry(child)
        for args in ({"max_steps": 0}, {"max_steps": 41}, {"max_steps": True},
                     {"task": ""}, {"task": "x" * 16001}, {"extra": 1}):
            result = await registry.execute(delegate(**args))
            self.assertEqual(result.error.code, "invalid_arguments")
        self.assertEqual(child.requests, [])

    async def test_model_failure_is_reported_without_raw_exception(self):
        result = await self.registry(FakeModel([])).execute(delegate())
        self.assertIsNone(result.error)
        self.assertEqual(result.output["status"], "failed")
        self.assertEqual(result.output["error"], "Runtime execution failed")

    async def test_parent_cancellation_waits_for_child_cleanup(self):
        child = BlockingModel()
        parent = FakeModel([ModelResponse(tool_calls=(delegate(),))])
        cancel = asyncio.Event()
        task = asyncio.create_task(AgentRuntime(parent, self.registry(child)).run("Task", cancel=cancel))
        await asyncio.wait_for(child.started.wait(), 1)
        cancel.set()
        result = await asyncio.wait_for(task, 1)
        self.assertTrue(child.stopped.is_set())
        self.assertEqual(result.state.status, RunStatus.CANCELLED)
        nested = [e.payload["child_type"] for e in result.events if e.type == EventType.SUBAGENT_EVENT]
        self.assertEqual(nested[-1], "subagent.finished")

    async def test_external_task_cancellation_propagates(self):
        child = BlockingModel()
        task = asyncio.create_task(self.registry(child).execute(delegate()))
        await asyncio.wait_for(child.started.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(child.stopped.is_set())

    async def test_timeout_cleans_up_child(self):
        child = BlockingModel()
        context = ExecutionContext(workspace=Path.cwd(), default_timeout_seconds=.01,
                                   max_timeout_seconds=.01)
        result = await self.registry(child, context=context).execute(delegate())
        self.assertEqual(result.error.message, "Subagent exceeded its time limit")
        self.assertTrue(child.stopped.is_set())

    async def test_output_is_bounded_and_marked(self):
        context = ExecutionContext(workspace=Path.cwd(), max_output_chars=800)
        child = FakeModel([ModelResponse(content="x" * 3000)])
        result = await self.registry(child, context=context).execute(delegate())
        self.assertIn("truncated", result.output["output"])
        self.assertLess(len(result.output["output"]), 800)
        self.assertTrue(result.output["output_truncated"])
        self.assertEqual(result.output["report_chars"], 3000)


class ParallelSubagentTests(unittest.IsolatedAsyncioTestCase):
    async def test_configured_schema_defaults_cap_and_explicit_budgets(self):
        from nemo.core.contracts.model_config import SubagentConfig
        from nemo.core.runtime.subagents import SubagentManager
        manager = SubagentManager(FakeModel([ModelResponse(content="done"), ModelResponse(content="done")]),
                                  (), context=ContextBuilder(system_prompt="Child"), policy=ReadOnlyPolicy(),
                                  limits=SubagentConfig(default_max_steps=22, max_steps=60, max_model_calls=240))
        self.addAsyncCleanup(manager.end_run)
        registry = ToolRegistry(manager.tools(), context=ExecutionContext(workspace=Path.cwd()))
        manager.begin_run()
        schema = next(t.parameters for t in registry.schemas() if t.name == "spawn_subagents")
        step_schema = schema["$defs"]["DelegateArguments"]["properties"]["max_steps"]
        self.assertEqual((step_schema["default"], step_schema["maximum"]), (22, 60))
        self.assertEqual(manager.budget.limit, 240)
        for value in (61, 0, True, "30"):
            refused = await registry.execute(ToolCall(id="bad", name="spawn_subagents",
                arguments={"tasks": [{"task": "valid"}, {"task": "invalid", "max_steps": value}]}))
            self.assertEqual(refused.error.code, "invalid_arguments")
            self.assertEqual(manager.jobs, {})
            self.assertEqual(manager.budget.used, 0)
        events = []
        accepted = await registry.execute(ToolCall(id="ok", name="spawn_subagents",
            arguments={"tasks": [{"task": "Default"}, {"task": "Explicit", "max_steps": 50}]}), on_event=events.append)
        self.assertIsNone(accepted.error)
        self.assertEqual([e.payload["max_steps"] for e in events], [22, 50])
        await asyncio.gather(*(j.worker for j in manager.jobs.values()))
        self.assertEqual(manager.budget.used, 2)


    async def test_full_report_pages_and_events_preserve_tail_with_small_output_limit(self):
        import json
        report = ('中文证据\n"quoted"\t' * 1000) + "END-OF-REPORT"
        manager, registry = self.setup_manager(FakeModel([ModelResponse(content=report)]),
            context=ExecutionContext(workspace=Path.cwd(), run_id="parent", max_output_chars=700))
        manager.begin_run()
        events = []
        spawn = await registry.execute(ToolCall(id="spawn", name="spawn_subagents",
            arguments={"tasks": [{"task": "Inspect", "max_steps": 1}]}), on_event=events.append)
        child_id = spawn.output["tasks"][0]["run_id"]
        await asyncio.gather(*(j.worker for j in manager.jobs.values()))
        preview = await registry.execute(ToolCall(id="get", name="get_subagents", arguments={}))
        item = preview.output["tasks"][0]
        self.assertEqual(item["report_chars"], len(report))
        self.assertTrue(item["output_truncated"])
        self.assertEqual(item["status"], "limit_reached")
        self.assertNotIn("END-OF-REPORT", item["output"])
        offset, chunks = 0, []
        while True:
            page = await registry.execute(ToolCall(id="read", name="read_subagent_report",
                arguments={"run_id": child_id, "offset": offset, "limit": 8000}))
            self.assertIsNone(page.error)
            self.assertLessEqual(len(json.dumps(page.output, ensure_ascii=False)), 700)
            chunks.append(page.output["text"])
            next_offset = page.output["next_offset"]
            if next_offset is None:
                break
            self.assertGreater(next_offset, offset)
            offset = next_offset
        self.assertEqual("".join(chunks), report)
        stored = [e for e in events if e.type == "subagent.report"]
        self.assertTrue(all(len(e.payload["text"]) <= 4000 for e in stored))
        self.assertEqual("".join(e.payload["text"] for e in stored), report)
        self.assertEqual(manager.budget.used, 1)
        invalid = await registry.execute(ToolCall(id="bad", name="read_subagent_report",
            arguments={"run_id": child_id, "offset": len(report) + 1}))
        self.assertEqual(invalid.error.code, "execution_error")
        other = registry.context.model_copy(update={"run_id": "other"})
        from nemo.core.runtime.subagents import ReadReportArguments
        from nemo.core.contracts.errors import ToolFailure
        with self.assertRaises(ToolFailure):
            manager.read_report(ReadReportArguments(run_id=child_id), other)
        await manager.end_run()
        with self.assertRaises(ToolFailure):
            manager.read_report(ReadReportArguments(run_id=child_id), registry.context)

    async def test_report_not_ready_unknown_and_empty_reports(self):
        manager, registry = self.setup_manager(BlockingModel())
        manager.begin_run()
        spawned = await registry.execute(ToolCall(id="spawn", name="spawn_subagents",
            arguments={"tasks": [{"task": "wait"}]}))
        child_id = spawned.output["tasks"][0]["run_id"]
        for args in ({"run_id": child_id}, {"run_id": "unknown"}):
            result = await registry.execute(ToolCall(id="read", name="read_subagent_report", arguments=args))
            self.assertEqual(result.error.code, "execution_error")
        await manager.cancel(list(manager.jobs.values()))
        result = await registry.execute(ToolCall(id="read", name="read_subagent_report", arguments={"run_id": child_id}))
        self.assertEqual(result.output["text"], "")
        self.assertEqual(result.output["total_chars"], 0)
        self.assertIsNone(result.output["next_offset"])
        for args in ({"offset": -1}, {"offset": True}, {"limit": 0}, {"limit": 8001}):
            result = await registry.execute(ToolCall(id="read", name="read_subagent_report",
                arguments={"run_id": child_id, **args}))
            self.assertEqual(result.error.code, "invalid_arguments")

    async def test_resume_preserves_history_reports_identity_and_cumulative_budget(self):
        model = FakeModel([
            ModelResponse(tool_calls=(ToolCall(id="a", name="add", arguments={"a": 20, "b": 22}),)),
            ModelResponse(content="42 confirmed; negative inputs unchecked."),
            ModelResponse(content="Continued with prior evidence."),
        ])
        manager, registry = self.setup_manager(model)
        manager.begin_run()
        first_events, second_events = [], []
        spawned = await registry.execute(ToolCall(id="spawn", name="spawn_subagents",
            arguments={"tasks": [{"task": "Inspect", "max_steps": 2}]}), on_event=first_events.append)
        child_id = spawned.output["tasks"][0]["run_id"]
        job = manager.jobs[child_id]
        await job.worker
        runtime = job.runtime
        self.assertEqual(job.status, "limit_reached")
        self.assertEqual(manager.budget.used, 2)
        resumed = await registry.execute(ToolCall(id="resume", name="resume_subagent",
            arguments={"run_id": child_id, "prompt": "Check the remaining inputs", "max_steps": 3}), on_event=second_events.append)
        self.assertIsNone(resumed.error)
        self.assertEqual(resumed.output["tasks"][0]["round"], 2)
        old = await registry.execute(ToolCall(id="report", name="read_subagent_report", arguments={"run_id": child_id, "round": 1}))
        self.assertIn("negative inputs unchecked", old.output["text"])
        duplicate = await registry.execute(ToolCall(id="dup", name="resume_subagent", arguments={"run_id": child_id}))
        self.assertEqual(duplicate.error.code, "execution_error")
        await job.worker
        self.assertIs(job.runtime, runtime)
        self.assertEqual((job.status, job.steps, len(manager.jobs), manager.budget.used), ("completed", 3, 1, 3))
        history = model.requests[2].messages
        self.assertEqual([m.content for m in history if m.role == "user"], ["Inspect", "Check the remaining inputs"])
        self.assertTrue(any(m.tool_result and m.tool_result.output == 42 for m in history))
        self.assertTrue(any(m.content == "42 confirmed; negative inputs unchecked." for m in history))
        self.assertEqual([e.seq for e in first_events + second_events], list(range(1, job.seq + 1)))
        self.assertEqual(second_events[0].type, "subagent.resumed")
        self.assertTrue(all(e.payload["round"] == 2 for e in second_events))
        self.assertEqual(second_events[-1].step, 3)
        latest = await registry.execute(ToolCall(id="latest", name="read_subagent_report", arguments={"run_id": child_id}))
        self.assertEqual((latest.output["round"], latest.output["text"]), (2, "Continued with prior evidence."))
        self.assertNotIn("resume_subagent", [t.name for t in model.requests[2].tools])

    async def test_resume_rejects_running_cancelled_failed_unknown_foreign_and_closed_jobs(self):
        manager, registry = self.setup_manager(BlockingModel())
        manager.begin_run()
        spawned = await registry.execute(ToolCall(id="spawn", name="spawn_subagents", arguments={"tasks": [{"task": "Block"}]}))
        job = manager.jobs[spawned.output["tasks"][0]["run_id"]]
        for child_id in (job.run_id, "unknown"):
            rejected = await registry.execute(ToolCall(id="resume", name="resume_subagent", arguments={"run_id": child_id}))
            self.assertEqual(rejected.error.code, "execution_error")
        await manager.cancel([job])
        rejected = await registry.execute(ToolCall(id="resume", name="resume_subagent", arguments={"run_id": job.run_id}))
        self.assertEqual(rejected.error.code, "execution_error")
        from nemo.core.runtime.subagents import ResumeArguments
        from nemo.core.contracts.errors import ToolFailure
        with self.assertRaises(ToolFailure):
            manager.resume(ResumeArguments(run_id=job.run_id), registry.context.model_copy(update={"run_id": "foreign"}), None)
        await manager.end_run()
        with self.assertRaises(ToolFailure):
            manager.resume(ResumeArguments(run_id=job.run_id), registry.context, None)
        failed_manager, failed_registry = self.setup_manager(FakeModel([]))
        failed_manager.begin_run()
        created = await failed_registry.execute(ToolCall(id="spawn", name="spawn_subagents", arguments={"tasks": [{"task": "Fail"}]}))
        failed_id = created.output["tasks"][0]["run_id"]
        await failed_manager.jobs[failed_id].worker
        refused = await failed_registry.execute(ToolCall(id="resume", name="resume_subagent", arguments={"run_id": failed_id}))
        self.assertEqual(refused.error.code, "execution_error")

    async def test_resume_admission_has_no_side_effects_and_never_resets_budget(self):
        manager, registry = self.setup_manager(FakeModel([ModelResponse(content="Original")]), calls=1)
        manager.begin_run()
        created = await registry.execute(ToolCall(id="spawn", name="spawn_subagents", arguments={"tasks": [{"task": "Inspect"}]}))
        job = manager.jobs[created.output["tasks"][0]["run_id"]]
        await job.worker
        for steps in (0, 41, True, "3"):
            rejected = await registry.execute(ToolCall(id="resume", name="resume_subagent", arguments={"run_id": job.run_id, "max_steps": steps}))
            self.assertEqual(rejected.error.code, "invalid_arguments")
        exhausted = await registry.execute(ToolCall(id="resume", name="resume_subagent", arguments={"run_id": job.run_id}))
        self.assertEqual(exhausted.error.code, "execution_error")
        self.assertEqual((job.round, job.output, manager.budget.used), (1, "Original", 1))
        manager.budget.limit = 4
        from nemo.core.runtime.subagents import ResumeArguments
        from nemo.core.contracts.errors import ToolFailure
        with self.assertRaises(ToolFailure):
            manager.resume(ResumeArguments(run_id=job.run_id), registry.context.model_copy(update={"max_output_chars": 20}), None)
        self.assertEqual((job.round, job.seq, job.output), (1, job.seq, "Original"))
        self.assertEqual(manager.budget.used, 1)

    async def test_resumed_compaction_is_budgeted_and_checkpoint_is_reused(self):
        from nemo.core.context.compaction import ContextWindow
        from nemo.core.runtime.subagents import SubagentManager
        model = FakeModel([ModelResponse(content="E" * 1000), ModelResponse(content="Earlier evidence summary"),
                           ModelResponse(content="Second report"), ModelResponse(content="Third report")])
        manager = SubagentManager(model, (AddTool(),), context=ContextBuilder(system_prompt="Child"),
                                  context_window=ContextWindow(4000, 512), policy=ReadOnlyPolicy())
        self.addAsyncCleanup(manager.end_run)
        registry = ToolRegistry(manager.tools(), context=ExecutionContext(workspace=Path.cwd()))
        manager.begin_run()
        events = []
        created = await registry.execute(ToolCall(id="spawn", name="spawn_subagents", arguments={"tasks": [{"task": "A" * 1000}]}))
        job = manager.jobs[created.output["tasks"][0]["run_id"]]
        await job.worker
        resumed = await registry.execute(ToolCall(id="resume", name="resume_subagent",
            arguments={"run_id": job.run_id, "prompt": "B" * 1600}), on_event=events.append)
        self.assertIsNone(resumed.error)
        await job.worker
        self.assertEqual(job.status, "completed")
        self.assertEqual(manager.budget.used, 3)
        self.assertEqual(model.requests[1].tools, ())
        self.assertTrue(any(e.type == "context.compacted" for e in events))
        checkpoint = job.runtime.context_engine.checkpoint
        self.assertIsNotNone(checkpoint)
        await registry.execute(ToolCall(id="resume", name="resume_subagent", arguments={"run_id": job.run_id, "prompt": "One follow-up"}))
        await job.worker
        self.assertEqual((job.round, job.steps, manager.budget.used), (3, 3, 4))
        self.assertIs(job.runtime.context_engine.checkpoint, checkpoint)
        self.assertIn("Earlier evidence summary", model.requests[-1].messages[1].content)

    async def test_resumed_worker_obeys_concurrency_and_cancel_waits_for_cleanup(self):
        from nemo.testing.fakes import CallbackModel, CleanupBlockingModel
        gate = CleanupBlockingModel()
        self.addCleanup(gate.cleanup_release.set)
        async def respond(request):
            latest = [m.content for m in request.messages if m.role == "user"][-1]
            if latest == "First":
                return ModelResponse(content="Initial report")
            return await gate.generate(request)
        manager, registry = self.setup_manager(CallbackModel(respond), concurrent=1)
        manager.begin_run()
        created = await registry.execute(ToolCall(id="spawn", name="spawn_subagents", arguments={"tasks": [{"task": "First"}]}))
        job = manager.jobs[created.output["tasks"][0]["run_id"]]
        await job.worker
        await registry.execute(ToolCall(id="resume", name="resume_subagent", arguments={"run_id": job.run_id, "prompt": "Continue"}))
        await asyncio.wait_for(gate.started.wait(), 1)
        other = await registry.execute(ToolCall(id="spawn", name="spawn_subagents", arguments={"tasks": [{"task": "Other"}]}))
        other_job = manager.jobs[other.output["tasks"][0]["run_id"]]
        await asyncio.sleep(0)
        self.assertEqual(other_job.status, "queued")
        cleanup = asyncio.create_task(manager.end_run())
        await asyncio.wait_for(gate.cleanup_started.wait(), 1)
        self.assertFalse(cleanup.done())
        gate.cleanup_release.set()
        await asyncio.wait_for(cleanup, 1)
        self.assertEqual((job.status, other_job.status), ("cancelled", "cancelled"))
        self.assertTrue(all(j.worker.done() for j in manager.jobs.values()))
        self.assertEqual(job.reports[1].output, "Initial report")
        self.assertEqual(job.reports[2].status, "cancelled")

    async def test_resumed_round_timeout_preserves_previous_report(self):
        from nemo.testing.fakes import CallbackModel
        gate = BlockingModel()
        async def respond(request):
            latest = [m.content for m in request.messages if m.role == "user"][-1]
            return ModelResponse(content="First report") if latest == "First" else await gate.generate(request)
        context = ExecutionContext(workspace=Path.cwd(), default_timeout_seconds=.02, max_timeout_seconds=.02)
        manager, registry = self.setup_manager(CallbackModel(respond), context=context)
        manager.begin_run()
        created = await registry.execute(ToolCall(id="spawn", name="spawn_subagents", arguments={"tasks": [{"task": "First"}]}))
        job = manager.jobs[created.output["tasks"][0]["run_id"]]
        await job.worker
        await registry.execute(ToolCall(id="resume", name="resume_subagent", arguments={"run_id": job.run_id}))
        await job.worker
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.error, "Subagent exceeded its time limit")
        self.assertTrue(gate.stopped.is_set())
        self.assertEqual(job.reports[1].output, "First report")
        self.assertEqual(manager.budget.used, 2)

    def setup_manager(self, model, *, concurrent=3, tasks=8, calls=40, context=None):
        from nemo.core.runtime.subagents import SubagentManager
        manager = SubagentManager(model, (AddTool(),), context=ContextBuilder(system_prompt="Child instructions"),
                                  policy=ReadOnlyPolicy(), max_concurrent=concurrent,
                                  max_tasks=tasks, max_model_calls=calls)
        registry = ToolRegistry(manager.tools(), context=context or ExecutionContext(workspace=Path.cwd()))
        self.addAsyncCleanup(manager.end_run)
        return manager, registry

    async def test_spawn_result_overflow_does_not_start_tasks_or_consume_capacity(self):
        gate = BlockingModel()
        manager, registry = self.setup_manager(
            gate, context=ExecutionContext(workspace=Path.cwd(), max_output_chars=700))
        manager.begin_run()
        events = []
        refused = await registry.execute(ToolCall(id="large", name="spawn_subagents",
            arguments={"tasks": [{"task": str(i)} for i in range(8)]}), on_event=events.append)
        self.assertEqual(refused.error.code, "execution_error")
        self.assertEqual(manager.jobs, {})
        self.assertEqual(events, [])
        self.assertEqual(manager.budget.used, 0)
        self.assertFalse(gate.started.is_set())
        retry = await registry.execute(ToolCall(id="retry", name="spawn_subagents",
            arguments={"tasks": [{"task": "Smaller batch"}]}))
        self.assertIsNone(retry.error)
        self.assertEqual(len(manager.jobs), 1)
        self.assertEqual(retry.output["tasks"][0]["run_id"], next(iter(manager.jobs)))

    async def test_spawn_queue_events_preserve_individual_step_budgets(self):
        manager, registry = self.setup_manager(BlockingModel())
        manager.begin_run()
        events = []
        result = await registry.execute(ToolCall(id="batch", name="spawn_subagents",
            arguments={"tasks": [{"task": "Small", "max_steps": 1},
                                  {"task": "Large", "max_steps": 4}]}), on_event=events.append)
        self.assertIsNone(result.error)
        queued = [event for event in events if event.type == "subagent.queued"]
        self.assertEqual([event.payload["max_steps"] for event in queued], [1, 4])
        self.assertEqual([event.run_id for event in queued],
                         [task["run_id"] for task in result.output["tasks"]])

    async def test_overlapping_cancels_preserve_child_resource_cleanup(self):
        from nemo.testing.fakes import CleanupBlockingModel
        model = CleanupBlockingModel()
        manager, registry = self.setup_manager(model)
        self.addCleanup(model.cleanup_release.set)
        manager.begin_run()
        events = []
        spawned = await registry.execute(ToolCall(id="spawn", name="spawn_subagents",
            arguments={"tasks": [{"task": "Investigate"}]}), on_event=events.append)
        job = manager.jobs[spawned.output["tasks"][0]["run_id"]]
        await asyncio.wait_for(model.started.wait(), 1)
        first = asyncio.create_task(manager.cancel([job]))
        await asyncio.wait_for(model.cleanup_started.wait(), 1)
        second = asyncio.create_task(manager.cancel([job]))
        # Cancelling a control call must not send a second cancellation into cleanup.
        await asyncio.sleep(0)
        first.cancel()
        model.cleanup_release.set()
        outcomes = await asyncio.wait_for(asyncio.gather(first, second, return_exceptions=True), 1)
        self.assertIsInstance(outcomes[0], asyncio.CancelledError)
        self.assertIsNone(outcomes[1])
        self.assertTrue(model.stopped.is_set())
        self.assertTrue(job.worker.done())
        self.assertEqual(job.status, "cancelled")
        self.assertEqual(sum(e.type == "subagent.finished" for e in events), 1)

    async def test_repeated_parent_cancellation_waits_for_child_cleanup_and_terminal(self):
        from nemo.testing.fakes import CallbackModel, CleanupBlockingModel
        child = CleanupBlockingModel()
        calls = 0

        async def respond(request):
            nonlocal calls
            if not any(t.name == "spawn_subagents" for t in request.tools):
                return await child.generate(request)
            calls += 1
            if calls == 1:
                return ModelResponse(tool_calls=(ToolCall(id="spawn", name="spawn_subagents",
                    arguments={"tasks": [{"task": "Background"}]}),))
            await child.started.wait()
            return ModelResponse(content="Finished parent work")

        manager, registry = self.setup_manager(CallbackModel(respond))
        events = []
        worker = asyncio.create_task(AgentRuntime(manager.model, registry).run("Parent", on_event=events.append))
        self.addAsyncCleanup(self.cancel_worker, worker)
        self.addCleanup(child.cleanup_release.set)
        await asyncio.wait_for(child.cleanup_started.wait(), 1)
        worker.cancel()
        await asyncio.sleep(0)
        worker.cancel()
        await asyncio.sleep(0)
        self.assertFalse(worker.done())
        self.assertFalse(any(e.type in {"run.completed", "run.cancelled"} for e in events))
        child.cleanup_release.set()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(worker, 1)
        self.assertTrue(child.stopped.is_set())
        self.assertTrue(all(j.worker.done() for j in manager.jobs.values()))
        self.assertEqual(events[-1].type, "run.cancelled")
        self.assertEqual(sum(e.type == "run.cancelled" for e in events), 1)
        self.assertEqual([e.seq for e in events], list(range(1, len(events) + 1)))

    async def test_cancelled_control_call_finalizes_tasks_that_never_started(self):
        manager, registry = self.setup_manager(BlockingModel())
        manager.begin_run()
        events = []
        spawned = await registry.execute(ToolCall(id="spawn", name="spawn_subagents",
            arguments={"tasks": [{"task": "Queued"}]}), on_event=events.append)
        job = manager.jobs[spawned.output["tasks"][0]["run_id"]]
        # Neither the child nor the control call has run yet.
        cancel = asyncio.create_task(manager.cancel([job]))
        job.worker.cancel()
        await asyncio.sleep(0)
        cancel.cancel()
        await asyncio.gather(cancel, return_exceptions=True)
        await manager.end_run()
        self.assertEqual(job.status, "cancelled")
        self.assertTrue(job.worker.done())
        self.assertEqual(sum(e.type == "subagent.finished" for e in events), 1)

    async def test_parallel_children_and_parent_progress_with_bounded_concurrency(self):
        from nemo.testing.fakes import CallbackModel
        gates = [BlockingModel() for _ in range(3)]
        progressed = asyncio.Event()
        parent_calls = 0
        async def respond(request):
            nonlocal parent_calls
            if not any(t.name == "spawn_subagents" for t in request.tools):
                task = next(m.content for m in request.messages if m.role == "user")
                return await gates[int(task)].generate(request)
            parent_calls += 1
            if parent_calls == 1:
                return ModelResponse(tool_calls=(ToolCall(id="spawn", name="spawn_subagents",
                    arguments={"tasks": [{"task": str(i)} for i in range(3)]}),))
            if parent_calls == 2:
                ids = next(m.tool_result.output["tasks"] for m in request.messages if m.role == "tool")
                progressed.set()
                return ModelResponse(tool_calls=(ToolCall(id="wait", name="wait_subagents",
                    arguments={"run_ids": [j["run_id"] for j in ids], "mode": "all"}),))
            return ModelResponse(content="All findings collected")
        model = CallbackModel(respond)
        manager, registry = self.setup_manager(model, concurrent=2)
        worker = asyncio.create_task(AgentRuntime(manager.model, registry).run("Parent"))
        self.addAsyncCleanup(self.cancel_worker, worker)
        await asyncio.wait_for(progressed.wait(), 1)
        await asyncio.wait_for(asyncio.gather(gates[0].started.wait(), gates[1].started.wait()), 1)
        self.assertFalse(gates[2].started.is_set())
        self.assertEqual([j.status for j in manager.jobs.values()], ["running", "running", "queued"])
        gates[0].release.set()
        await asyncio.wait_for(gates[2].started.wait(), 1)
        gates[1].release.set()
        gates[2].release.set()
        result = await asyncio.wait_for(worker, 1)
        self.assertEqual(result.state.output, "All findings collected")
        self.assertTrue(all(j.status == "completed" for j in manager.jobs.values()))
        self.assertEqual(manager.budget.used, 6)  # 3 parent + 3 child requests.
        nested = [e for e in result.events if e.type == EventType.SUBAGENT_EVENT]
        self.assertTrue(all(e.payload["tool_call_id"] == "spawn" for e in nested))
        self.assertEqual([e.seq for e in result.events], list(range(1, len(result.events) + 1)))
        self.assertEqual(result.events[-1].type, "run.completed")
        self.assertTrue(all(j.worker.done() for j in manager.jobs.values()))

    async def cancel_worker(self, worker):
        if not worker.done():
            worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

    async def test_wait_timeout_keeps_work_running_and_cancel_drains(self):
        manager, registry = self.setup_manager(BlockingModel(), concurrent=1)
        manager.begin_run()
        spawned = await registry.execute(ToolCall(id="spawn", name="spawn_subagents",
            arguments={"tasks": [{"task": "First"}, {"task": "Queued"}]}))
        ids = [j["run_id"] for j in spawned.output["tasks"]]
        waited = await registry.execute(ToolCall(id="wait", name="wait_subagents",
            arguments={"run_ids": ids, "timeout_seconds": .01, "mode": "all"}))
        self.assertEqual(waited.output["pending"], ids)
        self.assertFalse(manager.jobs[ids[0]].worker.done())
        for child_id in reversed(ids):
            cancelled = await registry.execute(ToolCall(id="cancel", name="cancel_subagent",
                arguments={"run_id": child_id}))
            self.assertEqual(cancelled.output["tasks"][0]["status"], "cancelled")
            self.assertTrue(manager.jobs[child_id].worker.done())
        self.assertTrue(manager.model.model.stopped.is_set())

    async def test_task_limit_ownership_invalid_batch_and_no_recursion(self):
        manager, registry = self.setup_manager(BlockingModel(), tasks=2, concurrent=1)
        manager.begin_run()
        bad = await registry.execute(ToolCall(id="bad", name="spawn_subagents",
            arguments={"tasks": [{"task": "Valid"}, {"task": "", "max_steps": True}]}))
        self.assertEqual(bad.error.code, "invalid_arguments")
        self.assertEqual(manager.jobs, {})
        await registry.execute(ToolCall(id="spawn", name="spawn_subagents",
            arguments={"tasks": [{"task": "A"}, {"task": "B"}]}))
        overflow = await registry.execute(ToolCall(id="overflow", name="spawn_subagents",
            arguments={"tasks": [{"task": "C"}]}))
        self.assertIn("task limit", overflow.error.message)
        unknown = await registry.execute(ToolCall(id="unknown", name="get_subagents",
            arguments={"run_ids": ["another-parent-child"]}))
        self.assertEqual(unknown.error.code, "execution_error")
        other = ToolRegistry(manager.tools(), context=ExecutionContext(workspace=Path.cwd(), run_id="other"))
        refused = await other.execute(ToolCall(id="other", name="get_subagents", arguments={}))
        self.assertEqual(refused.error.code, "execution_error")
        self.assertEqual([t.name for t in manager.child_tools], ["add"])

    async def test_completed_parent_cancels_uncollected_children_before_terminal(self):
        from nemo.testing.fakes import CallbackModel
        gate = BlockingModel()
        parent_calls = 0
        async def respond(request):
            nonlocal parent_calls
            if not any(t.name == "spawn_subagents" for t in request.tools):
                return await gate.generate(request)
            parent_calls += 1
            if parent_calls == 1:
                return ModelResponse(tool_calls=(ToolCall(id="spawn", name="spawn_subagents",
                    arguments={"tasks": [{"task": "Background"}]}),))
            await gate.started.wait()
            return ModelResponse(content="Parent finished early")
        manager, registry = self.setup_manager(CallbackModel(respond))
        result = await AgentRuntime(manager.model, registry).run("Parent")
        self.assertEqual(result.state.status, RunStatus.COMPLETED)
        self.assertTrue(gate.stopped.is_set())
        self.assertEqual(next(iter(manager.jobs.values())).status, "cancelled")
        self.assertEqual(result.events[-1].type, "run.completed")
        terminal = [e for e in result.events if e.type == EventType.SUBAGENT_EVENT][-1]
        self.assertEqual(terminal.payload["child_payload"]["status"], "cancelled")
        self.assertLess(terminal.seq, result.events[-1].seq)

    async def test_parent_cancellation_cleans_up_active_and_queued_children(self):
        from nemo.testing.fakes import CallbackModel
        gate = BlockingModel()
        calls = 0
        async def respond(request):
            nonlocal calls
            if not any(t.name == "spawn_subagents" for t in request.tools):
                return await gate.generate(request)
            calls += 1
            if calls == 1:
                return ModelResponse(tool_calls=(ToolCall(id="spawn", name="spawn_subagents",
                    arguments={"tasks": [{"task": "A"}, {"task": "B"}]}),))
            await asyncio.Event().wait()
        manager, registry = self.setup_manager(CallbackModel(respond), concurrent=1)
        cancel = asyncio.Event()
        worker = asyncio.create_task(AgentRuntime(manager.model, registry).run("Parent", cancel=cancel))
        self.addAsyncCleanup(self.cancel_worker, worker)
        await asyncio.wait_for(gate.started.wait(), 1)
        cancel.set()
        result = await asyncio.wait_for(worker, 1)
        self.assertEqual(result.state.status, RunStatus.CANCELLED)
        self.assertTrue(gate.stopped.is_set())
        self.assertTrue(all(j.status == "cancelled" and j.worker.done() for j in manager.jobs.values()))

    async def test_shared_call_budget_counts_generate_stream_and_rejects_excess(self):
        from nemo.core.runtime.subagents import BudgetedModel, ModelCallBudget
        from nemo.core.contracts.types import ModelRequest
        request = ModelRequest(messages=(), tools=())
        budget = ModelCallBudget(2)
        model = BudgetedModel(FakeModel([ModelResponse(content="A"), ModelResponse(content="B")]), budget)
        await model.generate(request)
        parts = [part async for part in model.stream(request)]
        self.assertEqual(parts[-1].content, "B")
        with self.assertRaisesRegex(Exception, "budget exhausted"):
            await model.generate(request)
        self.assertEqual(budget.used, 2)

    async def test_child_budget_failure_and_manager_reuse_are_bounded(self):
        manager, registry = self.setup_manager(FakeModel([ModelResponse(content="Only request")]), calls=1)
        manager.begin_run()
        spawn = await registry.execute(ToolCall(id="spawn", name="spawn_subagents",
            arguments={"tasks": [{"task": "A"}, {"task": "B"}]}))
        ids = [j["run_id"] for j in spawn.output["tasks"]]
        await registry.execute(ToolCall(id="wait", name="wait_subagents",
            arguments={"run_ids": ids, "mode": "all"}))
        self.assertEqual(manager.budget.used, 1)
        self.assertEqual([j.status for j in manager.jobs.values()], ["completed", "failed"])
        self.assertIn("budget exhausted", manager.jobs[ids[1]].error)
        await manager.end_run()
        manager.begin_run()
        self.assertEqual(manager.budget.used, 0)
        old = await registry.execute(ToolCall(id="old", name="get_subagents", arguments={"run_ids": ids}))
        self.assertEqual(old.error.code, "execution_error")

    async def test_any_wait_returns_finished_child_without_cancelling_sibling(self):
        from nemo.testing.fakes import CallbackModel
        gate = BlockingModel()
        async def respond(request):
            label = next(m.content for m in request.messages if m.role == "user")
            if label == "Fast":
                return ModelResponse(content="Evidence")
            return await gate.generate(request)
        manager, registry = self.setup_manager(CallbackModel(respond))
        manager.begin_run()
        spawned = await registry.execute(ToolCall(id="spawn", name="spawn_subagents",
            arguments={"tasks": [{"task": "Fast"}, {"task": "Slow"}]}))
        ids = [j["run_id"] for j in spawned.output["tasks"]]
        result = await registry.execute(ToolCall(id="wait", name="wait_subagents",
            arguments={"run_ids": ids, "mode": "any"}))
        self.assertEqual(result.output["tasks"][0]["status"], "completed")
        self.assertEqual(result.output["pending"], [ids[1]])
        await asyncio.wait_for(gate.started.wait(), 1)
        self.assertFalse(gate.stopped.is_set())

    async def test_queue_timeout_finishes_and_cleans_up_all_children(self):
        gate = BlockingModel()
        context = ExecutionContext(workspace=Path.cwd(), default_timeout_seconds=.01,
                                   max_timeout_seconds=.01)
        manager, registry = self.setup_manager(gate, concurrent=1, context=context)
        manager.begin_run()
        await registry.execute(ToolCall(id="spawn", name="spawn_subagents",
            arguments={"tasks": [{"task": "A"}, {"task": "B"}]}))
        await asyncio.wait_for(asyncio.gather(*(j.worker for j in manager.jobs.values())), 1)
        self.assertTrue(gate.stopped.is_set())
        self.assertTrue(all(j.status == "failed" and "time limit" in j.error for j in manager.jobs.values()))

    async def test_parent_budget_exhaustion_cancels_children(self):
        from nemo.testing.fakes import CallbackModel
        gate = BlockingModel()
        calls = 0
        async def respond(request):
            nonlocal calls
            if not any(t.name == "spawn_subagents" for t in request.tools):
                return await gate.generate(request)
            calls += 1
            if calls == 1:
                return ModelResponse(tool_calls=(ToolCall(id="spawn", name="spawn_subagents",
                    arguments={"tasks": [{"task": "Background"}]}),))
            await gate.started.wait()
            return ModelResponse(tool_calls=(ToolCall(id="status", name="get_subagents", arguments={}),))
        manager, registry = self.setup_manager(CallbackModel(respond), calls=3)
        result = await asyncio.wait_for(AgentRuntime(manager.model, registry).run("Parent"), 1)
        self.assertEqual(result.state.status, RunStatus.FAILED)
        self.assertIn("budget exhausted", result.state.error)
        self.assertEqual(manager.budget.used, 3)
        self.assertTrue(gate.stopped.is_set())
        self.assertTrue(all(j.worker.done() for j in manager.jobs.values()))

    async def test_results_are_aggregate_bounded_and_step_limit_drains_children(self):
        import json
        manager, registry = self.setup_manager(FakeModel([ModelResponse(content="\n" * 4000)]),
            context=ExecutionContext(workspace=Path.cwd(), max_output_chars=700))
        manager.begin_run()
        spawned = await registry.execute(ToolCall(id="spawn", name="spawn_subagents",
            arguments={"tasks": [{"task": "A"}]}))
        ids = [j["run_id"] for j in spawned.output["tasks"]]
        await asyncio.gather(*(j.worker for j in manager.jobs.values()))
        result = await registry.execute(ToolCall(id="get", name="get_subagents", arguments={"run_ids": ids}))
        self.assertIsNone(result.error)
        self.assertLessEqual(len(json.dumps(result.output)), 700)
        self.assertIn("truncated", result.output["tasks"][0]["output"])
        await manager.end_run()
        parent = FakeModel([ModelResponse(tool_calls=(ToolCall(id="spawn", name="spawn_subagents",
            arguments={"tasks": [{"task": "A"}, {"task": "B"}]}),))])
        next_manager, next_registry = self.setup_manager(parent)
        result = await AgentRuntime(next_manager.model, next_registry).run("Parent", max_steps=1)
        self.assertEqual(result.state.status, RunStatus.LIMIT_REACHED)
        self.assertTrue(all(j.worker.done() for j in next_manager.jobs.values()))
        self.assertEqual(result.events[-1].type, "run.limit_reached")

    async def test_limit_reached_child_returns_report_to_parent_within_call_budget(self):
        model = FakeModel([
            ModelResponse(tool_calls=(ToolCall(id="inspect", name="add", arguments={"a": 20, "b": 22}),)),
            ModelResponse(content="Confirmed: result is 42. Not checked: negative inputs."),
        ])
        manager, registry = self.setup_manager(model, calls=2)
        manager.begin_run()
        spawn = await registry.execute(ToolCall(id="spawn", name="spawn_subagents",
            arguments={"tasks": [{"task": "Inspect addition", "max_steps": 2}]}))
        result = await registry.execute(ToolCall(id="wait", name="wait_subagents",
            arguments={"run_ids": [spawn.output["tasks"][0]["run_id"]], "mode": "all"}))
        self.assertIsNone(result.error)
        child = result.output["tasks"][0]
        self.assertEqual(child["status"], "limit_reached")
        self.assertIn("Confirmed", child["output"])
        self.assertIn("Not checked", child["output"])
        self.assertEqual(child["steps"], 2)
        self.assertEqual(manager.budget.used, 2)
        self.assertEqual(model.requests[-1].tools, ())

    async def test_execution_details_and_report_are_forwarded_for_child_inspection(self):
        model = FakeModel([
            ModelResponse(content="Inspecting addition", tool_calls=(ToolCall(id="read", name="add", arguments={"a": 20, "b": 22}),)),
            ModelResponse(content="Result was 42; remaining checks are incomplete."),
        ])
        manager, registry = self.setup_manager(model)
        manager.begin_run()
        events = []
        spawned = await registry.execute(ToolCall(id="spawn", name="spawn_subagents",
            arguments={"tasks": [{"task": "Inspect addition", "max_steps": 2}]}), on_event=events.append)
        await registry.execute(ToolCall(id="wait", name="wait_subagents", arguments={
            "run_ids": [spawned.output["tasks"][0]["run_id"]], "mode": "all"}))
        self.assertEqual(events[0].payload["task"], "Inspect addition")
        completed = next(e for e in events if e.type == "model.completed")
        self.assertEqual(completed.payload["content"], "Inspecting addition")
        self.assertIn('"a": 20', completed.payload["tool_calls"][0]["arguments"])
        tool = next(e for e in events if e.type == "tool.completed")
        self.assertIn('"output":42', tool.payload["result"])
        self.assertEqual(events[-1].payload["output"], "Result was 42; remaining checks are incomplete.")
        self.assertEqual(events[-1].payload["status"], "limit_reached")
