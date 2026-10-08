import json
from pathlib import Path
import tempfile
import unittest

from nemo.adapters.persistence import SQLiteRepository
from nemo.adapters.tools.history import ReadSessionTool, SearchSessionsTool
from nemo.core.contracts.tools import ExecutionContext
from nemo.core.contracts.types import ToolCall, ModelResponse
from nemo.core.tools.registry import ToolRegistry
from nemo.server.application import NemoApplication
from nemo.testing.fakes import FakeModel
import asyncio


class HistoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.repo = SQLiteRepository(self.root / "sessions.db")
        self.service = NemoApplication(self.repo, client_factory=lambda **_: FakeModel([ModelResponse(content="The answer: blue coin")]))
        self.addAsyncCleanup(self.service.shutdown)
        self.registry = ToolRegistry((SearchSessionsTool(self.repo), ReadSessionTool(self.repo)),
                                     context=ExecutionContext(workspace=self.root, session_id="current"))
        for session_id, workspace in (("current", self.root), ("old", self.root), ("other", self.root / "other")):
            self.repo.create_session(session_id=session_id, workspace=workspace, model=None,
                approval_mode="full", user_instructions=None, project_instructions=None)
            run = self.service.start_run(session_id, prompt="A story about a blue coin", max_steps=1)
            for _ in range(100):
                await asyncio.sleep(.01)
                if self.service.get_run(run["run_id"])["status"] == "completed":
                    break

    async def call(self, name, arguments):
        return await self.registry.execute(ToolCall(id="call", name=name, arguments=arguments))

    async def test_default_search_is_scoped_and_excludes_current_session(self):
        result = await self.call("search_sessions", {"query": "coin"})
        self.assertIsNone(result.error)
        self.assertEqual([s["session_id"] for s in json.loads(result.output)["sessions"]], ["old"])
        self.assertEqual(json.loads((await self.call("search_sessions", {"query": "missing"})).output)["sessions"], [])

    async def test_other_project_requires_explicit_scope(self):
        denied = await self.call("read_session", {"session_id": "other"})
        self.assertIsNotNone(denied.error)
        result = await self.call("read_session", {"session_id": "other", "workspace": str(self.root / "other")})
        self.assertIsNone(result.error)
        self.assertIn("blue coin", result.output)
        self.assertIsNotNone((await self.call("read_session", {"session_id": "other", "workspace": "../other"})).error)

    async def test_pagination_preserves_source_positions_and_bounds(self):
        first = json.loads((await self.call("read_session", {"session_id": "old", "limit": 1})).output)
        self.assertEqual(first["messages"][0]["position"], 0)
        self.assertEqual(first["next_offset"], 1)
        second = json.loads((await self.call("read_session", {"session_id": "old", "limit": 1, "offset": 1})).output)
        self.assertEqual(second["messages"][0]["role"], "assistant")
        self.assertIsNone(second["next_offset"])
        self.registry.context = ExecutionContext(workspace=self.root, max_output_chars=50)
        self.assertIn("output limit", (await self.call("read_session", {"session_id": "old"})).error.message)
