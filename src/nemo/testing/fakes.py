from typing import Any, Mapping
import asyncio

from pydantic import BaseModel, ConfigDict

from nemo.core.contracts.tools import ExecutionContext
from nemo.core.contracts.events import EventType
from nemo.core.contracts.model_transport import HttpResponse
from nemo.core.contracts.types import Event, ModelRequest, ModelResponse, ToolCall


class AddArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    a: int
    b: int


class AddTool:
    name = "add"
    description = "Add two integers without external side effects"
    arguments_type = AddArguments
    read_only = True

    async def execute(self, arguments: AddArguments, context: ExecutionContext) -> int:
        return arguments.a + arguments.b

    def summarize(self, arguments: AddArguments) -> str:
        return f"add {arguments.a} + {arguments.b}"


class FakeModel:
    """Scripted responses with request capture, suitable for deterministic tests."""
    def __init__(self, responses: list[ModelResponse]):
        self.responses = iter(responses)
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request.model_copy(deep=True))
        return next(self.responses)


class AdditionModel:
    """Fixed demo scenario, not a natural-language calculator."""
    async def generate(self, request: ModelRequest) -> ModelResponse:
        # Look for the newest tool result rather than assuming it is the last
        # message: the runtime may append a reminder after it.
        result = next(
            (m.tool_result for m in reversed(request.messages) if m.role == "tool"), None
        )
        if result is None:
            return ModelResponse(tool_calls=(ToolCall(id="add-1", name="add",
                                                      arguments={"a": 12, "b": 30}),))
        if result.error or result.output != 42:
            raise ValueError("Expected successful tool result 42")
        return ModelResponse(content="12 + 30 = 42")


class BlockingModel:
    """Wait until cancelled, exposing synchronization points for lifecycle tests."""

    def __init__(self):
        self.started = asyncio.Event()
        self.stopped = asyncio.Event()
        self.release = asyncio.Event()

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.started.set()
        try:
            await self.release.wait()
            return ModelResponse(content="Released")
        finally:
            self.stopped.set()


class CleanupBlockingModel:
    """Expose an asynchronous resource cleanup phase after model cancellation."""

    def __init__(self):
        self.started = asyncio.Event()
        self.cleanup_started = asyncio.Event()
        self.cleanup_release = asyncio.Event()
        self.stopped = asyncio.Event()

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.cleanup_started.set()
            await self.cleanup_release.wait()
            self.stopped.set()


class RecordingTransport:
    """Offline transport that replays canned responses and records the requests.

    ``requests`` is test-only memory: it deliberately keeps the headers so tests
    can assert on authentication and on redaction. Never print it in production
    paths, where the Authorization header carries a live credential.
    """

    def __init__(self, responses: list[HttpResponse]):
        self._responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    async def post(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        json: Mapping[str, Any],
        timeout: float,
    ) -> HttpResponse:
        self.requests.append(
            {"url": url, "headers": dict(headers), "json": dict(json), "timeout": timeout}
        )
        if not self._responses:
            raise AssertionError("RecordingTransport ran out of canned responses")
        return self._responses.pop(0)


class FakeServerClient:
    """In-memory HTTP-client substitute for CLI wiring tests."""

    def __init__(self, base_url: str = "http://test"):
        self.base_url = base_url
        self.session = {
            "session_id": "server-session",
            "workspace": ".",
            "model": None,
            "approval_mode": "ask",
            "message_count": 0,
        }
        self.run = {
            "run_id": "server-run",
            "session_id": "server-session",
            "status": "completed",
            "output": "from server",
            "error": None,
        }

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def list_sessions(self):
        return [self.session]

    async def get_session(self, session_id):
        return self.session

    async def resume_session(self, session_id):
        return self.session

    async def create_session(self, *, workspace, model, approval_mode):
        self.session = {
            **self.session,
            "workspace": workspace,
            "model": model,
            "approval_mode": approval_mode.value,
        }
        return self.session

    async def update_mode(self, session_id, mode):
        self.session["approval_mode"] = mode.value
        return self.session

    async def start_run(self, session_id, *, prompt, max_steps):
        return self.run

    async def get_run(self, run_id):
        return self.run

    async def cancel_run(self, run_id):
        return {"status": "accepted"}

    async def answer_approval(self, run_id, request_id, outcome):
        return {"status": "answered"}

    async def events(self, run_id, *, after=0):
        yield Event(run_id=run_id, seq=1, step=1, type=EventType.RUN_COMPLETED)


class FakeMemorySettings:
    def __init__(self):
        self.enabled = True

    def memory_enabled(self) -> bool:
        return self.enabled

    def set_memory_enabled(self, enabled: bool) -> None:
        self.enabled = enabled


class CallbackModel:
    """Offline request-aware async responder for concurrency and integration tests."""
    def __init__(self, responder):
        self.responder = responder
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request.model_copy(deep=True))
        return await self.responder(request)


