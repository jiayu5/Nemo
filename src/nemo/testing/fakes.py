from typing import Any, Mapping

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
