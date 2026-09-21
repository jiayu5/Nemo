import asyncio
import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path

import httpx

from nemo.adapters.sqlite import ActiveRunError, SQLiteRepository
from nemo.adapters.tools.filesystem import WriteFileTool
from nemo.config.secrets import SecretLoader
from nemo.core.contracts.model_config import AppConfig
from nemo.core.contracts.types import ModelResponse, ToolCall
from nemo.core.models.registry import ModelRegistry
from nemo.core.models.resolver import ModelResolver
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

    @staticmethod
    def config():
        return AppConfig.model_validate(
            {
                "default": "chat",
                "providers": {
                    "demo": {
                        "protocol": "openai_compatible",
                        "base_url": (
                            "https://user:password@example.test/v1?api_key=hidden"
                        ),
                        "api_key_env": "DEMO_API_KEY",
                        "headers": {"X-Private": "must-not-leak"},
                    }
                },
                "models": {
                    "demo-model": {
                        "provider": "demo",
                        "model_id": "model-v1",
                        "capabilities": ["tool_calling"],
                    }
                },
                "aliases": {"fast": "demo-model"},
                "profiles": {"chat": {"model": "demo-model"}},
            }
        )

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
                openapi = (await client.get("/openapi.json")).json()
                self.assertTrue(
                    {
                        "/sessions/{session_id}/messages",
                        "/sessions/{session_id}/runs",
                        "/sessions/{session_id}/model",
                        "/runs/{run_id}/trace",
                        "/models",
                        "/providers",
                        "/providers/{provider_id}/test",
                    }
                    <= set(openapi["paths"])
                )
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

                messages = await client.get(f"/sessions/{session_id}/messages")
                self.assertEqual(
                    [item["role"] for item in messages.json()],
                    ["user", "assistant"],
                )
                runs = await client.get(f"/sessions/{session_id}/runs")
                self.assertEqual([item["run_id"] for item in runs.json()], [run_id])
                trace = await client.get(f"/runs/{run_id}/trace")
                self.assertEqual(trace.status_code, 200)
                self.assertEqual(trace.json()["run"]["run_id"], run_id)
                self.assertEqual(len(trace.json()["model_calls"]), 1)
                self.assertGreaterEqual(trace.json()["duration_ms"], 0)

    async def test_model_provider_catalog_and_connection_test(self):
        config = self.config()
        resolver = ModelResolver(ModelRegistry(config))

        def client_factory(**kwargs):
            model = FakeModel([ModelResponse(content="OK")])
            model.resolved = resolver.resolve(run_override=kwargs.get("run_override"))
            return model

        service = AgentService(
            SQLiteRepository(self.root / "catalog.sqlite"),
            client_factory=client_factory,
            tools_factory=lambda: (),
            config_loader=lambda: config,
            secret_loader_factory=lambda: SecretLoader(
                env_file=self.root / "missing.env",
                environ={"DEMO_API_KEY": "configured-secret"},
            ),
        )
        self.services.append(service)
        app = create_app(service=service)
        transport = httpx.ASGITransport(app=app)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                models = (await client.get("/models")).json()
                self.assertEqual(
                    [item["selection"] for item in models],
                    ["chat", "fast", "demo-model"],
                )
                self.assertTrue(models[0]["is_default"])
                self.assertEqual(models[0]["provider_id"], "demo")

                providers_response = await client.get("/providers")
                self.assertEqual(providers_response.status_code, 200)
                providers = providers_response.json()
                self.assertTrue(providers[0]["secret_configured"])
                self.assertEqual(providers[0]["base_url"], "https://example.test/v1")
                self.assertNotIn("configured-secret", providers_response.text)
                self.assertNotIn("must-not-leak", providers_response.text)
                self.assertNotIn("password", providers_response.text)
                self.assertNotIn("hidden", providers_response.text)

                session = (
                    await client.post(
                        "/sessions",
                        json={"workspace": str(self.root), "approval_mode": "full"},
                    )
                ).json()
                changed = await client.put(
                    f"/sessions/{session['session_id']}/model",
                    json={"model": "fast"},
                )
                self.assertEqual(changed.status_code, 200)
                self.assertEqual(changed.json()["model"], "fast")
                invalid = await client.put(
                    f"/sessions/{session['session_id']}/model",
                    json={"model": "missing"},
                )
                self.assertEqual(invalid.status_code, 422)

                started = await client.post(
                    f"/sessions/{session['session_id']}/runs",
                    json={"prompt": "hello", "max_steps": 1},
                )
                run_id = started.json()["run_id"]
                finished = await self.wait_terminal(service, run_id)
                self.assertEqual(finished["model_selection"], "fast")
                self.assertEqual(finished["model_name"], "demo-model")
                self.assertEqual(finished["model_id"], "model-v1")
                self.assertEqual(finished["model_protocol"], "openai_compatible")
                self.assertEqual(finished["model_provider"], "demo")

                tested = await client.post("/providers/demo/test", json={})
                self.assertEqual(tested.status_code, 200)
                self.assertEqual(tested.json()["selection"], "chat")
                self.assertGreaterEqual(tested.json()["latency_ms"], 0)

    async def test_existing_database_is_migrated_with_model_identity_columns(self):
        path = self.root / "old.sqlite"
        connection = sqlite3.connect(path)
        connection.execute(
            """CREATE TABLE runs (
                run_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                status TEXT NOT NULL,
                prompt TEXT NOT NULL,
                max_steps INTEGER NOT NULL,
                output TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT
            )"""
        )
        connection.commit()
        connection.close()

        repository = SQLiteRepository(path)
        self.addCleanup(repository.close)
        with sqlite3.connect(path) as check:
            columns = {row[1] for row in check.execute("PRAGMA table_info(runs)")}
        self.assertTrue(
            {
                "model_selection",
                "model_name",
                "model_id",
                "model_protocol",
                "model_provider",
            }
            <= columns
        )

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
        trace = service.trace(run["run_id"])
        self.assertEqual(len(trace["tool_calls"]), 1)
        self.assertEqual(trace["tool_calls"][0]["name"], "write_file")
        self.assertEqual(trace["tool_calls"][0]["status"], "completed")
        self.assertGreaterEqual(trace["tool_calls"][0]["duration_ms"], 0)

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