class FakeMcpSession:
    """Offline SDK session, shared by adapter and Server lifecycle tests."""
    def __init__(self, tools=None, *, blocked=False, failure=False, discovery_blocked=False):
        from mcp.types import Tool
        self.tools = tools if tools is not None else [Tool(name="add", description="Add integers", inputSchema={
            "type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"], "additionalProperties": False})]
        self.blocked, self.failure = blocked, failure
        self.discovery_blocked = discovery_blocked
        self.discovery_started = asyncio.Event()
        self.calls = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.stopped = asyncio.Event()
        self.closed = False
        self.result = None

    async def list_tools(self, cursor=None):
        from mcp.types import ListToolsResult
        self.discovery_started.set()
        if self.discovery_blocked:
            await self.release.wait()
        return ListToolsResult(tools=self.tools)

    async def call_tool(self, name, arguments):
        from mcp.types import CallToolResult, TextContent
        self.calls.append((name, arguments))
        self.started.set()
        try:
            if self.blocked:
                await self.release.wait()
            if self.failure:
                raise RuntimeError("private remote error")
            if self.result is not None:
                return self.result
            value = arguments.get("a", 0) + arguments.get("b", 0)
            return CallToolResult(content=[TextContent(type="text", text=str(value))], structuredContent={"value": value})
        finally:
            self.stopped.set()

    def context(self):
        from contextlib import asynccontextmanager
        @asynccontextmanager
        async def scope():
            try:
                yield self
            finally:
                self.closed = True
        return scope()


def empty_mcp_tools(workspace):
    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def scope():
        yield ()
    return scope()


class FakeOAuthServer:
    """No-network OAuth issuer exercising the real SDK discovery and PKCE flow."""
    def __init__(self):
        self.requests = []
        self.registrations = 0
        self.refreshes = 0
        self.refresh_failure = False
        self.registration_failure = False
        self.access_token = "oauth-access-private-123456789"
        self.refresh_token = "oauth-refresh-private-123456789"
        self.token_forms = []
        self.token_accept_headers = []
        self.token_error = None

    def transport(self):
        import httpx
        from urllib.parse import parse_qs
        def handle(request):
            self.requests.append((request.method, str(request.url)))
            path = request.url.path
            if path == "/mcp":
                if request.headers.get("Authorization") == "Bearer " + self.access_token:
                    return httpx.Response(200, json={"ok": True})
                return httpx.Response(401, headers={"WWW-Authenticate": 'Bearer resource_metadata="https://oauth.test/.well-known/oauth-protected-resource", scope="read"'})
            if path == "/.well-known/oauth-protected-resource":
                return httpx.Response(200, json={"resource": "https://oauth.test/mcp", "authorization_servers": ["https://oauth.test"], "scopes_supported": ["read"]})
            if path == "/.well-known/oauth-authorization-server":
                return httpx.Response(200, json={"issuer": "https://oauth.test", "authorization_endpoint": "https://oauth.test/authorize", "token_endpoint": "https://oauth.test/token", "registration_endpoint": "https://oauth.test/register", "response_types_supported": ["code"], "code_challenge_methods_supported": ["S256"], "token_endpoint_auth_methods_supported": ["none"], "scopes_supported": ["read"]})
            if path == "/register":
                import json
                self.registrations += 1
                if self.registration_failure:
                    return httpx.Response(400, json={"error": "private registration diagnostic"})
                return httpx.Response(201, json={**json.loads(request.content), "client_id": "nemo-client"})
            if path == "/token":
                self.token_accept_headers.append(request.headers.get("Accept"))
                if self.token_error:
                    return httpx.Response(200, json={"error": self.token_error, "error_description": "private diagnostic with credentials"})
                if request.headers.get("Accept") != "application/json":
                    return httpx.Response(200, text="access_token=private-token&token_type=bearer")
                form = parse_qs(request.content.decode())
                self.token_forms.append(form)
                if form["grant_type"] == ["refresh_token"]:
                    self.refreshes += 1
                    if self.refresh_failure:
                        return httpx.Response(400, json={"error": "private token diagnostic"})
                return httpx.Response(200, json={"access_token": self.access_token, "refresh_token": self.refresh_token, "token_type": "Bearer", "expires_in": 3600, "scope": "read"})
            return httpx.Response(404)
        return httpx.MockTransport(handle)


class FakeBrowserCallback:
    instances = []
    def __init__(self, port=0):
        self.port = port or 43210
        self.state = None
        self.issuer = None
        self.closed = False
        self.future = None
        self.loop = None
        self.instances.append(self)

    async def start(self):
        self.future = asyncio.get_running_loop().create_future()
        self.loop = asyncio.get_running_loop()
        return f"http://127.0.0.1:{self.port}/oauth/callback"

    async def close(self):
        self.closed = True
        if self.future is None:
            return
        if not self.future.done():
            self.future.cancel()
        elif not self.future.cancelled():
            self.future.exception()


class FakeCallbackWriter:
    def __init__(self):
        self.data = b""
        self.closed = False

    def write(self, data):
        self.data += data

    async def drain(self):
        pass

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass
