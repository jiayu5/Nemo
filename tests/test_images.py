import asyncio
import base64
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from PIL import Image

from nemo.adapters.images import FileImageStore, inspect_image
from nemo.adapters.persistence import SQLiteRepository
from nemo.adapters.protocols.openai_compatible import OpenAICompatibleAdapter
from nemo.core.context.compaction import ContextEngine, CompactionCheckpoint, estimate_request_tokens, history_digest
from nemo.core.context.builder import ContextBuilder
from nemo.core.context.compaction import ContextWindow
from nemo.core.contracts.errors import CapabilityError, ToolFailure
from nemo.core.contracts.model_config import AppConfig, ResolvedModel
from nemo.core.contracts.secrets import SecretValue
from nemo.core.contracts.types import Message, ModelRequest, ModelResponse, ToolCall
from nemo.core.runtime.agent import AgentRuntime
from nemo.core.tools.approval import ApprovalMode
from nemo.core.tools.registry import ToolRegistry
from nemo.server.app import create_app
from nemo.server.application import NemoApplication
from nemo.testing.fakes import AddTool, FakeModel


def png(color="red"):
    buffer = io.BytesIO()
    Image.new("RGB", (32, 24), color).save(buffer, "PNG")
    return buffer.getvalue()


def config(capabilities=("tool_calling", "image_input")):
    return AppConfig.model_validate({
        "default": "vision", "providers": {"demo": {"protocol": "openai_compatible",
        "base_url": "https://example.test/v1", "api_key_env": "DEMO_KEY"}},
        "models": {"vision": {"provider": "demo", "model_id": "vision", "capabilities": capabilities}},
    })


class ImageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = FileImageStore(self.root / "images")

    def test_storage_reopens_and_checks_session_scope_integrity_and_paths(self):
        ref = self.store.put("one", png(), "image/png")
        reopened = FileImageStore(self.root / "images")
        self.assertEqual(reopened.get("one", ref.asset_id).data, png())
        self.assertNotIn("data=", repr(reopened.get("one", ref.asset_id)))
        with self.assertRaises(ToolFailure):
            reopened.get("two", ref.asset_id)
        with self.assertRaises(ToolFailure):
            reopened.get("one", "../image")
        path = reopened._path("one", ref.asset_id)
        path.write_bytes(b"corrupt")
        with self.assertRaisesRegex(ToolFailure, "integrity"):
            reopened.get("one", ref.asset_id)
        path.unlink()  # Test fixture only.
        path.symlink_to(self.root / "outside")
        with self.assertRaisesRegex(ToolFailure, "path"):
            reopened.get("one", ref.asset_id)

    def test_invalid_mime_truncation_animation_and_limits(self):
        for data, mime in [(b"not an image", "image/png"), (png(), "image/jpeg"), (png()[:30], "image/png")]:
            with self.subTest(mime=mime, size=len(data)), self.assertRaises(ToolFailure):
                inspect_image(data, mime)
        with patch("nemo.adapters.images.MAX_IMAGE_BYTES", 10), self.assertRaises(ToolFailure):
            inspect_image(png())
        with patch("nemo.adapters.images.MAX_IMAGE_PIXELS", 10), self.assertRaises(ToolFailure):
            inspect_image(png())
        buffer = io.BytesIO()
        Image.new("RGB", (2, 2), "red").save(buffer, "PNG", save_all=True,
            append_images=[Image.new("RGB", (2, 2), "blue")], duration=100)
        with self.assertRaisesRegex(ToolFailure, "Animated"):
            inspect_image(buffer.getvalue())

    def test_adapter_encodes_one_text_then_ordered_images_without_mutating_history(self):
        refs = tuple(self.store.put("one", png(color), "image/png") for color in ("red", "blue"))
        adapter = OpenAICompatibleAdapter(lambda asset_id: self.store.get("one", asset_id))
        resolved = ResolvedModel(selection="vision", source="default", model_name="vision", model_id="vision",
            protocol="openai_compatible", base_url="https://example.test", api_key_env="KEY",
            capabilities=frozenset({"image_input"}))
        request = ModelRequest(messages=(Message(role="user", content="Compare", images=refs),), tools=())
        wire = adapter.encode_request(request, resolved, SecretValue("test"))
        parts = wire.body["messages"][0]["content"]
        self.assertEqual(parts[0], {"type": "text", "text": "Compare"})
        for index, color in enumerate(("red", "blue"), 1):
            self.assertEqual(base64.b64decode(parts[index]["image_url"]["url"].split(",", 1)[1]), png(color))
        self.assertNotIn("base64", request.model_dump_json())
        image_only = ModelRequest(messages=(Message(role="user", images=refs),), tools=())
        self.assertEqual(len(adapter.encode_request(image_only, resolved, SecretValue("test")).body["messages"][0]["content"]), 2)
        with self.assertRaises(CapabilityError):
            adapter.encode_request(request, resolved.model_copy(update={"capabilities": frozenset()}), SecretValue("test"))
        with patch("nemo.adapters.protocols.openai_compatible.MAX_REQUEST_IMAGE_BYTES", 1), self.assertRaises(ToolFailure):
            adapter.encode_request(request, resolved, SecretValue("test"))

    async def test_runtime_images_survive_tool_calls_and_compaction(self):
        ref = self.store.put("one", png(), "image/png")
        model = FakeModel([ModelResponse(tool_calls=(ToolCall(id="add", name="add", arguments={"a": 1, "b": 2}),)), ModelResponse(content="3")])
        result = await AgentRuntime(model, ToolRegistry((AddTool(),))).run("", images=(ref,))
        self.assertEqual(result.state.messages[0].images, (ref,))
        self.assertTrue(all(any(message.images for message in request.messages) for request in model.requests))
        history = (Message(role="user", content="Look", images=(ref,)), Message(role="assistant", content="Red"), Message(role="user", content="Again"))
        checkpoint = CompactionCheckpoint(2, history_digest(history[:2]), "A red image was shown")
        engine = ContextEngine(ContextBuilder(), FakeModel([]), ContextWindow(20000, 1000))
        request = engine._request(history, ToolRegistry(), (), checkpoint)
        self.assertEqual(next(message for message in request.messages if message.images).images, (ref,))
        summary = engine._summary_request(history[:2], None)
        self.assertTrue(any(message.images for message in summary.messages))
        plain = ModelRequest(messages=(Message(role="user", content="Look"),), tools=())
        visual = ModelRequest(messages=(history[0],), tools=())
        self.assertGreater(estimate_request_tokens(visual), estimate_request_tokens(plain) + 1000)

    async def test_upload_image_only_run_history_restart_and_cross_session_rejection(self):
        model = FakeModel([ModelResponse(content="red"), ModelResponse(content="again")])
        service = NemoApplication(SQLiteRepository(self.root / "db.sqlite"), client_factory=lambda **_: model,
                                  image_store=self.store, config_loader=config)
        session = service.create_session(workspace=str(self.root), model=None, approval_mode=ApprovalMode.ASK)
        other = service.create_session(workspace=str(self.root), model=None, approval_mode=ApprovalMode.ASK)
        session_id = session["session_id"]
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(service=service, desktop_token="a" * 24)),
                    base_url="http://test", headers={"X-Nemo-Token": "a" * 24}) as client:
                upload = await client.post(f"/sessions/{session_id}/images", content=png(), headers={"Content-Type": "image/png"})
                self.assertEqual(upload.status_code, 201, upload.text)
                asset_id = upload.json()["asset_id"]
                image_path = f"/sessions/{session_id}/images/{asset_id}"
                self.assertEqual((await client.get(image_path)).content, png())
                self.assertEqual((await client.get(image_path, headers={"X-Nemo-Token": "wrong"})).status_code, 401)
                self.assertEqual((await client.get(f"/sessions/{other['session_id']}/images/{asset_id}")).status_code, 404)
                bad = await client.post(f"/sessions/{session_id}/runs", json={"images": ["../x"]})
                self.assertEqual(bad.status_code, 422)
                bad = await client.post(f"/sessions/{other['session_id']}/runs", json={"images": [asset_id]})
                self.assertEqual(bad.status_code, 422)
                self.assertEqual((await client.post(f"/sessions/{session_id}/runs", json={"prompt": " "})).status_code, 422)
                self.assertEqual((await client.post(f"/sessions/{session_id}/runs", json={"images": [asset_id] * 9})).status_code, 422)
                started = await client.post(f"/sessions/{session_id}/runs", json={"images": [asset_id]})
                self.assertEqual(started.status_code, 202, started.text)
                await service.runs._tasks[started.json()["run_id"]]
                history = service.session_messages(session_id)
                self.assertEqual(history[0].content, "")
                self.assertEqual(history[0].images[0].asset_id, asset_id)
                self.assertNotIn("base64", history[0].model_dump_json())
                invalid = await client.post(f"/sessions/{session_id}/images", content=png(), headers={"Content-Type": "image/jpeg"})
                self.assertEqual(invalid.status_code, 422)
                service.runs._config_loader = lambda: config(("tool_calling",))
                rejected = await client.post(f"/sessions/{session_id}/runs", json={"prompt": "again"})
                self.assertEqual(rejected.status_code, 422)
        finally:
            await service.shutdown()
        reopened = SQLiteRepository(self.root / "db.sqlite")
        try:
            self.assertEqual(reopened.messages(session_id)[0].images[0].asset_id, asset_id)
        finally:
            reopened.close()
