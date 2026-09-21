import asyncio
import stat
import tempfile
import unittest
from pathlib import Path

import httpx

from nemo.adapters.sqlite import ActiveRunError, SQLiteRepository
from nemo.adapters.tools.filesystem import WriteFileTool
from nemo.core.contracts.types import ModelResponse, ToolCall
from nemo.core.tools.approval import ApprovalMode, ApprovalOutcome
from nemo.server.app import create_app
from nemo.server.service import AgentService
from nemo.testing.fakes import FakeModel


class ServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.services = []

    async def asyncTearDown(self):
        for service in reversed(self.services):
            await service.shutdown()

    def service(self, responses, *, tools=(), approval_timeout=1.0):
        model = FakeModel(list(responses))
        service = AgentService(
            SQLiteRepository(self.root / f"db-{len(self.services)}.sqlite"),
            client_factory=lambda **_: model,
            tools_factory=lambda: tuple(tools),
            approval_timeout_seconds=approval_timeout,
        )
        self.services.append(service)
        return service, model

    async def wait_terminal(self, service, run_id):
        for _ in range(100):
            run = service.get_run(run_id)
            if run["status"] not in {"queued", "running"}:
                return run
            await asyncio.sleep(0.01)
        self.fail("run did not finish")

    async def test_http_session_run_and_sse_reconnect(self):
        service, _ = self.service([ModelResponse(content="done")])
        app = create_app(service=service)
        transport = httpx.ASGITransport(app=app)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                created = await client.post(
                    "/sessions",
                    json={
                        "workspace": str(self.root),
                        "approval_mode": "full",
                    },
                )
                self.assertEqual(created.status_code, 201)
                session_id = created.json()["session_id"]

                started = await client.post(
                    f"/sessions/{session_id}/runs",
                    json={"prompt": "hello", "max_steps": 3},
                )
                self.assertEqual(started.status_code, 202)
                run_id = started.json()["run_id"]
                await self.wait_terminal(service, run_id)

                response = await client.get(f"/runs/{run_id}/events")
                self.assertEqual(response.status_code, 200)
                ids = [
                    int(line.removeprefix("id: "))
                    for line in response.text.splitlines()
                    if line.startswith("id: ")
                ]
                self.assertEqual(ids, list(range(1, len(ids) + 1)))
                self.assertIn("event: run.completed", response.text)

                resumed = await client.get(
                    f"/runs/{run_id}/events", headers={"Last-Event-ID": "2"}
                )
                resumed_ids = [
                    int(line.removeprefix("id: "))
                    for line in resumed.text.splitlines()
                    if line.startswith("id: ")
                ]
                self.assertTrue(resumed_ids)
                self.assertTrue(all(value > 2 for value in resumed_ids))

                session = await client.get(f"/sessions/{session_id}")
                self.assertEqual(session.json()["message_count"], 2)

    async def test_one_active_run_per_session_and_cancel(self):
        entered = asyncio.Event()

        class BlockingModel:
            async def generate(self, request):
                entered.set()
                await asyncio.Event().wait()

        service = AgentService(
            SQLiteRepository(self.root / "active.sqlite"),
            client_factory=lambda **_: BlockingModel(),
            tools_factory=lambda: (),
        )
        self.services.append(service)
        session = service.create_session(
            workspace=str(self.root), model=None, approval_mode=ApprovalMode.FULL
        )
        first = service.start_run(session["session_id"], prompt="one", max_steps=2)
        await asyncio.wait_for(entered.wait(), 1)
        with self.assertRaises(ActiveRunError):
            service.start_run(session["session_id"], prompt="two", max_steps=2)
        self.assertEqual(service.cancel_run(first["run_id"]), "accepted")
        run = await self.wait_terminal(service, first["run_id"])
        self.assertEqual(run["status"], "cancelled")

    async def test_approval_round_trip_is_persisted_and_unblocks_tool(self):
        call = ToolCall(
            id="write-1",
            name="write_file",
            arguments={"path": "answer.txt", "content": "approved"},
        )
        service, _ = self.service(
            [ModelResponse(tool_calls=(call,)), ModelResponse(content="finished")],
            tools=(WriteFileTool(),),
        )
        session = service.create_session(
            workspace=str(self.root), model=None, approval_mode=ApprovalMode.ASK
        )
        run = service.start_run(session["session_id"], prompt="write it", max_steps=3)
        request_id = None
        for _ in range(100):
            events = service.events_after(run["run_id"], 0)
            requested = next((e for e in events if e.type == "approval.requested"), None)
            if requested:
                request_id = requested.payload["request_id"]
                break
            await asyncio.sleep(0.01)
        self.assertIsNotNone(request_id)
        service.answer_approval(
            run["run_id"], request_id, ApprovalOutcome.ALLOW_ONCE
        )
        finished = await self.wait_terminal(service, run["run_id"])
        self.assertEqual(finished["status"], "completed")
        self.assertEqual((self.root / "answer.txt").read_text(), "approved")
        event_types = [e.type for e in service.events_after(run["run_id"], 0)]
        self.assertIn("approval.resolved", event_types)

    async def test_startup_marks_abandoned_runs_interrupted(self):
        repository = SQLiteRepository(self.root / "recovery.sqlite")
        repository.create_session(
            session_id="s1",
            workspace=self.root,
            model=None,
            approval_mode="full",
            user_instructions=None,
            project_instructions=None,
        )
        repository.create_run(run_id="r1", session_id="s1", prompt="x", max_steps=2)
        service = AgentService(repository, client_factory=lambda **_: FakeModel([]))
        self.services.append(service)
        self.assertEqual(await service.startup(), ["r1"])
        self.assertEqual(service.get_run("r1")["status"], "interrupted")
        self.assertEqual(service.events_after("r1", 0)[-1].type, "run.interrupted")

    async def test_failed_run_persists_its_terminal_event(self):
        class BrokenModel:
            async def generate(self, request):
                raise RuntimeError("provider internals stay private")

        service = AgentService(
            SQLiteRepository(self.root / "failed.sqlite"),
            client_factory=lambda **_: BrokenModel(),
            tools_factory=lambda: (),
        )
        self.services.append(service)
        session = service.create_session(
            workspace=str(self.root), model=None, approval_mode=ApprovalMode.FULL
        )
        run = service.start_run(session["session_id"], prompt="fail", max_steps=1)
        finished = await self.wait_terminal(service, run["run_id"])
        self.assertEqual(finished["status"], "failed")
        self.assertEqual(
            service.events_after(run["run_id"], 0)[-1].type, "run.failed"
        )
        self.assertNotIn("provider internals", finished["error"])

    async def test_persisted_payloads_are_redacted(self):
        secret = "sk-live-0123456789abcdef"
        service, _ = self.service([ModelResponse(content=f"value {secret}")])
        session = service.create_session(
            workspace=str(self.root), model=None, approval_mode=ApprovalMode.FULL
        )
        run = service.start_run(
            session["session_id"], prompt=f"token={secret}", max_steps=1
        )
        await self.wait_terminal(service, run["run_id"])
        messages = service.repository.messages(session["session_id"])
        self.assertEqual(len(messages), 2)
        self.assertIn("[redacted]", messages[-1].content)
        for path in service.repository.path.parent.glob(service.repository.path.name + "*"):
            self.assertNotIn(secret.encode(), path.read_bytes())
        self.assertEqual(stat.S_IMODE(service.repository.path.stat().st_mode), 0o600)
