from nemo.testing.fakes import FakeMemorySettings
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest

import httpx

from nemo.adapters.memory import FileMemoryStore
from nemo.adapters.persistence import SQLiteRepository
from nemo.adapters.tools.memory import ReadMemoryTool, SaveMemoryTool, SearchMemoryTool
from nemo.bootstrap import build_local_tools
from nemo.core.contracts.errors import ToolFailure
from nemo.core.contracts.tools import ExecutionContext
from nemo.core.contracts.types import Message, ModelResponse, ToolCall
from nemo.core.memory import MemoryDraft, memory_messages, CATALOG_HEADER
from nemo.core.tools.approval import ApprovalMode, ApprovalPolicy
from nemo.core.tools.registry import ToolRegistry
from nemo.server.app import create_app
from nemo.server.application import NemoApplication
from nemo.testing.fakes import FakeModel


class MemoryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.store = FileMemoryStore(self.root / "memory", settings=FakeMemorySettings())
        self.workspace = self.root / "project"
        self.workspace.mkdir()
        self.draft = MemoryDraft(title="解释偏好", content="先给结论，再给具体依据。", kind="preference")

    def test_scopes_restart_and_source(self):
        entry = self.store.save(self.workspace, "project", self.draft,
                                source_session_id="session-a", source_message_index=2)
        reopened = FileMemoryStore(self.root / "memory", settings=FakeMemorySettings())
        self.assertEqual(reopened.list(self.workspace, "project")[0], entry)
        self.assertEqual(reopened.list(self.root / "other", "project"), [])
        self.store.save(self.workspace, "user", self.draft)
        self.assertEqual(len(reopened.list(self.root / "other", "user")), 1)
        index = next((self.root / "memory" / "projects").glob("*/MEMORY.md"))
        self.assertIn(entry.id, index.read_text())
        self.assertEqual(entry.source_message_index, 2)

    def test_concurrent_updates_conflict_instead_of_lost_write(self):
        entry = self.store.save(self.workspace, "user", self.draft)
        def update(number):
            try:
                return self.store.save(self.workspace, "user", MemoryDraft(title="title", content=str(number)),
                                       entry_id=entry.id, revision=entry.revision)
            except ToolFailure:
                return None
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(update, [1, 2]))
        self.assertEqual(sum(result is not None for result in results), 1)

    def test_forget_removes_active_entry_and_updates_catalog(self):
        entry = self.store.save(self.workspace, "project", self.draft)
        history = memory_messages((), self.store.catalog(self.workspace))
        self.store.forget(self.workspace, "project", entry.id, entry.revision)
        self.assertEqual(self.store.list(self.workspace, "project"), [])
        update = memory_messages(history, self.store.catalog(self.workspace))
        self.assertIn("no longer active", update[0].content)
        self.assertEqual(memory_messages((*history, *update), self.store.catalog(self.workspace)), ())
        self.assertEqual(len(list((self.root / "memory").rglob("forgotten/entry-*.md"))), 1)

    def test_catalog_does_not_repeat_or_change_old_messages(self):
        self.assertEqual(memory_messages((), self.store.catalog(self.workspace)), ())
        self.store.save(self.workspace, "user", self.draft)
        history = memory_messages((), self.store.catalog(self.workspace))
        original = history[0].model_dump_json()
        self.assertEqual(memory_messages(history, self.store.catalog(self.workspace)), ())
        self.store.set_enabled(False)
        self.assertIn("disabled", memory_messages(history, self.store.catalog(self.workspace))[0].content)
        self.assertEqual(history[0].model_dump_json(), original)

    def test_secrets_invalid_ids_and_symlinks_are_rejected(self):
        with self.assertRaises(ToolFailure):
            self.store.save(self.workspace, "user", MemoryDraft(title="key", content="api_key=supersecretvalue"))
        with self.assertRaises(ToolFailure):
            self.store.save(self.workspace, "user", self.draft, entry_id="../../outside", revision="bad")
        self.store.save(self.workspace, "user", self.draft)
        (self.root / "outside.md").write_text("outside")
        (self.root / "memory" / "entry-evil.md").symlink_to(self.root / "outside.md")
        with self.assertRaises(ToolFailure):
            self.store.list(self.workspace, "user")
        self.assertEqual((self.root / "outside.md").read_text(), "outside")

    def test_corruption_is_visible_not_overwritten(self):
        entry = self.store.save(self.workspace, "user", self.draft)
        path = self.root / "memory" / f"entry-{entry.id}.md"
        path.write_text("damaged")
        with self.assertRaises(ToolFailure):
            self.store.save(self.workspace, "user", self.draft)
        self.assertEqual(path.read_text(), "damaged")

    def test_duplicate_save_is_idempotent(self):
        first = self.store.save(self.workspace, "user", self.draft)
        second = self.store.save(self.workspace, "user", self.draft)
        self.assertEqual(first, second)


class MemoryIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.store = FileMemoryStore(self.root / "memory", settings=FakeMemorySettings())
        self.repo = SQLiteRepository(self.root / "nemo.db")
        self.model = FakeModel([])
        self.service = NemoApplication(self.repo, client_factory=lambda **_: self.model,
            memory_store=self.store,
            tools_factory=lambda: build_local_tools(memory_store=self.store, history_repository=self.repo))
        self.addAsyncCleanup(self.service.shutdown)

    def session(self, session_id, workspace=None):
        return self.repo.create_session(session_id=session_id, workspace=workspace or self.root, model=None,
            approval_mode="full", user_instructions=None, project_instructions=None)

    async def run_prompt(self, session_id, prompt):
        run = self.service.start_run(session_id, prompt=prompt, max_steps=3)
        for _ in range(200):
            await asyncio.sleep(.01)
            status = self.service.get_run(run["run_id"])
            if status["status"] not in ("queued", "running"):
                self.assertEqual(status["status"], "completed", status)
                return
        self.fail("run did not finish")

    async def test_saved_memory_recalled_in_new_session_with_provenance_and_history(self):
        self.session("a")
        self.session("b")
        self.model = FakeModel([
            ModelResponse(tool_calls=(ToolCall(id="save", name="save_memory", arguments={
                "scope": "project", "title": "回答习惯", "content": "先给结论，再给依据。", "kind": "feedback"}),)),
            ModelResponse(content="已记住"),
        ])
        await self.run_prompt("a", "记住我希望先给结论")
        entry = self.store.list(self.root, "project")[0]
        self.assertEqual(entry.source_session_id, "a")
        self.assertEqual(self.repo.messages("a")[entry.source_message_index].role, "user")
        self.model = FakeModel([
            ModelResponse(tool_calls=(ToolCall(id="read", name="read_memory", arguments={"scope": "project", "id": entry.id}),)),
            ModelResponse(content="按你的习惯先给结论"), ModelResponse(content="继续"),
        ])
        await self.run_prompt("b", "我的回答习惯是什么？")
        self.assertIn(entry.id, str(self.model.requests[0].messages))
        self.assertIn("先给结论，再给依据", str(self.model.requests[1].messages))
        before = self.repo.messages("b")
        await self.run_prompt("b", "继续")
        after = self.repo.messages("b")
        self.assertEqual(after[:len(before)], before)
        self.assertEqual(sum(m.content.startswith(CATALOG_HEADER) for m in after), 1)
        self.assertIn("先给结论，再给依据", str(self.model.requests[-1].messages))
        run_id = self.repo.list_runs("a")[0]["run_id"]
        self.assertNotIn("先给结论，再给依据", str(self.repo.events_after(run_id)))

    async def test_tools_honor_approval_disabled_and_output_limits(self):
        context = ExecutionContext(workspace=self.root)
        registry = ToolRegistry((SaveMemoryTool(self.store),), context=context, policy=ApprovalPolicy(ApprovalMode.ASK))
        call = ToolCall(id="save", name="save_memory", arguments={"title": "x", "content": "y"})
        self.assertEqual((await registry.execute(call)).error.code, "denied")
        self.assertEqual(self.store.list(self.root, "project"), [])
        self.store.set_enabled(False)
        registry = ToolRegistry((SearchMemoryTool(self.store),), context=context)
        result = await registry.execute(ToolCall(id="find", name="search_memory", arguments={}))
        self.assertIn("disabled", result.error.message)
        self.store.set_enabled(True)
        entry = self.store.save(self.root, "project", MemoryDraft(title="long", content="x" * 2000))
        registry = ToolRegistry((ReadMemoryTool(self.store),), context=ExecutionContext(workspace=self.root, max_output_chars=100))
        result = await registry.execute(ToolCall(id="read", name="read_memory", arguments={"scope": "project", "id": entry.id}))
        self.assertIn("output limit", result.error.message)

    async def test_memory_result_survives_m7_compaction_and_can_be_read_again(self):
        from nemo.core.context.builder import ContextBuilder
        from nemo.core.context.compaction import ContextWindow
        from nemo.core.runtime.agent import AgentRuntime
        entry = self.store.save(self.root, "user", MemoryDraft(title="style", content="Start with the conclusion."))
        registry = ToolRegistry((ReadMemoryTool(self.store),), context=ExecutionContext(workspace=self.root))
        read = ToolCall(id="read", name="read_memory", arguments={"scope": "user", "id": entry.id})
        model = FakeModel([
            ModelResponse(tool_calls=(read,)), ModelResponse(content="Read the preference."),
            ModelResponse(content="Earlier memory says to start with the conclusion."),
            ModelResponse(tool_calls=(read.model_copy(update={"id": "read-again"}),)),
            ModelResponse(content="Confirmed the original preference."),
        ])
        builder = ContextBuilder(system_prompt="Stable")
        first = await AgentRuntime(model, registry, context=builder, context_window=ContextWindow(tokens=9000)).run("A" * 3000)
        self.assertEqual(first.state.status.value, "completed")
        second = await AgentRuntime(model, registry, context=builder, context_window=ContextWindow(tokens=9000)).run(
            "B" * 3000, history=tuple(first.state.messages))
        self.assertEqual(second.state.status.value, "completed", second.state.error)
        self.assertIn("context.compacted", [e.type for e in second.events])
        self.assertIn("Start with the conclusion", str(model.requests[2].messages))
        self.assertIn("Start with the conclusion", str(model.requests[-1].messages))
        self.assertEqual(self.store.list(self.root, "user")[0], entry)

    async def test_http_manager_update_conflict_forget_and_settings(self):
        self.session("a")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(service=self.service)), base_url="http://test") as client:
            response = await client.post("/sessions/a/memories", json={"title": "preference", "content": "concise", "scope": "user"})
            self.assertEqual(response.status_code, 200, response.text)
            entry = response.json()
            body = {k: entry[k] for k in ("title", "content", "scope", "id", "revision", "kind")}
            body["content"] = "detailed"
            self.assertEqual((await client.post("/sessions/a/memories", json=body)).status_code, 200)
            self.assertEqual((await client.post("/sessions/a/memories", json=body)).status_code, 409)
            current = (await client.get("/sessions/a/memories")).json()["entries"][0]
            self.assertEqual((await client.post("/sessions/a/memories/forget", json={k: current[k] for k in ("id", "scope", "revision")})).status_code, 200)
            self.assertEqual((await client.get("/sessions/a/memories")).json()["entries"], [])
            await client.put("/memory/settings", json={"enabled": False})
            self.assertFalse((await client.get("/sessions/a/memories")).json()["enabled"])
            self.assertEqual((await client.get("/sessions/missing/memories")).status_code, 404)
