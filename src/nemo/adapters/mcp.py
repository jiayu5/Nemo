"""Official MCP SDK connections, owned and closed by one dedicated task."""
import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import re

from jsonschema import Draft202012Validator
from pydantic import Field
from mcp import ClientSession, StdioServerParameters
from mcp.shared.exceptions import McpError
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
import httpx

from nemo.config.secrets import SecretLoader
from nemo.core.contracts.errors import ToolFailure, first_public_message
from nemo.core.contracts.types import Contract
from nemo.core.contracts.tools import ExecutionContext
from nemo.core.tools.limits import truncate_text
from nemo.redaction import redact


class McpHttpClient(httpx.AsyncClient):
    """Remember safe transport failures even when SDK writer tasks consume them."""
    failure_message = None

    async def send(self, request, **kwargs):
        try:
            return await super().send(request, **kwargs)
        except ToolFailure as exc:
            self.failure_message = exc.public_message
            raise
        except httpx.TimeoutException:
            self.failure_message = "MCP HTTP request timed out; check network access and the proxy environment variable in MCP settings"
            raise
        except httpx.TransportError:
            self.failure_message = "MCP HTTP connection failed; check network access, TLS and the proxy environment variable in MCP settings"
            raise


class RemoteArguments(Contract):
    payload: dict = Field(default_factory=dict)


def tool_name(server: str, name: str) -> str:
    value = f"mcp_{server}_{name}"
    if len(value) <= 64 and re.fullmatch(r"[A-Za-z0-9_-]+", value):
        return value
    return re.sub(r"[^A-Za-z0-9_-]", "_", value)[:49] + "_" + sha256(value.encode()).hexdigest()[:14]


def check_schema(value):
    if isinstance(value, dict):
        for key, part in value.items():
            if key in {"$ref", "$dynamicRef"} and isinstance(part, str) and not part.startswith("#"):
                raise ValueError("External schema references are unsupported")
            check_schema(part)
    elif isinstance(value, list):
        for part in value:
            check_schema(part)


class McpTool:
    arguments_type = RemoteArguments

    def __init__(self, connection, tool):
        self.connection = connection
        self.remote_name = tool.name
        self.name = tool_name(connection.server_id, tool.name)
        self.description = connection.safe(f"MCP {connection.server_id}: " + (tool.description or tool.name)[:2000])
        self.input_schema = json.loads(connection.safe(json.dumps(tool.inputSchema, ensure_ascii=False)))
        check_schema(self.input_schema)
        Draft202012Validator.check_schema(self.input_schema)
        self.validator = Draft202012Validator(self.input_schema)
        self.read_only = tool.name in connection.config.read_only_tools
        self.subagent_allowed = self.read_only and tool.name in connection.config.subagent_tools

    def parse_arguments(self, arguments: dict) -> RemoteArguments:
        try:
            valid = self.validator.is_valid(arguments)
        except Exception:
            raise ValueError("MCP schema could not validate arguments") from None
        if not valid:
            raise ValueError("Arguments do not match MCP schema")
        return RemoteArguments(payload=arguments)

    def summarize(self, arguments) -> str:
        return f"MCP {self.connection.server_id}: {self.remote_name}"

    async def execute(self, arguments, context: ExecutionContext) -> dict:
        if context.workspace.resolve() != self.connection.workspace:
            raise ToolFailure("MCP connection belongs to a different workspace")
        try:
            async with asyncio.timeout(min(context.default_timeout_seconds, context.max_timeout_seconds,
                                            self.connection.config.timeout_seconds)):
                result = await self.connection.call(self.remote_name, arguments.payload)
        except TimeoutError:
            raise ToolFailure("MCP tool timed out; it was cancelled and was not retried") from None
        if result.isError:
            raise ToolFailure("MCP server reported a tool error; the call was not retried")
        content = []
        for item in result.content:
            if item.type == "text":
                content.append({"type": "text", "text": item.text})
            elif item.type == "resource" and getattr(item.resource, "text", None) is not None:
                content.append({"type": "text", "text": item.resource.text})
            else:
                content.append({"type": item.type, "note": "Non-text content is not supported by this model client"})
        raw = self.connection.safe(json.dumps({"content": content, "structured_content": result.structuredContent}, ensure_ascii=False))
        if len(raw) > context.max_output_chars:
            # JSON escaping and metadata count toward the same output limit.
            limit = max(0, context.max_output_chars - 200)
            while limit:
                bounded = {"text": truncate_text(raw, limit), "truncated": True}
                if len(json.dumps(bounded, ensure_ascii=False)) <= context.max_output_chars:
                    return bounded
                limit //= 2
            raise ToolFailure("MCP output limit is too small")
        return json.loads(raw)


