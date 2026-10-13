import asyncio
import base64
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import httpx
from mcp.shared.auth import OAuthToken
from nemo.adapters.mcp_oauth import BrowserCallback, McpOAuthManager, OAuthStorage
from nemo.config.secrets import SecretLoader
from nemo.core.contracts.errors import ToolFailure
from nemo.core.contracts.mcp import McpServerConfig
from nemo.testing.fakes import FakeOAuthServer, FakeBrowserCallback, FakeCallbackWriter


class McpOAuthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = McpServerConfig(transport="streamable_http", url="https://oauth.test/mcp", auth_mode="oauth")
        self.server = FakeOAuthServer()
        server = self.server
        class Connection:
            tools = ()
            def __init__(inner, server_id, config, workspace, *, oauth_manager, oauth_interaction):
                inner.auth = oauth_manager.provider(server_id, config, **oauth_interaction)
                inner.closed = False
            async def start(inner):
                async with httpx.AsyncClient(transport=server.transport(), auth=inner.auth) as client:
                    response = await client.get(self.config.url)
                    response.raise_for_status()
            async def close(inner):
                inner.closed = True
        self.manager = McpOAuthManager(self.root / "credentials", connection_factory=Connection)
        self.addAsyncCleanup(self.manager.shutdown)
        self.callback_patch = patch("nemo.adapters.mcp_oauth.BrowserCallback", FakeBrowserCallback)
        self.callback_patch.start()
        self.addCleanup(self.callback_patch.stop)

    async def wait_status(self, *statuses):
        async with asyncio.timeout(2):
            while True:
                status = self.manager.status("demo", self.config)
                if status["status"] in statuses:
                    return status
                if status.get("authorization_url") and "url" in statuses:
                    return status
                await asyncio.sleep(.005)

    async def authorize(self):
        await self.manager.start("demo", self.config, self.root)
        status = await self.wait_status("url", "failed")
        self.assertEqual(status["status"], "authorizing", status)
        url = status["authorization_url"]
        params = parse_qs(urlsplit(url).query)
        self.assertEqual(params["code_challenge_method"], ["S256"])
        FakeBrowserCallback.instances[-1].future.set_result(("authorization-code", params["state"][0]))
        result = await self.wait_status("connected", "failed")
        self.assertEqual(result["status"], "connected", result)
        verifier = self.server.token_forms[-1]["code_verifier"][0]
        challenge = base64.urlsafe_b64encode(sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(params["code_challenge"], [challenge])
        self.assertTrue(FakeBrowserCallback.instances[-1].closed)

    async def test_sdk_dynamic_registration_pkce_storage_and_restart_reuse(self):
        await self.authorize()
        storage = self.manager.storage("demo", self.config)
        self.assertEqual(storage.path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(self.server.access_token, json.dumps(self.manager.status("demo", self.config)))
        other = McpOAuthManager(storage.root)
        async with httpx.AsyncClient(transport=self.server.transport(), auth=other.provider("demo", self.config)) as client:
            self.assertEqual((await client.get(self.config.url)).status_code, 200)
        self.assertEqual(self.server.registrations, 1)
        changed = self.config.model_copy(update={"url": "https://other.test/mcp"})
        self.assertEqual(other.status("demo", changed)["status"], "not_connected")

    async def test_expired_tokens_refresh_once_across_parallel_connections(self):
        await self.authorize()
        storage = self.manager.storage("demo", self.config)
        storage.write(expires_at=time.time() - 1)
        async def request():
            async with httpx.AsyncClient(transport=self.server.transport(), auth=self.manager.provider("demo", self.config)) as client:
                return (await client.get(self.config.url)).status_code
        self.assertEqual(await asyncio.gather(request(), request()), [200, 200])
        self.assertEqual(self.server.refreshes, 1)
        self.assertTrue(all(value == "application/json" for value in self.server.token_accept_headers))

    async def test_token_exchange_rejection_reports_safe_actionable_error(self):
        self.server.token_error = "incorrect_client_credentials"
        await self.manager.start("demo", self.config, self.root)
        status = await self.wait_status("url", "failed")
        params = parse_qs(urlsplit(status["authorization_url"]).query)
        FakeBrowserCallback.instances[-1].future.set_result(("authorization-code", params["state"][0]))
        result = await self.wait_status("failed")
        self.assertIn("client ID and client secret", result["error"])
        self.assertNotIn("private diagnostic", json.dumps(result))
        self.assertFalse(self.manager.storage("demo", self.config).read().get("tokens"))

    async def test_failed_refresh_requires_login_without_opening_browser(self):
        await self.authorize()
        self.manager.storage("demo", self.config).write(expires_at=time.time() - 1)
        self.server.refresh_failure = True
        with self.assertRaises(ToolFailure):
            async with httpx.AsyncClient(transport=self.server.transport(), auth=self.manager.provider("demo", self.config)) as client:
                await client.get(self.config.url)
        self.assertEqual(self.manager.status("demo", self.config)["status"], "not_connected")

    async def test_cancel_disconnect_and_late_credential_write_fencing(self):
        await self.manager.start("demo", self.config, self.root)
        await self.wait_status("url")
        await self.manager.cancel("demo")
        self.assertTrue(FakeBrowserCallback.instances[-1].closed)
        self.assertEqual(self.manager.status("demo", self.config)["status"], "cancelled")
        old = self.manager.storage("demo", self.config)
        await self.manager.disconnect("demo", self.config)
        with self.assertRaises(ToolFailure):
            await old.set_tokens(OAuthToken(access_token="late-token", token_type="Bearer"))
        self.assertEqual(self.manager.status("demo", self.config)["status"], "not_connected")

    async def test_failed_registration_is_safe_and_callback_closes(self):
        self.server.registration_failure = True
        await self.manager.start("demo", self.config, self.root)
        result = await self.wait_status("failed")
        self.assertNotIn("private", json.dumps(result))
        self.assertTrue(FakeBrowserCallback.instances[-1].closed)

    async def test_callback_rejects_wrong_state_replay_and_denial(self):
        callback = BrowserCallback()
        callback.future = asyncio.get_running_loop().create_future()
        callback.state = "expected"
        callback.issuer = "https://oauth.test"
        async def receive(query):
            reader, writer = asyncio.StreamReader(), FakeCallbackWriter()
            reader.feed_data(f"GET /oauth/callback?{query} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode())
            reader.feed_eof()
            await callback.handle(reader, writer)
            return writer.data
        self.assertIn(b"400", await receive("state=wrong&code=secret"))
        self.assertFalse(callback.future.done())
        self.assertIn(b"400", await receive("state=expected&code=secret&iss=https://wrong.test"))
        self.assertIn(b"200", await receive("state=expected&code=secret"))
        self.assertEqual(await callback.future, ("secret", "expected"))
        self.assertIn(b"400", await receive("state=expected&code=secret"))
        callback.future = asyncio.get_running_loop().create_future()
        self.assertIn(b"200", await receive("state=expected&error=access_denied"))
        with self.assertRaises(ToolFailure):
            await callback.future
        await callback.close()

    async def test_pre_registered_client_and_config_validation(self):
        config = self.config.model_copy(update={"oauth_client_id": "known-client", "oauth_issuer": "https://oauth.test", "oauth_client_secret_env": "CLIENT_SECRET"})
        storage = OAuthStorage(self.root, "demo", config, secret_loader=SecretLoader(environ={"CLIENT_SECRET": "private-client-secret"}))
        info = await storage.get_client_info()
        self.assertEqual(info.client_id, "known-client")
        self.assertEqual(info.client_secret, "private-client-secret")
        self.assertFalse(storage.path.exists())
        for values in ({"transport": "stdio", "command": "x", "auth_mode": "oauth"},
                       {"transport": "streamable_http", "url": "http://example.com/mcp", "auth_mode": "oauth"},
                       {"transport": "streamable_http", "url": self.config.url, "auth_mode": "oauth", "headers": {"Authorization": "SECRET"}}):
            with self.assertRaises(ValueError):
                McpServerConfig(**values)

    async def test_real_authorization_server_then_stored_token_reuse_without_browser(self):
        import functools
        from examples.mcp_oauth_demo import create_server
        from nemo.adapters.mcp import McpConnection
        server = create_server(8799)
        app = server.streamable_http_app()
        def factory(**kwargs):
            return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), **kwargs)
        def opener(url):
            # Plays the browser: asks the real authorization endpoint, then delivers the code.
            response = asyncio.run(fetch(url))
            callback = FakeBrowserCallback.instances[-1]
            params = parse_qs(urlsplit(response.headers["location"]).query)
            callback.loop.call_soon_threadsafe(callback.future.set_result, (params["code"][0], params["state"][0]))
        async def fetch(url):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
                return await client.get(url)
        config = self.config.model_copy(update={"url": "http://127.0.0.1:8799/mcp"})
        manager = McpOAuthManager(self.root / "live",
            connection_factory=functools.partial(McpConnection, http_client_factory=factory), browser_opener=opener)
        self.addAsyncCleanup(manager.shutdown)
        async with server.session_manager.run():
            async def tools():
                connection = McpConnection("demo", config, self.root,
                    oauth_manager=manager, http_client_factory=factory)
                try:
                    await connection.start()
                    return [tool.name for tool in connection.tools]
                finally:
                    await connection.close()
            with self.assertRaises(ToolFailure) as unauthorized:
                await tools()
            self.assertIn("connect it in MCP settings", unauthorized.exception.public_message)
            await manager.start("demo", config, self.root)
            async with asyncio.timeout(20):
                while True:
                    status = manager.status("demo", config)
                    if status["status"] in {"connected", "failed", "cancelled"}:
                        break
                    await asyncio.sleep(.01)
            self.assertEqual(status["status"], "connected", status)
            # Stored credentials authorize later runs and connection tests without a browser.
            self.assertEqual(await tools(), ["mcp_demo_add"])
            self.assertNotIn(manager.storage("demo", config).read()["tokens"]["access_token"], json.dumps(status))
