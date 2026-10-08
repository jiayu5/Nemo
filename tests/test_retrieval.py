import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nemo.adapters.tools.filesystem import ReadFileTool
from nemo.adapters.tools.search import SearchWorkspaceTool
from nemo.adapters.workspace_retrieval import WorkspaceRetriever
from nemo.core.context.builder import ContextBuilder
from nemo.core.context.compaction import ContextWindow, estimate_request_tokens
from nemo.core.contracts.tools import ExecutionContext
from nemo.core.contracts.types import AgentState, Message, ModelResponse, RunStatus, ToolCall
from nemo.core.runtime.agent import AgentRuntime
from nemo.core.tools.registry import ToolRegistry
from nemo.testing.fakes import FakeModel


class WorkspaceRetrievalTests(unittest.IsolatedAsyncioTestCase):
    async def test_finds_ranked_chunks_with_sources_and_skips_private_paths(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = Path(directory)
            (root / "billing.py").write_text(
                "def invoice_total(items):\n    return sum(item.price for item in items)\n",
                encoding="utf-8",
            )
            (root / "notes.md").write_text("A note about invoices.\n", encoding="utf-8")
            (root / ".env").write_text("invoice_total=private\n", encoding="utf-8")
            external = Path(outside) / "outside.py"
            external.write_text("invoice_total outside\n", encoding="utf-8")
            (root / "link.py").symlink_to(external)
            results = await WorkspaceRetriever(root).retrieve("invoice_total")
            self.assertTrue(results)
            self.assertEqual(results[0].path, "billing.py")
            self.assertEqual(results[0].start_line, 1)
            self.assertNotIn(".env", [item.path for item in results])
            self.assertNotIn("link.py", [item.path for item in results])

    async def test_scope_and_file_changes_are_applied_when_search_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs").mkdir()
            path = root / "docs" / "guide.md"
            path.write_text("first version\n", encoding="utf-8")
            (root / "other.md").write_text("new phrase elsewhere\n", encoding="utf-8")
            retriever = WorkspaceRetriever(root)
            self.assertEqual(await retriever.retrieve("new phrase", root / "docs"), ())
            path.write_text("new phrase is here\n", encoding="utf-8")
            matches = await retriever.retrieve("new phrase", root / "docs")
            self.assertEqual([item.path for item in matches], ["docs/guide.md"])


class SearchWorkspaceToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_is_bounded_and_points_to_read_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "billing.py").write_text(
                "def invoice_total(items):\n    return sum(item.price for item in items)\n",
                encoding="utf-8",
            )
            registry = ToolRegistry(
                (SearchWorkspaceTool(),), context=ExecutionContext(workspace=root)
            )
            result = await registry.execute(ToolCall(
                id="search-1", name="search_workspace",
                arguments={"query": "invoice_total"},
            ))
            self.assertIsNone(result.error)
            self.assertIn("[billing.py: lines 1-2]", result.output)
            self.assertIn("def invoice_total", result.output)
            self.assertLessEqual(len(result.output), 3200)

    async def test_search_rejects_workspace_escape_and_hidden_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".private").mkdir()
            (root / "private_token").mkdir()
            registry = ToolRegistry(
                (SearchWorkspaceTool(),), context=ExecutionContext(workspace=root)
            )
            for path in ("..", ".private", "private_token"):
                result = await registry.execute(ToolCall(
                    id="search-1", name="search_workspace",
                    arguments={"query": "secret", "path": path},
                ))
                self.assertIsNotNone(result.error)


class RuntimeRetrievalTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_workspace_scan_without_model_tool_call(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = ToolRegistry(
                (SearchWorkspaceTool(),),
                context=ExecutionContext(workspace=Path(directory)),
            )
            model = FakeModel([ModelResponse(content="Hello")])
            with patch.object(WorkspaceRetriever, "retrieve", side_effect=AssertionError):
                result = await AgentRuntime(model, registry).run("Hello")
            self.assertEqual(result.state.status, RunStatus.COMPLETED)
            self.assertEqual(len(model.requests), 1)
            self.assertFalse(any(m.role == "tool" for m in result.state.messages))

    async def test_model_requests_search_and_result_enters_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "billing.py").write_text(
                "def invoice_total(items):\n    return sum(item.price for item in items)\n",
                encoding="utf-8",
            )
            registry = ToolRegistry(
                (SearchWorkspaceTool(), ReadFileTool()),
                context=ExecutionContext(workspace=root),
            )
            model = FakeModel([
                ModelResponse(tool_calls=(ToolCall(
                    id="search-1", name="search_workspace",
                    arguments={"query": "invoice_total"},
                ),)),
                ModelResponse(content="Found billing.py"),
            ])
            result = await AgentRuntime(model, registry).run("Find invoice_total")
            self.assertEqual(result.state.status, RunStatus.COMPLETED)
            self.assertNotIn("def invoice_total", str(model.requests[0].messages))
            self.assertIn("def invoice_total", str(model.requests[1].messages))
            saved = [m.tool_result.output for m in result.state.messages if m.role == "tool"]
            self.assertEqual(len(saved), 1)
            self.assertIn("def invoice_total", saved[0])

    async def test_current_turn_tool_result_is_rejected_if_it_would_overflow(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "large.txt").write_text("x" * 5000, encoding="utf-8")
            prompt = "Read large.txt"
            builder = ContextBuilder(system_prompt="Stable")
            registry = ToolRegistry(
                (ReadFileTool(),), context=ExecutionContext(workspace=root)
            )
            base = builder.build(
                AgentState(messages=[Message(role="user", content=prompt)]), registry
            )
            window = ContextWindow(
                tokens=estimate_request_tokens(base) + 2048 + 1200
            )
            model = FakeModel([
                ModelResponse(tool_calls=(ToolCall(
                    id="read-1", name="read_file", arguments={"path": "large.txt"},
                ),)),
                ModelResponse(content="I need a smaller range."),
            ])
            result = await AgentRuntime(
                model, registry, context=builder, context_window=window
            ).run(prompt)
            self.assertEqual(result.state.status, RunStatus.COMPLETED)
            tool_result = next(m.tool_result for m in result.state.messages if m.role == "tool")
            self.assertIsNotNone(tool_result.error)
            self.assertIn("reduce output", tool_result.error.message)
            self.assertNotIn("x" * 100, str(model.requests[1].messages))

    async def test_older_search_result_can_be_compacted_by_m7(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "billing.py").write_text(
                "def invoice_total(items):\n    return sum(item.price for item in items)\n",
                encoding="utf-8",
            )
            registry = ToolRegistry(
                (SearchWorkspaceTool(),), context=ExecutionContext(workspace=root)
            )
            builder = ContextBuilder(system_prompt="Stable")
            window = ContextWindow(tokens=9000)
            model = FakeModel([
                ModelResponse(tool_calls=(ToolCall(
                    id="search-1", name="search_workspace",
                    arguments={"query": "invoice_total"},
                ),)),
                ModelResponse(content="I found billing.py."),
                ModelResponse(content="The earlier search found invoice_total in billing.py."),
                ModelResponse(content="The function totals prices."),
            ])
            first = await AgentRuntime(
                model, registry, context=builder, context_window=window
            ).run("Find invoice_total. " + "A" * 3000)
            self.assertEqual(first.state.status, RunStatus.COMPLETED)
            second = await AgentRuntime(
                model, registry, context=builder, context_window=window
            ).run("Explain it. " + "B" * 3000, history=tuple(first.state.messages))
            self.assertEqual(second.state.status, RunStatus.COMPLETED)
            self.assertIn("context.compacted", [event.type for event in second.events])
            self.assertIn("def invoice_total", str(model.requests[2].messages))
            self.assertIn("The earlier search found", str(model.requests[3].messages))
