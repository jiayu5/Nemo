import asyncio
import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path

import httpx

from nemo.adapters.persistence import SQLiteRepository
from nemo.server.errors import ActiveRunError
from nemo.adapters.tools.filesystem import WriteFileTool
from nemo.config.editor import ConfigEditor
from nemo.config.loader import load_config
from nemo.config.secrets import SecretLoader
from nemo.core.contracts.model_config import AppConfig
from nemo.core.contracts.types import ModelResponse, ModelTextDelta, ToolCall
from nemo.core.models.registry import ModelRegistry
from nemo.core.models.resolver import ModelResolver
from nemo.core.tools.approval import ApprovalMode, ApprovalOutcome
from nemo.server.app import create_app
from nemo.server.application import NemoApplication
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
        service = NemoApplication(
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

    async def test_desktop_access_requires_token_and_checks_origin(self):
        service, _ = self.service([])
        app = create_app(service=service, desktop_token="desktop-token-with-enough-entropy")
        transport = httpx.ASGITransport(app=app)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                self.assertEqual((await client.get("/health")).status_code, 401)
                self.assertEqual((await client.get("/openapi.json")).status_code, 401)
                headers = {"X-Nemo-Token": "desktop-token-with-enough-entropy"}
                self.assertEqual((await client.get("/health", headers=headers)).status_code, 200)
                self.assertEqual((await client.get("/health", headers={
                    **headers, "Origin": "https://untrusted.example",
                })).status_code, 403)
                preflight = await client.options("/health", headers={
                    "Origin": "tauri://localhost",
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Headers": "X-Nemo-Token",
                })
                self.assertEqual(preflight.status_code, 204)
                self.assertEqual(preflight.headers["Access-Control-Allow-Origin"],
                                 "tauri://localhost")
                allowed = await client.get("/health", headers={
                    **headers, "Origin": "tauri://localhost",
                })
                self.assertEqual(allowed.status_code, 200)
                self.assertEqual(allowed.headers["Access-Control-Allow-Origin"],
                                 "tauri://localhost")

    async def test_streamed_text_is_replayed_through_run_sse(self):
        class StreamingModel:
            async def stream(self, request):
                yield ModelTextDelta(text="Hello ")
                yield ModelTextDelta(text="stream")
                yield ModelResponse(content="Hello stream")

        service = NemoApplication(
            SQLiteRepository(self.root / "streaming.sqlite"),
            client_factory=lambda **_: StreamingModel(),
        )
        self.services.append(service)
        app = create_app(service=service)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                created = await client.post(
                    "/sessions", json={"workspace": str(self.root), "approval_mode": "full"}
                )
                run = await client.post(
                    f"/sessions/{created.json()['session_id']}/runs",
                    json={"prompt": "hello", "max_steps": 1},
                )
                run_id = run.json()["run_id"]
                await self.wait_terminal(service, run_id)

                response = await client.get(f"/runs/{run_id}/events")
                self.assertIn("event: model.delta", response.text)
                self.assertIn('"text":"Hello "', response.text)
                first_delta = next(
                    event for event in service.events_after(run_id, 0)
                    if event.type == "model.delta"
                )
                replay = await client.get(f"/runs/{run_id}/events?after={first_delta.seq}")
                self.assertNotIn('"text":"Hello "', replay.text)
                self.assertIn('"text":"stream"', replay.text)
                messages = (await client.get(
                    f"/sessions/{created.json()['session_id']}/messages"
                )).json()
                self.assertEqual(messages[-1]["content"], "Hello stream")

    async def test_model_provider_catalog_and_connection_test(self):
        config = self.config()
        resolver = ModelResolver(ModelRegistry(config))

        def client_factory(**kwargs):
            model = FakeModel([ModelResponse(content="OK")])
            model.resolved = resolver.resolve(run_override=kwargs.get("run_override"))
            return model

        service = NemoApplication(
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

    async def test_provider_settings_validate_save_and_hot_reload(self):
        config_path = self.root / "settings.toml"
        secret_path = self.root / ".env"
        editor = ConfigEditor(
            config_path=config_path,
            secret_path=secret_path,
            environ={},
        )
        service = NemoApplication(
            SQLiteRepository(self.root / "settings.sqlite"),
            client_factory=lambda **_: FakeModel([ModelResponse(content="OK")]),
            tools_factory=lambda: (),
            config_loader=lambda: load_config(config_path),
            secret_loader_factory=lambda: SecretLoader(
                env_file=secret_path, environ={}
            ),
            config_editor=editor,
        )
        self.services.append(service)
        app = create_app(service=service)
        transport = httpx.ASGITransport(app=app)
        request = {
            "protocol": "openai_compatible",
            "base_url": "https://example.test/v1",
            "api_key_env": "DEMO_API_KEY",
            "proxy_env": "NEMO_PROXY",
            "timeout_seconds": 45,
            "model_name": "demo-model",
            "model_id": "demo-v1",
            "tool_calling": True,
            "make_default": True,
            "api_key": {"action": "replace", "value": "private-key-value"},
            "proxy": {"action": "replace", "value": "http://127.0.0.1:7890"},
        }
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                empty = await client.get("/settings/providers")
                self.assertEqual(empty.json(), {
                    "config_exists": False,
                    "default": None,
                    "providers": [],
                })
                preview = await client.post(
                    "/settings/providers/demo/validate", json=request
                )
                self.assertEqual(preview.status_code, 200)
                self.assertEqual(preview.json()["status"], "valid")
                self.assertFalse(config_path.exists())
                self.assertNotIn("private-key-value", preview.text)

                saved = await client.put("/settings/providers/demo", json=request)
                self.assertEqual(saved.status_code, 200)
                self.assertEqual(saved.json()["status"], "saved")
                self.assertNotIn("private-key-value", saved.text)
                self.assertEqual(secret_path.stat().st_mode & 0o777, 0o600)

                settings = (await client.get("/settings/providers")).json()
                provider = settings["providers"][0]
                self.assertEqual(provider["api_key_source"], "file")
                self.assertEqual(provider["proxy_source"], "file")
                self.assertEqual(provider["models"][0]["model_id"], "demo-v1")

                providers = (await client.get("/providers")).json()
                models = (await client.get("/models")).json()
                self.assertEqual(providers[0]["proxy_env"], "NEMO_PROXY")
                self.assertTrue(providers[0]["proxy_configured"])
                self.assertEqual(models[0]["selection"], "demo-model")

                unsafe = dict(request)
                unsafe["timeout_seconds"] = -1
                unsafe["api_key"] = {
                    "action": "replace",
                    "value": "must-not-appear-in-validation",
                }
                rejected = await client.put(
                    "/settings/providers/demo", json=unsafe
                )
                self.assertEqual(rejected.status_code, 422)
                self.assertNotIn("must-not-appear-in-validation", rejected.text)

    async def test_provider_settings_reject_environment_secret_mutation(self):
        editor = ConfigEditor(
            config_path=self.root / "environment.toml",
            secret_path=self.root / ".env",
            environ={"DEMO_API_KEY": "process-secret"},
        )
        service = NemoApplication(
            SQLiteRepository(self.root / "environment.sqlite"),
            client_factory=lambda **_: FakeModel([]),
            config_editor=editor,
        )
        self.services.append(service)
        app = create_app(service=service)
        transport = httpx.ASGITransport(app=app)
        request = {
            "protocol": "openai_compatible",
            "base_url": "https://example.test/v1",
            "api_key_env": "DEMO_API_KEY",
            "model_name": "demo",
            "model_id": "demo",
            "api_key": {"action": "delete"},
        }
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.put("/settings/providers/demo", json=request)
        self.assertEqual(response.status_code, 422)
        self.assertIn("read-only", response.text)
        self.assertNotIn("process-secret", response.text)
        self.assertFalse(editor.config_path.exists())

    async def test_provider_settings_delete_cascades_config_but_keeps_secrets(self):
        config_path = self.root / "delete.toml"
        secret_path = self.root / ".env"
        config_path.write_text(
            """
default = "primary"

[providers.one]
protocol = "openai_compatible"
base_url = "https://one.example/v1"
api_key_env = "ONE_API_KEY"

[providers.two]
protocol = "openai_compatible"
base_url = "https://two.example/v1"
api_key_env = "TWO_API_KEY"

[models.primary]
provider = "one"
model_id = "one-v1"

[models.backup]
provider = "two"
model_id = "two-v1"

[aliases]
fast = "primary"

[profiles.chat]
model = "primary"
""",
            encoding="utf-8",
        )
        secret_path.write_text("ONE_API_KEY=keep-me\n", encoding="utf-8")
        editor = ConfigEditor(config_path=config_path, secret_path=secret_path, environ={})
        service = NemoApplication(
            SQLiteRepository(self.root / "delete.sqlite"),
            client_factory=lambda **_: FakeModel([]),
            config_editor=editor,
        )
        self.services.append(service)
        app = create_app(service=service)
        transport = httpx.ASGITransport(app=app)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                deleted = await client.delete("/settings/providers/one")
                self.assertEqual(deleted.status_code, 200)
                self.assertEqual(deleted.json()["removed_models"], ["primary"])
                self.assertEqual(deleted.json()["default"], "backup")
                self.assertEqual((await client.delete("/settings/providers/two")).status_code, 409)
                self.assertEqual((await client.delete("/settings/providers/missing")).status_code, 404)

        config = load_config(config_path)
        self.assertEqual(list(config.providers), ["two"])
        self.assertEqual(config.aliases, {})
        self.assertEqual(config.profiles, {})
        self.assertEqual(secret_path.read_text(encoding="utf-8"), "ONE_API_KEY=keep-me\n")

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

        service = NemoApplication(
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
        service = NemoApplication(repository, client_factory=lambda **_: FakeModel([]))
        self.services.append(service)
        self.assertEqual(await service.startup(), ["r1"])
        self.assertEqual(service.get_run("r1")["status"], "interrupted")
        self.assertEqual(service.events_after("r1", 0)[-1].type, "run.interrupted")

    async def test_failed_run_persists_its_terminal_event(self):
        class BrokenModel:
            async def generate(self, request):
                raise RuntimeError("provider internals stay private")

        service = NemoApplication(
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
