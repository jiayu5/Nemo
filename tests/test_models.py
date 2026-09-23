import asyncio
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import httpx

from nemo.adapters.httpx_transport import HttpxTransport
from nemo.adapters.protocols.openai_compatible import OpenAICompatibleAdapter
from nemo.bootstrap import build_adapter, build_model_client, resolve_model
from nemo.config.loader import load_config
from nemo.core.contracts.errors import (
    CapabilityError,
    ConfigError,
    MissingSecretError,
    ProviderError,
    ResponseFormatError,
    TransportError,
    UnknownReferenceError,
)
from nemo.core.contracts.model_config import ResolvedModel
from nemo.core.contracts.model_transport import HttpResponse
from nemo.core.contracts.secrets import SecretValue
from nemo.core.contracts.types import (
    Message,
    ModelRequest,
    ModelResponse,
    ModelReasoningDelta,
    ModelTextDelta,
    ModelUsage,
    RunStatus,
    ToolCall,
    ToolError,
    ToolResult,
    ToolSpec,
)
from nemo.core.models.client import ModelClient
from nemo.core.models.registry import ModelRegistry
from nemo.core.models.resolver import ModelResolver
from nemo.redaction import StreamingTextRedactor
from nemo.core.runtime.agent import AgentRuntime
from nemo.core.tools.registry import ToolRegistry
from nemo.testing.fakes import AddTool, RecordingTransport

SECRET = "sk-abcdefghijklmnop"

CONFIG = """
default = "chat"

[providers.deepseek]
protocol = "openai_compatible"
base_url = "https://api.deepseek.com/v1"
api_key_env = "DEMO_API_KEY"
proxy_env = "NEMO_PROXY"
headers = { "X-Tenant" = "nemo" }

[providers.mirror]
protocol = "openai_compatible"
base_url = "https://mirror.example.com/v1"
api_key_env = "MIRROR_API_KEY"

[models.chat-model]
provider = "deepseek"
model_id = "chat-model"
capabilities = ["tool_calling"]

[models.chat-model.parameters]
temperature = 0.2

[models.mirror-model]
provider = "mirror"
model_id = "mirror-model"
capabilities = ["tool_calling"]

[models.text-model]
provider = "deepseek"
model_id = "text-model"

[aliases]
fast = "chat-model"
mirror = "mirror-model"

[profiles.chat]
model = "chat-model"

[profiles.chat.parameters]
temperature = 0.9
"""


def tool_call_response(call_id="c1", name="add", arguments='{"a": 12, "b": 30}'):
    return HttpResponse(
        200,
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": call_id,
                                "type": "function",
                                "function": {"name": name, "arguments": arguments},
                            }
                        ],
                    }
                }
            ]
        },
    )


def text_response(content="done"):
    return HttpResponse(200, {"choices": [{"message": {"role": "assistant", "content": content}}]})


def usage_response(usage):
    return HttpResponse(
        200, {"choices": [{"message": {"role": "assistant", "content": "ok"}}], "usage": usage}
    )


class ModelTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.config_path = self.dir / "config.toml"
        self.config_path.write_text(CONFIG, encoding="utf-8")
        self.registry = ModelRegistry(load_config(self.config_path))
        self.resolver = ModelResolver(self.registry)

    def client(self, transport, **kwargs):
        return build_model_client(
            config_path=self.config_path,
            transport=transport,
            environ={"DEMO_API_KEY": SECRET, "MIRROR_API_KEY": "sk-mirror-1234567890"},
            **kwargs,
        )


class ResolverTests(ModelTestCase):
    def test_default_selection_and_source(self):
        resolved = self.resolver.resolve()
        self.assertEqual(resolved.model_name, "chat-model")
        self.assertEqual(resolved.model_id, "chat-model")
        self.assertEqual(resolved.source, "default")
        self.assertEqual(resolved.protocol, "openai_compatible")

    def test_precedence_run_then_session_then_agent(self):
        self.assertEqual(self.resolver.resolve(run_override="fast").source, "run")
        self.assertEqual(self.resolver.resolve(session="fast").source, "session")
        self.assertEqual(self.resolver.resolve(agent="fast").source, "agent")
        resolved = self.resolver.resolve(run_override="mirror", session="fast")
        self.assertEqual((resolved.model_name, resolved.source), ("mirror-model", "run"))

    def test_profile_parameters_override_model_parameters(self):
        resolved = self.resolver.resolve(run_override="chat")
        self.assertEqual(resolved.parameters["temperature"], 0.9)
        bare = self.resolver.resolve(run_override="chat-model")
        self.assertEqual(bare.parameters["temperature"], 0.2)

    def test_alias_resolves_to_its_model(self):
        resolved = self.resolver.resolve(run_override="fast")
        self.assertEqual(resolved.model_name, "chat-model")
        self.assertIsNone(self.registry.profile("fast"))

    def test_capabilities_and_headers_come_from_the_selected_model(self):
        self.assertEqual(
            self.resolver.resolve(run_override="text-model").capabilities, frozenset()
        )
        self.assertEqual(
            self.resolver.resolve().headers, {"X-Tenant": "nemo"}
        )
        self.assertEqual(self.resolver.resolve().proxy_env, "NEMO_PROXY")

    def test_unknown_selection(self):
        with self.assertRaises(UnknownReferenceError):
            self.resolver.resolve(run_override="ghost")


