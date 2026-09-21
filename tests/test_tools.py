import asyncio
import tempfile
import unittest
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from nemo.adapters.tools.filesystem import (
    EditFileTool,
    ListDirTool,
    ReadFileTool,
    WriteFileTool,
)
from nemo.adapters.tools.shell import RunShellTool
from nemo.core.context.builder import ContextBuilder
from nemo.core.contracts.tools import ExecutionContext, PolicyDecision
from nemo.core.contracts.types import RunStatus, ToolCall
from nemo.core.runtime.agent import AgentRuntime
from nemo.core.tools.policy import AllowAllPolicy, ReadOnlyPolicy
from nemo.core.tools.registry import ToolRegistry
from nemo.testing.fakes import AddTool, FakeModel

LOCAL_TOOLS = (ReadFileTool(), WriteFileTool(), EditFileTool(), ListDirTool(), RunShellTool())


def call(name, arguments, id="c1"):
    return ToolCall(id=id, name=name, arguments=arguments)


class LocalToolTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name).resolve()

    def context(self, **overrides):
        return ExecutionContext(workspace=self.workspace, **overrides)

    def registry(self, *, policy=None, context=None, tools=LOCAL_TOOLS, **kwargs):
        return ToolRegistry(
            tools, context=context or self.context(), policy=policy, **kwargs
        )

    def write(self, name, content):
        path = self.workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path


