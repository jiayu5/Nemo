import tempfile
import unittest
from pathlib import Path

import httpx

from nemo.adapters.persistence import SQLiteRepository
from nemo.adapters.skills import FileSkillCatalog, FileSkillManagementStore
from nemo.bootstrap import build_skill_tools
from nemo.core.contracts.types import ModelResponse
from nemo.core.tools.approval import ApprovalMode
from nemo.server.app import create_app
from nemo.server.application import NemoApplication
from nemo.testing.fakes import FakeModel

CONTENT = "---\nname: review\ndescription: Check Python changes\n---\nInspect the diff."


class ServerSkillsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.user = self.root / "user-skills"
        self.model = FakeModel([ModelResponse(content="done")])
        self.service = NemoApplication(SQLiteRepository(self.root / "history.sqlite"),
            client_factory=lambda **_: self.model,
            skill_catalog_factory=lambda workspace: FileSkillCatalog(workspace, self.user),
            skill_tools_factory=build_skill_tools,
            skill_management_store=FileSkillManagementStore(self.user))
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(service=self.service)), base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.service.shutdown()
        self.tmp.cleanup()

    async def test_user_import_preview_details_and_conflict(self):
        body = {"content": CONTENT, "scope": "user"}
        preview = await self.client.post("/skills/preview", json=body)
        self.assertEqual(preview.status_code, 200)
        self.assertFalse(self.user.exists())
        imported = await self.client.post("/skills/import", json=body)
        self.assertEqual(imported.status_code, 201)
        installed = (await self.client.get("/skills")).json()
        self.assertEqual(installed["entries"][0]["id"], "user:review")
        detail = await self.client.get("/skills/detail", params={"skill_id": "user:review"})
        self.assertEqual(detail.json()["body"], "Inspect the diff.")
        self.assertNotIn(str(self.user), detail.text)
        conflict = await self.client.post("/skills/import", json=body)
        self.assertEqual(conflict.status_code, 409)

    async def test_import_does_not_refresh_current_session(self):
        session = self.service.create_session(workspace=str(self.root), model=None, approval_mode=ApprovalMode.ASK)
        sid = session["session_id"]
        response = await self.client.post("/skills/import", json={"session_id": sid, "scope": "project", "content": CONTENT})
        self.assertEqual(response.status_code, 201)
        self.assertFalse(self.service.sessions.skills(sid).entries)
        listing = await self.client.get("/skills", params={"session_id": sid})
        self.assertEqual(listing.json()["entries"][0]["id"], "project:review")
        run = self.service.start_run(sid, prompt="Hello", max_steps=2)
        await self.service.runs._tasks[run["run_id"]]
        self.assertNotIn("project:review", self.model.requests[0].messages[0].content)
        await self.client.post(f"/sessions/{sid}/resume")
        self.assertEqual(self.service.sessions.skills(sid).entries[0].id, "project:review")

    async def test_project_scope_requires_valid_session_and_cannot_supply_target_path(self):
        response = await self.client.post("/skills/import", json={"scope": "project", "content": CONTENT})
        self.assertEqual(response.status_code, 409)
        response = await self.client.post("/skills/import", json={"session_id": "missing", "scope": "project", "content": CONTENT})
        self.assertEqual(response.status_code, 404)
        response = await self.client.post("/skills/import", json={"workspace": "/tmp", "content": CONTENT})
        self.assertEqual(response.status_code, 422)
        other = self.root / "other"
        other.mkdir()
        session = self.service.create_session(workspace=str(other), model=None, approval_mode=ApprovalMode.ASK)
        response = await self.client.post("/skills/import", json={"session_id": session["session_id"], "scope": "project", "content": CONTENT})
        self.assertEqual(response.status_code, 201)
        self.assertEqual((await self.client.get("/skills")).json()["entries"], [])
        self.assertTrue((other / ".nemo" / "skills" / "review" / "SKILL.md").exists())
        self.assertFalse((self.root / ".nemo").exists())

    async def test_invalid_and_secret_documents_do_not_write_files(self):
        for content in ("bad document", CONTENT + "\npassword=sk-live-0123456789abcdef", "x" * 131073):
            response = await self.client.post("/skills/import", json={"content": content})
            self.assertIn(response.status_code, {409, 422})
            self.assertNotIn("sk-live-0123456789abcdef", response.text)
        self.assertFalse(self.user.exists())