class AdapterTests(ModelTestCase):
    def setUp(self):
        super().setUp()
        self.adapter = OpenAICompatibleAdapter()
        self.secret = SecretValue(SECRET)
        self.resolved = self.resolver.resolve()

    def test_encode_request_shape(self):
        request = ModelRequest(
            messages=(Message(role="user", content="hi"),),
            tools=(ToolSpec(name="add", description="add", parameters={"type": "object"}),),
        )
        encoded = self.adapter.encode_request(request, self.resolved, self.secret)
        self.assertEqual(encoded.url, "https://api.deepseek.com/v1/chat/completions")
        self.assertEqual(encoded.headers["Authorization"], f"Bearer {SECRET}")
        self.assertEqual(encoded.headers["X-Tenant"], "nemo")
        self.assertEqual(encoded.body["model"], "chat-model")
        self.assertEqual(encoded.body["messages"], [{"role": "user", "content": "hi"}])
        self.assertEqual(encoded.body["tools"][0]["function"]["name"], "add")
        self.assertEqual(encoded.body["temperature"], 0.9)

    def test_encoded_request_repr_is_redacted(self):
        request = ModelRequest(messages=(Message(role="user", content="hi"),), tools=())
        encoded = self.adapter.encode_request(request, self.resolved, self.secret)
        self.assertNotIn(SECRET, repr(encoded))

    def test_tool_messages_round_trip_into_wire_format(self):
        call = ToolCall(id="c1", name="add", arguments={"a": 1, "b": 2})
        ok = ToolResult(tool_call_id="c1", output=3)
        failed = ToolResult(tool_call_id="c2", error=ToolError(code="unknown_tool", message="nope"))
        request = ModelRequest(
            messages=(
                Message(role="user", content="go"),
                Message(role="assistant", tool_calls=(call,)),
                Message(role="tool", tool_result=ok),
                Message(role="tool", tool_result=failed),
            ),
            tools=(),
        )
        messages = self.adapter.encode_request(request, self.resolved, self.secret).body["messages"]
        self.assertIsNone(messages[1]["content"])
        self.assertEqual(messages[1]["tool_calls"][0]["function"]["arguments"], '{"a": 1, "b": 2}')
        self.assertEqual(messages[2]["content"], "3")
        self.assertIn("unknown_tool", messages[3]["content"])

    def test_decode_response_with_tool_calls(self):
        response = self.adapter.decode_response(tool_call_response(), self.secret)
        self.assertEqual(response.content, "")
        self.assertEqual(response.tool_calls[0].arguments, {"a": 12, "b": 30})

    def test_decode_response_without_tool_calls(self):
        response = self.adapter.decode_response(text_response("hello"), self.secret)
        self.assertEqual(response.content, "hello")
        self.assertEqual(response.tool_calls, ())

    def test_reasoning_is_returned_only_with_current_turn_tool_history(self):
        response = self.adapter.decode_response(HttpResponse(200, {"choices": [{"message": {
            "content": "answer", "reasoning_content": "thinking",
        }}]}), self.secret)
        self.assertEqual(response.reasoning_content, "thinking")
        request = ModelRequest(messages=(
            Message(role="assistant", content="old", reasoning_content="old thought"),
            Message(role="user", content="new question"),
            Message(role="assistant", content="using tool", reasoning_content="new thought"),
            Message(role="user", content="<reminder>step 2 of 10</reminder>"),
        ), tools=())
        encoded = self.adapter.encode_request(request, self.resolved, self.secret).body["messages"]
        self.assertNotIn("reasoning_content", encoded[0])
        self.assertEqual(encoded[2]["reasoning_content"], "new thought")

    def test_decodes_usage_shape_captured_from_a_real_response(self):
        """Field shape verified against a live DeepSeek reply on 2026-09-17."""

        response = self.adapter.decode_response(
            usage_response(
                {
                    "prompt_tokens": 492,
                    "completion_tokens": 1,
                    "total_tokens": 493,
                    "prompt_tokens_details": {"cached_tokens": 256},
                    "prompt_cache_hit_tokens": 256,
                    "prompt_cache_miss_tokens": 236,
                }
            ),
            self.secret,
        )
        self.assertEqual(response.usage.prompt_tokens, 492)
        self.assertEqual(response.usage.completion_tokens, 1)
        self.assertEqual(response.usage.total_tokens, 493)
        self.assertEqual(response.usage.cached_prompt_tokens, 256)
        self.assertAlmostEqual(response.usage.cache_hit_rate(), 256 / 492)

    def test_decodes_openai_style_cached_tokens_only(self):
        response = self.adapter.decode_response(
            usage_response(
                {
                    "prompt_tokens": 100,
                    "completion_tokens": 5,
                    "prompt_tokens_details": {"cached_tokens": 64},
                }
            ),
            self.secret,
        )
        self.assertEqual(response.usage.cached_prompt_tokens, 64)
        self.assertEqual(response.usage.total_tokens, 105)

    def test_usage_may_be_absent_or_malformed(self):
        self.assertIsNone(self.adapter.decode_response(text_response(), self.secret).usage)
        for usage in ({}, {"prompt_tokens": "many"}, [], None, {"prompt_tokens": True}):
            with self.subTest(usage=usage):
                response = self.adapter.decode_response(usage_response(usage), self.secret)
                self.assertIsNone(response.usage)

    def test_usage_values_are_clamped_and_optional(self):
        over_reported = self.adapter.decode_response(
            usage_response({"prompt_tokens": 10, "prompt_cache_hit_tokens": 99}), self.secret
        )
        # A provider must never be able to push the hit rate above 100%.
        self.assertEqual(over_reported.usage.cached_prompt_tokens, 10)
        self.assertIsNone(ModelUsage().cache_hit_rate())

    def test_decode_rejects_malformed_payloads(self):
        cases = [
            HttpResponse(200, None),
            HttpResponse(200, {}),
            HttpResponse(200, {"choices": []}),
            HttpResponse(200, {"choices": [{}]}),
            tool_call_response(arguments="not json"),
            tool_call_response(name=""),
        ]
        for response in cases:
            with self.subTest(body=response.body):
                with self.assertRaises(ResponseFormatError):
                    self.adapter.decode_response(response, self.secret)

    def test_decode_error_redacts_the_secret(self):
        response = HttpResponse(401, {"error": {"message": f"Invalid key {SECRET}"}})
        error = self.adapter.decode_error(response, self.secret)
        self.assertIsInstance(error, ProviderError)
        self.assertNotIn(SECRET, str(error))
        self.assertIn("HTTP 401", str(error))

    def test_decode_error_without_body(self):
        error = self.adapter.decode_error(HttpResponse(500, None), self.secret)
        self.assertIn("HTTP 500", str(error))

    def test_decode_error_uses_plain_text_bodies(self):
        error = self.adapter.decode_error(
            HttpResponse(401, None, "Authentication Fails (governor)"), self.secret
        )
        self.assertIn("Authentication Fails", str(error))
        self.assertIn("HTTP 401", str(error))

    def test_decode_error_redacts_plain_text_bodies(self):
        error = self.adapter.decode_error(
            HttpResponse(401, None, f"bad key {SECRET}"), self.secret
        )
        self.assertNotIn(SECRET, str(error))