class FilesystemToolTests(LocalToolTestCase):
    async def test_read_file_returns_content_with_line_header(self):
        self.write("notes.txt", "one\ntwo\nthree\n")
        result = await self.registry().execute(call("read_file", {"path": "notes.txt"}))
        self.assertIsNone(result.error)
        self.assertIn("[notes.txt: lines 1-3 of 3]", result.output)
        self.assertIn("two", result.output)

    async def test_read_file_pages_by_line(self):
        self.write("big.txt", "".join(f"line {i}\n" for i in range(1, 11)))
        result = await self.registry().execute(
            call("read_file", {"path": "big.txt", "offset": 4, "limit": 2})
        )
        self.assertIn("[big.txt: lines 4-5 of 10]", result.output)
        self.assertIn("line 4", result.output)
        self.assertIn("line 5", result.output)
        self.assertNotIn("line 6", result.output)

    async def test_read_file_missing_and_binary(self):
        missing = await self.registry().execute(call("read_file", {"path": "nope.txt"}))
        self.assertEqual(missing.error.code, "execution_error")
        self.assertIn("file not found", missing.error.message)

        binary = self.workspace / "blob.bin"
        binary.write_bytes(b"\x00\x01\x02binary")
        blob = await self.registry().execute(call("read_file", {"path": "blob.bin"}))
        self.assertIn("binary file", blob.error.message)

    async def test_read_file_truncates_long_output(self):
        self.write("long.txt", "x" * 5000)
        result = await self.registry(context=self.context(max_output_chars=100)).execute(
            call("read_file", {"path": "long.txt"})
        )
        self.assertIn("[truncated: showing the first 100 of 5000 characters]", result.output)

    async def test_write_file_creates_and_refuses_silent_overwrite(self):
        first = await self.registry().execute(
            call("write_file", {"path": "new/note.txt", "content": "hello"})
        )
        self.assertIsNone(first.error)
        self.assertEqual((self.workspace / "new" / "note.txt").read_text(), "hello")

        again = await self.registry().execute(
            call("write_file", {"path": "new/note.txt", "content": "replaced"})
        )
        self.assertEqual(again.error.code, "execution_error")
        self.assertIn("already exists", again.error.message)
        self.assertEqual((self.workspace / "new" / "note.txt").read_text(), "hello")

        forced = await self.registry().execute(
            call("write_file", {"path": "new/note.txt", "content": "replaced", "overwrite": True})
        )
        self.assertIsNone(forced.error)
        self.assertEqual((self.workspace / "new" / "note.txt").read_text(), "replaced")

    async def test_edit_file_is_surgical_and_reports_ambiguity(self):
        self.write("code.py", "value = 1\nother = 1\n")
        ambiguous = await self.registry().execute(
            call("edit_file", {"path": "code.py", "old_string": "= 1", "new_string": "= 2"})
        )
        self.assertIn("appears 2 times", ambiguous.error.message)
        self.assertEqual((self.workspace / "code.py").read_text(), "value = 1\nother = 1\n")

        unique = await self.registry().execute(
            call("edit_file", {"path": "code.py", "old_string": "value = 1", "new_string": "value = 7"})
        )
        self.assertIsNone(unique.error)
        self.assertIn("replaced 1 occurrence", unique.output)
        self.assertEqual((self.workspace / "code.py").read_text(), "value = 7\nother = 1\n")

        missing = await self.registry().execute(
            call("edit_file", {"path": "code.py", "old_string": "absent", "new_string": "x"})
        )
        self.assertIn("was not found", missing.error.message)

    async def test_edit_file_replace_all(self):
        self.write("code.py", "a\na\n")
        result = await self.registry().execute(
            call(
                "edit_file",
                {"path": "code.py", "old_string": "a", "new_string": "b", "replace_all": True},
            )
        )
        self.assertIn("replaced 2 occurrence", result.output)
        self.assertEqual((self.workspace / "code.py").read_text(), "b\nb\n")

    async def test_list_dir_marks_directories_and_handles_empty(self):
        self.write("pkg/module.py", "")
        self.write("readme.md", "")
        result = await self.registry().execute(call("list_dir", {"path": "."}))
        self.assertIn("2 entries", result.output)
        self.assertIn("pkg/", result.output)
        self.assertIn("readme.md", result.output)

        not_a_dir = await self.registry().execute(call("list_dir", {"path": "readme.md"}))
        self.assertIn("not a directory", not_a_dir.error.message)

        empty_dir = self.workspace / "empty"
        empty_dir.mkdir()
        empty = await self.registry().execute(call("list_dir", {"path": "empty"}))
        self.assertIn("empty directory", empty.output)

    async def test_paths_may_not_escape_the_workspace(self):
        outside = await self.registry().execute(
            call("write_file", {"path": "../escaped.txt", "content": "nope"})
        )
        self.assertEqual(outside.error.code, "execution_error")
        self.assertIn("escapes the workspace", outside.error.message)
        self.assertFalse((self.workspace.parent / "escaped.txt").exists())

        absolute = await self.registry().execute(
            call("read_file", {"path": "/etc/hosts"})
        )
        self.assertIn("escapes the workspace", absolute.error.message)

    async def test_symlink_pointing_outside_is_rejected(self):
        target = self.workspace.parent / "outside.txt"
        target.write_text("secret", encoding="utf-8")
        self.addCleanup(target.unlink)
        link = self.workspace / "link.txt"
        link.symlink_to(target)

        result = await self.registry().execute(call("read_file", {"path": "link.txt"}))
        self.assertIn("escapes the workspace", result.error.message)


