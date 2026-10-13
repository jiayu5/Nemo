import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from mcp.types import Tool, CallToolResult, TextContent, ToolAnnotations
from nemo.adapters.mcp import McpConnection, McpHttpClient, tool_name
from nemo.config.secrets import SecretLoader
from nemo.core.contracts.mcp import McpServerConfig
from nemo.core.contracts.tools import ExecutionContext
from nemo.core.contracts.types import ToolCall
from nemo.core.runtime.subagents import SubagentManager
from nemo.core.context.builder import ContextBuilder
from nemo.core.tools.registry import ToolRegistry
from nemo.core.tools.policy import ReadOnlyPolicy
from nemo.testing.fakes import FakeMcpSession, FakeModel


class McpTests(unittest.IsolatedAsyncioTestCase):
    async def test_transport_failure_survives_sdk_writer_error_masking(self):
        import httpx
        def fail(request):
            raise httpx.ConnectTimeout("private transport diagnostic with credentials", request=request)
        def factory(**kwargs):
            return McpHttpClient(transport=httpx.MockTransport(fail), **kwargs)
        connection = McpConnection("remote", McpServerConfig(transport="streamable_http", url="https://example.com/mcp"),
            Path.cwd(), http_client_factory=factory)
        with self.assertRaises(Exception) as result:
            await connection.start()
        self.assertIn("proxy environment variable", result.exception.public_message)
        self.assertNotIn("private", result.exception.public_message)
        self.assertTrue(connection._owner.done())

    async def connection(self, session, **settings):
        config = McpServerConfig(command="unused", **settings)
        connection = McpConnection("demo", config, Path.cwd())
        connection._session = session.context
        await connection.start()
        self.addAsyncCleanup(connection.close)
        return connection

    async def test_schema_validation_policy_and_explicit_child_allowlist(self):
        session = FakeMcpSession()
        session.tools[0].annotations = ToolAnnotations(readOnlyHint=True)
        connection = await self.connection(session)
        tool = connection.tools[0]
        self.assertFalse(tool.read_only)  # Server annotations cannot grant permissions.
        registry = ToolRegistry(connection.tools, context=ExecutionContext(workspace=Path.cwd()), policy=ReadOnlyPolicy())
        denied = await registry.execute(ToolCall(id="x", name=tool.name, arguments={"a": 20, "b": 22}))
        self.assertEqual(denied.error.code, "denied")
        self.assertEqual(session.calls, [])
        allowed = await self.connection(FakeMcpSession(), read_only_tools=["add"])
        registry = ToolRegistry(allowed.tools, context=ExecutionContext(workspace=Path.cwd()))
        self.assertEqual(registry.schemas()[0].parameters, session.tools[0].inputSchema)
        for args in ({"a": True, "b": 1}, {"a": "1", "b": 2}, {"a": 1}, {"a": 1, "b": 2, "extra": 3}):
            result = await registry.execute(ToolCall(id="bad", name=tool.name, arguments=args))
            self.assertEqual(result.error.code, "invalid_arguments")
        result = await registry.execute(ToolCall(id="ok", name=tool.name, arguments={"a": 20, "b": 22}))
        self.assertEqual(result.output["structured_content"], {"value": 42})
        manager = SubagentManager(FakeModel([]), allowed.tools, context=ContextBuilder(), policy=ReadOnlyPolicy())
        self.assertEqual(manager.child_tools, ())
        child_connection = await self.connection(FakeMcpSession(), read_only_tools=["add"], subagent_tools=["add"])
        manager = SubagentManager(FakeModel([]), child_connection.tools, context=ContextBuilder(), policy=ReadOnlyPolicy())
        self.assertEqual([t.name for t in manager.child_tools], ["mcp_demo_add"])

    async def test_timeout_cancels_call_and_connection_closes_without_retry(self):
        session = FakeMcpSession(blocked=True)
        connection = await self.connection(session)
        registry = ToolRegistry(connection.tools, context=ExecutionContext(workspace=Path.cwd(), default_timeout_seconds=.02))
        result = await registry.execute(ToolCall(id="call", name="mcp_demo_add", arguments={"a": 1, "b": 2}))
        self.assertIn("timed out", result.error.message)
        await asyncio.wait_for(session.stopped.wait(), 1)
        self.assertEqual(len(session.calls), 1)
        await connection.close()
        self.assertTrue(session.closed)
        self.assertTrue(connection._owner.done())
        self.assertEqual(connection.state, "disconnected")

    async def test_expected_and_unexpected_remote_errors_are_safe(self):
        session = FakeMcpSession(failure=True)
        connection = await self.connection(session)
        registry = ToolRegistry(connection.tools, context=ExecutionContext(workspace=Path.cwd()))
        result = await registry.execute(ToolCall(id="x", name="mcp_demo_add", arguments={"a": 1, "b": 2}))
        self.assertEqual(result.error.code, "execution_error")
        self.assertNotIn("private", result.error.message)
        session = FakeMcpSession()
        connection = await self.connection(session)
        registry = ToolRegistry(connection.tools, context=ExecutionContext(workspace=Path.cwd()))
        session.result = CallToolResult(content=[TextContent(type="text", text="private diagnostic")], isError=True)
        result = await registry.execute(ToolCall(id="x", name="mcp_demo_add", arguments={"a": 1, "b": 2}))
        self.assertNotIn("private", result.error.message)

    async def test_output_and_known_credentials_are_bounded_and_redacted(self):
        session = FakeMcpSession()
        secret = "credential-private-123456789"
        config = McpServerConfig(command="unused", env={"TOKEN": "TEST_TOKEN"})
        loader = SecretLoader(env_file=Path("absent.env"), environ={"TEST_TOKEN": secret})
        connection = McpConnection("demo", config, Path.cwd(), secret_loader=loader)
        connection._values(config.env)
        connection._session = session.context
        await connection.start()
        self.addAsyncCleanup(connection.close)
        session.result = CallToolResult(content=[TextContent(type="text", text=secret + "\\n" * 2000)])
        registry = ToolRegistry(connection.tools, context=ExecutionContext(workspace=Path.cwd(), max_output_chars=500))
        result = await registry.execute(ToolCall(id="x", name="mcp_demo_add", arguments={"a": 1, "b": 2}))
        self.assertIsNone(result.error)
        self.assertTrue(result.output["truncated"])
        self.assertNotIn(secret, json.dumps(result.output))
        self.assertLessEqual(len(json.dumps(result.output, ensure_ascii=False)), 500)

    async def test_foreign_workspace_and_external_schema_reference_are_rejected(self):
        session = FakeMcpSession()
        connection = await self.connection(session)
        registry = ToolRegistry(connection.tools, context=ExecutionContext(workspace=Path('/tmp')))
        result = await registry.execute(ToolCall(id="x", name="mcp_demo_add", arguments={"a": 1, "b": 2}))
        self.assertEqual(result.error.code, "execution_error")
        bad = FakeMcpSession([Tool(name="bad", inputSchema={"type": "object", "$ref": "https://example.com/schema"})])
        connection = McpConnection("bad", McpServerConfig(command="unused"), Path.cwd())
        connection._session = bad.context
        with self.assertRaises(Exception):
            await connection.start()
        self.assertTrue(bad.closed)
        self.assertTrue(connection._owner.done())

    def test_names_are_stable_unique_and_model_compatible(self):
        self.assertEqual(tool_name("demo", "add"), "mcp_demo_add")
        names = [tool_name("demo", name) for name in ("a/b", "a.b", "a" * 200)]
        self.assertEqual(len(set(names)), 3)
        self.assertTrue(all(len(n) <= 64 for n in names))
        self.assertEqual(names[0], tool_name("demo", "a/b"))

    async def test_real_stdio_sdk_discovers_calls_and_cleans_up(self):
        workspace = Path(__file__).resolve().parents[1]
        config = McpServerConfig(command=sys.executable, args=["examples/mcp_demo.py"], read_only_tools=["add"])
        connection = McpConnection("demo", config, workspace)
        try:
            await connection.start()
            registry = ToolRegistry(connection.tools, context=ExecutionContext(workspace=workspace))
            result = await registry.execute(ToolCall(id="x", name="mcp_demo_add", arguments={"a": 20, "b": 22}))
            self.assertIsNone(result.error)
            self.assertEqual(result.output["structured_content"]["result"], 42)
        finally:
            await connection.close()
        self.assertTrue(connection._owner.done())

    async def test_real_streamable_http_sdk_against_demo_server(self):
        import httpx
        from examples.mcp_demo import create_server
        import logging
        root = logging.getLogger()
        level, handlers = root.level, list(root.handlers)
        self.addCleanup(lambda: root.setLevel(level))
        self.addCleanup(lambda: setattr(root, "handlers", handlers))
        server = create_server()
        app = server.streamable_http_app()
        def factory(headers=None, timeout=None, auth=None, **kwargs):
            return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), headers=headers,
                                     timeout=timeout, auth=auth, follow_redirects=True)
        connection = McpConnection("httpdemo", McpServerConfig(transport="streamable_http", url="http://127.0.0.1:8767/mcp",
                                    read_only_tools=["add"]), Path.cwd(), http_client_factory=factory)
        async with server.session_manager.run():
            try:
                await connection.start()
                registry = ToolRegistry(connection.tools, context=ExecutionContext(workspace=Path.cwd()))
                result = await registry.execute(ToolCall(id="x", name="mcp_httpdemo_add", arguments={"a": 20, "b": 22}))
                self.assertIsNone(result.error)
                self.assertEqual(result.output["structured_content"]["result"], 42)
            finally:
                await connection.close()
        self.assertTrue(connection._owner.done())