class ClientTests(ModelTestCase):
    def test_adapter_protocol_must_match_the_resolved_model(self):
        class OtherAdapter(OpenAICompatibleAdapter):
            protocol = "openai_responses"

        with self.assertRaises(ValueError):
            ModelClient(
                resolved=self.resolver.resolve(),
                adapter=OtherAdapter(),
                transport=RecordingTransport([]),
                secret=SecretValue(SECRET),
            )

    def test_missing_secret_fails_before_any_call(self):
        with self.assertRaises(MissingSecretError) as caught:
            build_model_client(
                config_path=self.config_path,
                transport=RecordingTransport([]),
                environ={},
            )
        self.assertIn("DEMO_API_KEY", str(caught.exception))

    def test_unimplemented_protocol_reports_the_support_matrix(self):
        with self.assertRaises(ConfigError) as caught:
            build_adapter("anthropic_messages")
        message = str(caught.exception)
        self.assertIn("anthropic_messages", message)
        self.assertIn("openai_compatible", message)

    def test_capability_mismatch_is_rejected_before_encoding(self):
        transport = RecordingTransport([])
        client = self.client(transport, run_override="text-model")
        request = ModelRequest(
            messages=(Message(role="user", content="hi"),),
            tools=(ToolSpec(name="add", description="add", parameters={}),),
        )
        with self.assertRaises(CapabilityError):
            asyncio.run(client.generate(request))
        self.assertEqual(transport.requests, [])

    def test_failed_request_becomes_a_provider_error_without_the_secret(self):
        transport = RecordingTransport(
            [HttpResponse(401, {"error": {"message": f"bad key {SECRET}"}})]
        )
        client = self.client(transport)
        request = ModelRequest(messages=(Message(role="user", content="hi"),), tools=())
        with self.assertRaises(ProviderError) as caught:
            asyncio.run(client.generate(request))
        self.assertNotIn(SECRET, str(caught.exception))

    def test_same_protocol_new_provider_needs_config_only(self):
        transport = RecordingTransport([text_response("a"), text_response("b")])
        for selection in ("chat", "mirror"):
            client = self.client(transport, run_override=selection)
            asyncio.run(client.generate(ModelRequest(messages=(), tools=())))
        self.assertEqual(
            [request["url"] for request in transport.requests],
            [
                "https://api.deepseek.com/v1/chat/completions",
                "https://mirror.example.com/v1/chat/completions",
            ],
        )

    def test_streamed_text_tools_usage_and_secret_splits(self):
        frames = [
            {"choices": [{"delta": {"content": "Hello "}}]},
            {"choices": [{"delta": {"content": "sk-abc"}}]},
            {"choices": [{"delta": {"content": "defghi world "}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call-1", "function": {"name": "add", "arguments": '{"a":'}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "12,\"b\":30}"}}]}}]},
            {"choices": [], "usage": {"prompt_tokens": 8, "completion_tokens": 5}},
        ]
        raw = "".join(f"data: {json.dumps(frame)}\n\n" for frame in frames) + "data: [DONE]\n\n"
        sent = []

        def answer(request):
            sent.append(json.loads(request.content))
            return httpx.Response(200, text=raw)

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as http_client:
                model = self.client(HttpxTransport(http_client))
                parts = [part async for part in model.stream(ModelRequest(messages=(), tools=()))]
                return parts

        parts = asyncio.run(run())
        self.assertTrue(sent[0]["stream"])
        self.assertEqual("".join(part.text for part in parts[:-1]), "Hello sk-[redacted] world ")
        self.assertEqual(parts[-1].content, "Hello sk-abcdefghi world ")
        self.assertEqual(parts[-1].tool_calls[0].arguments, {"a": 12, "b": 30})
        self.assertEqual(parts[-1].usage.total_tokens, 13)

    def test_streamed_reasoning_is_separate_and_redacted(self):
        frames = [
            {"choices": [{"delta": {"reasoning_content": "Think sk-abc"}}]},
            {"choices": [{"delta": {"reasoning_content": "defghi first "}}]},
            {"choices": [{"delta": {"content": "Answer"}}]},
        ]
        raw = "".join(f"data: {json.dumps(frame)}\n\n" for frame in frames) + "data: [DONE]\n\n"

        async def run():
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(lambda _: httpx.Response(200, text=raw))
            ) as http_client:
                model = self.client(HttpxTransport(http_client))
                return [part async for part in model.stream(ModelRequest(messages=(), tools=()))]

        parts = asyncio.run(run())
        self.assertEqual("".join(part.text for part in parts if isinstance(part, ModelReasoningDelta)),
                         "Think sk-[redacted] first ")
        self.assertEqual("".join(part.text for part in parts if isinstance(part, ModelTextDelta)),
                         "Answer")
        self.assertEqual(parts[-1].reasoning_content, "Think sk-abcdefghi first ")

    def test_stream_rejects_truncation_and_never_yields_tool_fragments(self):
        raw = 'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c","function":{"name":"add","arguments":"{\\"a\\":"}}]}}]}\n\n'

        async def run():
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(lambda _: httpx.Response(200, text=raw))
            ) as http_client:
                model = self.client(HttpxTransport(http_client))
                return [part async for part in model.stream(ModelRequest(messages=(), tools=()))]

        with self.assertRaises(ResponseFormatError):
            asyncio.run(run())

    def test_streaming_redaction_keeps_split_bearer_token_private(self):
        redactor = StreamingTextRedactor()
        visible = "".join(redactor.feed(piece) for piece in ("Bearer ", "secret", "value "))
        visible += redactor.finish()
        self.assertEqual(visible, "Bearer [redacted] ")

    def test_streaming_redaction_keeps_mixed_script_secret_private(self):
        redactor = StreamingTextRedactor()
        visible = "".join(redactor.feed(piece) for piece in ("Bearer ", "abc", "中文", "def "))
        visible += redactor.finish()
        self.assertEqual(visible, "Bearer [redacted] ")