class ShellToolTests(LocalToolTestCase):
    async def test_runs_command_in_the_workspace(self):
        self.write("marker.txt", "content")
        result = await self.registry().execute(call("run_shell", {"command": "cat marker.txt"}))
        self.assertIsNone(result.error)
        self.assertIn("exit_code: 0", result.output)
        self.assertIn("content", result.output)

    async def test_non_zero_exit_is_output_not_a_failure(self):
        result = await self.registry().execute(
            call("run_shell", {"command": "echo bad >&2; exit 3"})
        )
        self.assertIsNone(result.error)
        self.assertIn("exit_code: 3", result.output)
        self.assertIn("bad", result.output)

    async def test_workdir_is_respected_and_bounded(self):
        self.write("sub/inner.txt", "inner")
        inside = await self.registry().execute(
            call("run_shell", {"command": "cat inner.txt", "workdir": "sub"})
        )
        self.assertIn("inner", inside.output)

        outside = await self.registry().execute(
            call("run_shell", {"command": "pwd", "workdir": ".."})
        )
        self.assertIn("escapes the workspace", outside.error.message)

    async def test_timeout_kills_the_whole_process_group(self):
        # The background subshell is a grandchild of the shell we start: killing
        # only the shell would leave it alive to create marker.txt a second later.
        result = await self.registry().execute(
            call(
                "run_shell",
                {"command": "(sleep 1; echo late > marker.txt) & sleep 5", "timeout_seconds": 0.3},
            )
        )
        self.assertEqual(result.error.code, "execution_error")
        self.assertIn("timed out", result.error.message)
        await asyncio.sleep(1.2)
        self.assertFalse((self.workspace / "marker.txt").exists())

    async def test_cancellation_kills_the_process_group_and_propagates(self):
        registry = self.registry()
        task = asyncio.create_task(
            registry.execute(
                call(
                    "run_shell",
                    {
                        "command": "(sleep 1; echo late > marker.txt) & sleep 5",
                        "timeout_seconds": 10,
                    },
                )
            )
        )
        await asyncio.sleep(0.2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await asyncio.sleep(1.2)
        self.assertFalse((self.workspace / "marker.txt").exists())

    async def test_timeout_is_capped_by_the_context(self):
        context = self.context(default_timeout_seconds=0.2, max_timeout_seconds=0.3)
        result = await self.registry(context=context).execute(
            call("run_shell", {"command": "sleep 5", "timeout_seconds": 60})
        )
        self.assertIn("timed out after 0.3s", result.error.message)

    async def test_large_output_is_truncated_in_the_middle(self):
        command = "python3 -c \"print('H' * 4000); print('T' * 4000)\""
        result = await self.registry(context=self.context(max_output_chars=200)).execute(
            call("run_shell", {"command": command})
        )
        self.assertIn("exit_code: 0", result.output)
        self.assertIn("characters omitted from the middle", result.output)
        self.assertLess(len(result.output), 500)


class PolicyTests(LocalToolTestCase):
    async def test_read_only_policy_denies_mutation(self):
        registry = self.registry(policy=ReadOnlyPolicy())
        allowed = await registry.execute(call("list_dir", {"path": "."}))
        self.assertIsNone(allowed.error)

        denied = await registry.execute(
            call("write_file", {"path": "x.txt", "content": "nope"})
        )
        self.assertEqual(denied.error.code, "denied")
        self.assertIn("read-only mode", denied.error.message)
        self.assertFalse((self.workspace / "x.txt").exists())

    async def test_policy_receives_tool_identity_and_arguments(self):
        seen = {}

        class RecordingPolicy:
            async def decide(self, *, facts, arguments, context):
                seen.update(
                    tool_name=facts.tool_name, read_only=facts.read_only,
                    summary=facts.summary, shell_command=facts.shell_command,
                    path=arguments.path, workspace=context.workspace,
                )
                return PolicyDecision(allowed=True)

        await self.registry(policy=RecordingPolicy()).execute(
            call("read_file", {"path": "anything.txt"})
        )
        self.assertEqual(seen["tool_name"], "read_file")
        self.assertTrue(seen["read_only"])
        self.assertEqual(seen["summary"], "read anything.txt")
        self.assertIsNone(seen["shell_command"])
        self.assertEqual(seen["path"], "anything.txt")
        self.assertEqual(seen["workspace"], self.workspace)

    async def test_allow_all_policy_is_the_default(self):
        self.assertIsInstance(self.registry().policy, AllowAllPolicy)


class RegistryBoundaryTests(LocalToolTestCase):
    async def test_summary_is_single_line_and_bounded(self):
        registry = self.registry()
        summary = registry.summary_for(call("run_shell", {"command": "echo 'a\nb'"}))
        self.assertEqual(summary, "$ echo 'a b'")

        long_command = "x" * 500
        bounded = registry.summary_for(call("run_shell", {"command": long_command}))
        self.assertEqual(len(bounded), 200)

    async def test_summary_is_absent_for_unknown_or_invalid_calls(self):
        registry = self.registry()
        self.assertIsNone(registry.summary_for(call("nope", {})))
        self.assertIsNone(registry.summary_for(call("run_shell", {"command": ""})))

    async def test_oversized_result_is_rejected_with_guidance(self):
        class BigTool:
            name = "big"
            description = "returns too much"
            arguments_type = _EmptyArguments
            read_only = True

            async def execute(self, arguments, context):
                return "x" * 5000

            def summarize(self, arguments):
                return "big"

        result = await self.registry(tools=(BigTool(),), max_result_chars=200).execute(
            call("big", {})
        )
        self.assertIn("exceeded 200 characters", result.error.message)

    async def test_unexpected_errors_stay_opaque_but_expected_ones_are_shown(self):
        class RudeTool:
            name = "rude"
            description = "raises a secret-bearing error"
            arguments_type = _EmptyArguments
            read_only = True

            async def execute(self, arguments, context):
                raise RuntimeError("api key sk-should-not-appear")

            def summarize(self, arguments):
                return "rude"

        result = await self.registry(tools=(RudeTool(),)).execute(call("rude", {}))
        self.assertEqual(result.error.message, "Tool execution failed")
        self.assertNotIn("sk-should-not-appear", str(result.error.message))

    async def test_tool_result_reaches_the_model(self):
        self.write("answer.txt", "42")
        model = FakeModel(
            [
                _tool_call_response(call("read_file", {"path": "answer.txt"})),
                _tool_call_response(None),
            ]
        )
        runtime = AgentRuntime(model, self.registry())
        result = await runtime.run("read answer.txt")
        self.assertEqual(result.state.status, RunStatus.COMPLETED)
        tool_message = result.state.messages[2]
        self.assertIn("42", tool_message.tool_result.output)


class _EmptyArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _tool_call_response(tool_call):
    from nemo.core.contracts.types import ModelResponse

    if tool_call is None:
        return ModelResponse(content="done")
    return ModelResponse(tool_calls=(tool_call,))


class SystemPromptTests(LocalToolTestCase):
    async def test_system_prompt_is_first_and_stable_across_turns(self):
        self.write("data.txt", "hello")
        model = FakeModel([_tool_call_response(call("read_file", {"path": "data.txt"})), _tool_call_response(None)])
        runtime = AgentRuntime(
            model, self.registry(), ContextBuilder(system_prompt="You are Nemo.")
        )
        await runtime.run("read it")

        first_request = model.requests[0].messages
        second_request = model.requests[1].messages
        self.assertEqual(first_request[0].role, "system")
        self.assertEqual(first_request[0].content, "You are Nemo.")
        self.assertEqual(
            [message.role for message in second_request[:3]],
            ["system", "user", "assistant"],
        )
        # The prefix must be byte-identical between turns, or prompt caching dies.
        self.assertEqual(second_request[0].model_dump(), first_request[0].model_dump())

    async def test_system_prompt_absent_by_default(self):
        model = FakeModel([_tool_call_response(None)])
        await AgentRuntime(model, self.registry()).run("hi")
        self.assertEqual([m.role for m in model.requests[0].messages], ["user"])

    async def test_agent_state_does_not_contain_the_system_prompt(self):
        model = FakeModel([_tool_call_response(None)])
        runtime = AgentRuntime(
            model, self.registry(), ContextBuilder(system_prompt="You are Nemo.")
        )
        result = await runtime.run("hi")
        self.assertEqual([m.role for m in result.state.messages], ["user", "assistant"])


class TraceSummaryTests(LocalToolTestCase):
    async def test_tool_events_carry_a_readable_summary(self):
        self.write("trace.txt", "data")
        model = FakeModel(
            [
                _tool_call_response(call("read_file", {"path": "trace.txt"})),
                _tool_call_response(None),
            ]
        )
        result = await AgentRuntime(model, self.registry()).run("read it")
        started = next(e for e in result.events if e.type == "tool.started")
        self.assertEqual(started.payload["summary"], "read trace.txt")
        completed = next(e for e in result.events if e.type == "tool.completed")
        self.assertEqual(completed.payload["summary"], "read trace.txt")

    async def test_denied_tool_is_visible_in_the_trace(self):
        model = FakeModel(
            [
                _tool_call_response(call("run_shell", {"command": "rm -rf /"})),
                _tool_call_response(None),
            ]
        )
        result = await AgentRuntime(
            model, self.registry(policy=ReadOnlyPolicy())
        ).run("delete everything")
        failed = next(e for e in result.events if e.type == "tool.failed")
        self.assertEqual(failed.payload["error_code"], "denied")
        self.assertEqual(failed.payload["summary"], "$ rm -rf /")