class McpConnection:
    def __init__(self, server_id, config, workspace: Path, *, secret_loader=None, http_client_factory=McpHttpClient,
                 oauth_manager=None, oauth_interaction=None):
        self.server_id, self.config = server_id, config
        self.workspace = workspace.resolve()
        self.loader = secret_loader or SecretLoader()
        self.http_client_factory = http_client_factory
        self.state = "disconnected"
        self.tools = ()
        self._queue = asyncio.Queue()
        self._ready = None
        self._owner = None
        self._active = set()
        self._secrets = []
        self._closing = False
        self.oauth_manager = oauth_manager
        self.oauth_interaction = oauth_interaction or {}
        self._http_client = None

    def safe(self, value: str) -> str:
        for secret in self._secrets:
            value = secret.redact(value)
        return redact(value)

    def _values(self, references):
        values = {}
        for name, reference in references.items():
            secret = self.loader.load(reference)
            self._secrets.append(secret)
            values[name] = secret.reveal()
        return values

    @asynccontextmanager
    async def _session(self):
        async with AsyncExitStack() as stack:
            if self.config.transport == "stdio":
                # SDK supplies its minimal inherited environment, plus explicit references.
                errlog = stack.enter_context(open(os.devnull, "w"))
                streams = await stack.enter_async_context(stdio_client(StdioServerParameters(
                    command=self.config.command, args=self.config.args,
                    cwd=str(self.workspace), env=self._values(self.config.env)), errlog=errlog))
            else:
                proxy = None
                if self.config.proxy_env:
                    proxy_secret = self.loader.load(self.config.proxy_env)
                    self._secrets.append(proxy_secret)
                    proxy = proxy_secret.reveal()
                auth = None
                if self.config.auth_mode == "oauth":
                    if self.oauth_manager is None:
                        raise ToolFailure("MCP OAuth is unavailable; connect the account in MCP settings")
                    auth = self.oauth_manager.provider(self.server_id, self.config, loader=self.loader,
                        sink=self._secrets, **self.oauth_interaction)
                client = await stack.enter_async_context(self.http_client_factory(
                    headers=self._values(self.config.headers), timeout=httpx.Timeout(self.config.timeout_seconds),
                    trust_env=False, proxy=proxy, auth=auth))
                self._http_client = client
                streams = await stack.enter_async_context(streamable_http_client(self.config.url, http_client=client))
            session = await stack.enter_async_context(ClientSession(streams[0], streams[1],
                read_timeout_seconds=timedelta(seconds=300 if self.oauth_interaction else self.config.timeout_seconds)))
            await session.initialize()
            yield session

    async def start(self):
        self._ready = asyncio.get_running_loop().create_future()
        self._owner = asyncio.create_task(self._serve(), name=f"nemo-mcp-{self.server_id}")
        try:
            async with asyncio.timeout(300 if self.oauth_interaction else self.config.timeout_seconds):
                await asyncio.shield(self._ready)
        except BaseException:
            await self.close()
            raise

    async def _invoke(self, session, name, arguments, future):
        try:
            result = await session.call_tool(name, arguments)
            if not future.done():
                future.set_result(result)
        except asyncio.CancelledError:
            if not future.done():
                future.set_exception(ToolFailure("MCP request was interrupted; it was not retried"))
            raise
        except Exception as exc:
            if not isinstance(exc, McpError):
                self.state = "failed"
                self._queue.put_nowait(None)
            if not future.done():
                future.set_exception(ToolFailure("MCP call failed or connection was lost; it was not retried"))

    async def _serve(self):
        self.state = "connecting"
        try:
            async with self._session() as session:
                tools, cursor = [], None
                for _ in range(16):
                    page = await session.list_tools(cursor=cursor)
                    tools.extend(page.tools)
                    if len(tools) > 64:
                        raise ToolFailure("MCP server exposes too many tools")
                    cursor = page.nextCursor
                    if not cursor:
                        break
                else:
                    raise ToolFailure("MCP discovery pagination limit reached")
                self.tools = tuple(McpTool(self, t) for t in tools)
                if len({t.name for t in self.tools}) != len(self.tools):
                    raise ToolFailure("MCP server returned duplicate tools")
                self.state = "connected"
                if not self._ready.done():
                    self._ready.set_result(None)
                try:
                    while True:
                        item = await self._queue.get()
                        if item is None:
                            break
                        name, arguments, future = item
                        if future.cancelled():
                            continue
                        task = asyncio.create_task(self._invoke(session, name, arguments, future))
                        self._active.add(task)
                        task.add_done_callback(self._active.discard)
                        future.add_done_callback(lambda f, t=task: t.cancel() if f.cancelled() and not t.done() else None)
                finally:
                    for task in self._active:
                        task.cancel()
                    # Same task enters/exits the SDK's AnyIO context managers.
                    import anyio
                    with anyio.CancelScope(shield=True):
                        await asyncio.gather(*self._active, return_exceptions=True)
        except BaseException as exc:
            self.state = "failed"
            if not self._ready.done():
                # Expected failures carry an actionable, secret-free message:
                # an unauthorized account must say so instead of failing opaquely.
                self._ready.set_exception(ToolFailure(
                    first_public_message(exc) or getattr(self._http_client, "failure_message", None)
                    or "MCP connection or tool discovery failed"))
        finally:
            if self.state != "failed":
                self.state = "disconnected"
            while not self._queue.empty():
                item = self._queue.get_nowait()
                if item is not None and not item[2].done():
                    item[2].set_exception(ToolFailure("MCP connection was closed"))

    async def call(self, name, arguments):
        if self.state != "connected" or self._closing:
            raise ToolFailure("MCP connection is unavailable; start a new run or test the connection")
        future = asyncio.get_running_loop().create_future()
        self._queue.put_nowait((name, arguments, future))
        return await future

    async def close(self):
        if not self._owner:
            return
        self._closing = True
        self._queue.put_nowait(None)
        # Starting handshake must also be interruptible.
        if (self.state == "connecting" or not self._ready.done()) and not self._owner.cancelling():
            self._owner.cancel()
        cancelled = False
        while not self._owner.done():
            try:
                await asyncio.shield(self._owner)
            except asyncio.CancelledError:
                cancelled = True
        await self._owner
        if self._ready.done() and not self._ready.cancelled():
            self._ready.exception()  # Consume an abandoned startup error.
        if cancelled:
            raise asyncio.CancelledError


@asynccontextmanager
async def connect_tools(servers, workspace: Path, *, secret_loader=None, http_client_factory=McpHttpClient, oauth_manager=None):
    connections = []
    try:
        for server_id, config in servers.items():
            if config.enabled:
                connection = McpConnection(server_id, config, workspace, secret_loader=secret_loader,
                    http_client_factory=http_client_factory, oauth_manager=oauth_manager)
                connections.append(connection)
                await connection.start()
        yield tuple(tool for connection in connections for tool in connection.tools)
    finally:
        cancelled = False
        for connection in reversed(connections):
            try:
                await connection.close()
            except asyncio.CancelledError:
                cancelled = True
        if cancelled:
            raise asyncio.CancelledError
