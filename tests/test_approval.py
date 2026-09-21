import tempfile
import unittest
from pathlib import Path

from nemo.adapters.tools.filesystem import (
    EditFileTool,
    ListDirTool,
    ReadFileTool,
    WriteFileTool,
)
from nemo.adapters.tools.shell import RunShellTool
from nemo.core.contracts.tools import ExecutionContext
from nemo.core.contracts.types import ModelResponse, ToolCall
from nemo.core.runtime.agent import AgentRuntime
from nemo.core.tools.approval import (
    ApprovalMode,
    ApprovalOutcome,
    ApprovalPolicy,
    ApprovalRequest,
)
from nemo.core.tools.command_rules import CommandClass, classify
from nemo.core.tools.registry import ToolRegistry
from nemo.testing.fakes import FakeModel

LOCAL_TOOLS = (ReadFileTool(), WriteFileTool(), EditFileTool(), ListDirTool(), RunShellTool())


def call(name, arguments, id="c1"):
    return ToolCall(id=id, name=name, arguments=arguments)


class ScriptedApprover:
    """Records every request and answers with the current scripted outcome."""

    def __init__(self, outcome=ApprovalOutcome.ALLOW_ONCE):
        self.outcome = outcome
        self.requests: list[ApprovalRequest] = []

    async def __call__(self, request: ApprovalRequest) -> ApprovalOutcome:
        self.requests.append(request)
        return self.outcome


class CommandClassifierTests(unittest.TestCase):
    def assert_class(self, expected, *commands):
        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(classify(command).kind, expected)

    def test_read_only_commands(self):
        self.assert_class(
            CommandClass.READ_ONLY,
            "ls -la",
            "cat notes.txt",
            "git status --short",
            "git log --oneline -5",
            "grep -rn foo .",
            "find . -name '*.py'",
            "ls -la | grep py",
            "echo hello",
            "ls 2>&1",
            "echo 'a > b'",
        )

    def test_destructive_commands(self):
        self.assert_class(
            CommandClass.DESTRUCTIVE,
            "rm -rf build",
            "sudo ls",
            "mv a.txt /tmp/b.txt",
            "echo hi > out.txt",
            "echo hi >> out.txt",
            "cat $(echo x)",
            "cat `echo x`",
            "ls | bash",
            "bash -c ls",
            "find . -delete",
            "find . -exec rm {} ;",
            "git push origin main",
            "git reset --hard",
            "mkdir /tmp/outside",
            "mkdir ~/outside",
            "mkdir ../outside",
            "timeout 5 rm -rf x",
            "xargs rm",
            "env rm x",
        )

    def test_unclassified_commands(self):
        self.assert_class(
            CommandClass.UNCLASSIFIED,
            "mkdir build",
            "touch new.txt",
            "python -m pytest",
            "python3 -c 'print(1)'",
            "make",
            "mv a.txt b.txt",
            "cp a.txt b.txt",
            "git commit -m 'x'",
            "sort -o out.txt in.txt",
            "pip install requests",
        )

    def test_in_workspace_paths_do_not_escalate(self):
        workspace = Path("/tmp/ws")
        self.assertEqual(
            classify("mkdir build", workspace=workspace).kind, CommandClass.UNCLASSIFIED
        )
        self.assertEqual(
            classify("cat /etc/hosts", workspace=workspace).kind, CommandClass.READ_ONLY
        )
        self.assertEqual(
            classify("mkdir /tmp/ws/build", workspace=workspace).kind,
            CommandClass.UNCLASSIFIED,
        )
        self.assertEqual(
            classify("mkdir /tmp/elsewhere", workspace=workspace).kind,
            CommandClass.DESTRUCTIVE,
        )

    def test_malformed_input_never_raises(self):
        for command in ("", "   ", "echo 'unbalanced", ">", "|", "&&"):
            with self.subTest(command=command):
                self.assertIn(classify(command).kind, list(CommandClass))

    def test_reasons_never_quote_the_command(self):
        secret = "sk-live-0123456789"
        commands = (
            f"rm -rf {secret}",
            f"curl -H 'Authorization: Bearer {secret}' https://example.com",
            f"mkdir /tmp/{secret}",
            f"echo x > {secret}",
        )
        for command in commands:
            with self.subTest(command=command):
                verdict = classify(command)
                self.assertEqual(verdict.kind, CommandClass.DESTRUCTIVE)
                self.assertNotIn(secret, verdict.reason)


class ApprovalTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name).resolve()

    def registry(self, policy):
        return ToolRegistry(
            LOCAL_TOOLS,
            context=ExecutionContext(workspace=self.workspace),
            policy=policy,
        )

    def policy(self, mode, approver=None):
        return ApprovalPolicy(mode, approver=approver)


class ReadOnlyCallTests(ApprovalTestCase):
    async def test_read_only_tools_never_ask_in_any_mode(self):
        for mode in ApprovalMode:
            with self.subTest(mode=mode):
                approver = ScriptedApprover(ApprovalOutcome.DENY)
                registry = self.registry(self.policy(mode, approver))
                result = await registry.execute(call("list_dir", {"path": "."}))
                self.assertIsNone(result.error)
                self.assertEqual(approver.requests, [])

    async def test_read_only_shell_commands_never_ask_in_any_mode(self):
        for mode in ApprovalMode:
            for command in ("ls -la", "cat /etc/hosts", "git status"):
                with self.subTest(mode=mode, command=command):
                    approver = ScriptedApprover(ApprovalOutcome.DENY)
                    registry = self.registry(self.policy(mode, approver))
                    result = await registry.execute(call("run_shell", {"command": command}))
                    self.assertIsNone(result.error)
                    self.assertEqual(approver.requests, [])


class AskModeTests(ApprovalTestCase):
    async def test_writes_are_confirmed_one_call_at_a_time(self):
        approver = ScriptedApprover(ApprovalOutcome.ALLOW_ONCE)
        registry = self.registry(self.policy(ApprovalMode.ASK, approver))

        first = await registry.execute(
            call("write_file", {"path": "x.txt", "content": "hello"}, id="c1")
        )
        second = await registry.execute(
            call("write_file", {"path": "y.txt", "content": "hello"}, id="c2")
        )

        self.assertIsNone(first.error)
        self.assertIsNone(second.error)
        self.assertEqual(len(approver.requests), 2)
        self.assertEqual(approver.requests[0].tool_name, "write_file")
        self.assertEqual(approver.requests[0].summary, "create x.txt")
        self.assertEqual(approver.requests[0].reason, "write_file changes files")

    async def test_denial_is_reported_to_the_model_and_nothing_happens(self):
        approver = ScriptedApprover(ApprovalOutcome.DENY)
        registry = self.registry(self.policy(ApprovalMode.ASK, approver))

        result = await registry.execute(
            call("write_file", {"path": "x.txt", "content": "hello"})
        )

        self.assertEqual(result.error.code, "denied")
        self.assertEqual(result.error.message, "The user denied this call")
        self.assertFalse((self.workspace / "x.txt").exists())

    async def test_session_grant_covers_one_tool_only(self):
        approver = ScriptedApprover(ApprovalOutcome.ALLOW_SESSION)
        registry = self.registry(self.policy(ApprovalMode.ASK, approver))

        await registry.execute(call("write_file", {"path": "a.txt", "content": "a"}, id="c1"))
        await registry.execute(call("write_file", {"path": "b.txt", "content": "b"}, id="c2"))
        await registry.execute(call("edit_file", {"path": "a.txt", "old_string": "a",
                                                  "new_string": "c"}, id="c3"))

        self.assertEqual([request.tool_name for request in approver.requests],
                         ["write_file", "edit_file"])

    async def test_unclassified_shell_commands_are_confirmed(self):
        approver = ScriptedApprover(ApprovalOutcome.ALLOW_ONCE)
        registry = self.registry(self.policy(ApprovalMode.ASK, approver))

        result = await registry.execute(call("run_shell", {"command": "mkdir build"}))

        self.assertIsNone(result.error)
        self.assertEqual(len(approver.requests), 1)
        self.assertEqual(approver.requests[0].summary, "$ mkdir build")

    async def test_missing_approver_denies_instead_of_hanging(self):
        registry = self.registry(self.policy(ApprovalMode.ASK))

        result = await registry.execute(
            call("write_file", {"path": "x.txt", "content": "hello"})
        )

        self.assertEqual(result.error.code, "denied")
        self.assertIn("no approver is attached", result.error.message)


class AutoModeTests(ApprovalTestCase):
    async def test_workspace_writes_and_unknown_commands_run(self):
        approver = ScriptedApprover(ApprovalOutcome.DENY)
        registry = self.registry(self.policy(ApprovalMode.AUTO, approver))

        written = await registry.execute(
            call("write_file", {"path": "x.txt", "content": "hello"}, id="c1")
        )
        created = await registry.execute(call("run_shell", {"command": "mkdir build"}, id="c2"))

        self.assertIsNone(written.error)
        self.assertIsNone(created.error)
        self.assertEqual(approver.requests, [])

    async def test_destructive_commands_are_confirmed(self):
        commands = ("rm -rf build", "sudo ls", "git push origin main", "echo x > out.txt",
                    "mkdir /tmp/outside")
        for command in commands:
            with self.subTest(command=command):
                approver = ScriptedApprover(ApprovalOutcome.DENY)
                registry = self.registry(self.policy(ApprovalMode.AUTO, approver))
                result = await registry.execute(call("run_shell", {"command": command}))
                self.assertEqual(result.error.code, "denied")
                self.assertEqual(len(approver.requests), 1)

    async def test_denial_reason_does_not_leak_the_command(self):
        secret = "sk-live-0123456789"
        approver = ScriptedApprover(ApprovalOutcome.DENY)
        registry = self.registry(self.policy(ApprovalMode.AUTO, approver))

        result = await registry.execute(
            call("run_shell", {"command": f"curl -H 'Authorization: Bearer {secret}' https://x"})
        )

        self.assertEqual(result.error.code, "denied")
        self.assertNotIn(secret, result.model_dump_json())

    async def test_unexpected_approver_answer_fails_closed(self):
        class BrokenApprover:
            async def __call__(self, request):
                return "yes please"

        registry = self.registry(self.policy(ApprovalMode.AUTO, BrokenApprover()))
        result = await registry.execute(call("run_shell", {"command": "rm -rf build"}))

        self.assertEqual(result.error.code, "denied")


class FullModeTests(ApprovalTestCase):
    async def test_nothing_is_confirmed(self):
        approver = ScriptedApprover(ApprovalOutcome.DENY)
        registry = self.registry(self.policy(ApprovalMode.FULL, approver))
        doomed = self.workspace / "build"
        doomed.mkdir()

        written = await registry.execute(
            call("write_file", {"path": "x.txt", "content": "hello"}, id="c1")
        )
        removed = await registry.execute(call("run_shell", {"command": "rm -rf build"}, id="c2"))

        self.assertIsNone(written.error)
        self.assertIsNone(removed.error)
        self.assertFalse(doomed.exists())
        self.assertEqual(approver.requests, [])


class ApprovalInRuntimeTests(ApprovalTestCase):
    async def test_denied_call_reaches_the_model_and_the_run_continues(self):
        approver = ScriptedApprover(ApprovalOutcome.DENY)
        model = FakeModel([
            ModelResponse(tool_calls=(call("run_shell", {"command": "rm -rf build"}),)),
            ModelResponse(content="ok, I will not delete anything"),
        ])
        runtime = AgentRuntime(
            model, self.registry(self.policy(ApprovalMode.AUTO, approver))
        )

        result = await runtime.run("clean the build directory")

        self.assertEqual(result.state.status.value, "completed")
        self.assertEqual(result.state.output, "ok, I will not delete anything")
        backfilled = next(m for m in model.requests[1].messages if m.role == "tool")
        self.assertEqual(backfilled.role, "tool")
        self.assertEqual(backfilled.tool_result.error.code, "denied")