class ModelSystemIntegrationTests(ModelTestCase):
    def test_client_drives_the_unchanged_runtime_loop(self):
        transport = RecordingTransport([tool_call_response(), text_response("12 + 30 = 42")])
        client = self.client(transport)
        runtime = AgentRuntime(client, ToolRegistry((AddTool(),)))
        result = asyncio.run(runtime.run("计算 12 + 30"))

        self.assertEqual(result.state.status, RunStatus.COMPLETED)
        self.assertEqual(result.state.output, "12 + 30 = 42")
        self.assertEqual(len(transport.requests), 2)
        follow_up = transport.requests[1]["json"]["messages"]
        tool_messages = [m for m in follow_up if m["role"] == "tool"]
        self.assertEqual(tool_messages[-1]["content"], "42")

    def test_secret_never_reaches_the_result_or_the_events(self):
        secret_env = {"DEMO_API_KEY": SECRET}
        transport = RecordingTransport([tool_call_response(), text_response("ok")])
        client = build_model_client(
            config_path=self.config_path, transport=transport, environ=secret_env
        )
        result = asyncio.run(AgentRuntime(client, ToolRegistry((AddTool(),))).run("task"))
        self.assertNotIn(SECRET, result.model_dump_json())
        self.assertEqual(transport.requests[0]["headers"]["Authorization"], f"Bearer {SECRET}")

    def test_usage_reaches_the_model_completed_event(self):
        transport = RecordingTransport(
            [
                usage_response(
                    {
                        "prompt_tokens": 492,
                        "completion_tokens": 1,
                        "total_tokens": 493,
                        "prompt_cache_hit_tokens": 256,
                        "prompt_cache_miss_tokens": 236,
                    }
                )
            ]
        )
        client = self.client(transport, run_override="text-model")
        result = asyncio.run(AgentRuntime(client, ToolRegistry()).run("hi"))

        self.assertEqual(result.state.output, "ok")
        payload = next(e for e in result.events if e.type == "model.completed").payload
        self.assertEqual(payload["prompt_tokens"], 492)
        self.assertEqual(payload["completion_tokens"], 1)
        self.assertEqual(payload["cached_prompt_tokens"], 256)

    def test_usage_keys_stay_present_when_the_provider_reports_nothing(self):
        transport = RecordingTransport([text_response("ok")])
        client = self.client(transport, run_override="text-model")
        result = asyncio.run(AgentRuntime(client, ToolRegistry()).run("hi"))

        payload = next(e for e in result.events if e.type == "model.completed").payload
        self.assertEqual(
            (payload["prompt_tokens"], payload["completion_tokens"],
             payload["cached_prompt_tokens"]),
            (None, None, None),
        )

    def test_runtime_stays_vendor_and_transport_agnostic(self):
        source = Path("src/nemo/core/runtime/agent.py").read_text(encoding="utf-8").lower()
        for forbidden in (
            "openai",
            "anthropic",
            "deepseek",
            "httpx",
            "http://",
            "api_key",
            "nemo.adapters",
            "nemo.config",
            "nemo.core.models",
        ):
            with self.subTest(token=forbidden):
                self.assertNotIn(forbidden, source)


class ResolvedModelContractTests(unittest.TestCase):
    def test_resolved_model_accepts_only_known_protocols(self):
        with self.assertRaises(ValueError):
            ResolvedModel(
                selection="x",
                source="default",
                model_name="x",
                model_id="x",
                protocol="mystery_protocol",
                base_url="https://example.com",
                api_key_env="KEY",
            )


class HttpxTransportTests(unittest.TestCase):
    """Exercises the real transport code path without leaving the machine."""

    def post(self, handler, *, status_ok=True):
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await HttpxTransport(client=client).post(
                    "https://example.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {SECRET}"},
                    json={"model": "chat-model"},
                    timeout=5,
                )

        return asyncio.run(run())

    def test_sends_json_headers_and_returns_decoded_body(self):
        seen = {}

        def handler(request):
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content)
            seen["auth"] = request.headers.get("authorization")
            return httpx.Response(200, json={"choices": []})

        response = self.post(handler)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.body, {"choices": []})
        self.assertEqual(seen["url"], "https://example.com/v1/chat/completions")
        self.assertEqual(seen["body"], {"model": "chat-model"})
        self.assertEqual(seen["auth"], f"Bearer {SECRET}")

    def test_error_status_is_passed_through_untouched(self):
        response = self.post(lambda request: httpx.Response(429, json={"error": {"message": "slow"}}))
        self.assertEqual(response.status, 429)
        self.assertEqual(response.body["error"]["message"], "slow")

    def test_non_json_body_becomes_none(self):
        response = self.post(lambda request: httpx.Response(502, text="<html>bad gateway</html>"))
        self.assertEqual((response.status, response.body), (502, None))
        self.assertEqual(response.text, "<html>bad gateway</html>")

    def test_connection_failure_becomes_a_transport_error_without_the_secret(self):
        def handler(request):
            raise httpx.ConnectError("connection refused")

        with self.assertRaises(TransportError) as caught:
            self.post(handler)
        self.assertIn("ConnectError", str(caught.exception))
        self.assertNotIn(SECRET, str(caught.exception))

    def test_explicit_proxy_is_revealed_only_to_httpx(self):
        proxy = SecretValue("http://proxy-user:proxy-pass@127.0.0.1:7890")
        with patch("nemo.adapters.httpx_transport.httpx.AsyncClient") as factory:
            transport = HttpxTransport(proxy=proxy)
            transport._ensure_client()
        factory.assert_called_once_with(proxy=proxy.reveal())
        self.assertNotIn("proxy-pass", repr(proxy))
