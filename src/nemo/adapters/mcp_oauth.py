"""Browser OAuth using the official MCP SDK; no credentials leave this adapter."""
import asyncio
from contextlib import contextmanager
from hashlib import sha256
import fcntl
import json
import logging
import os
from pathlib import Path
import secrets
import tempfile
import time
from urllib.parse import parse_qs, urlsplit

import httpx
from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthMetadata, OAuthToken
from mcp.shared.auth import ProtectedResourceMetadata

from nemo.config.secrets import SecretLoader
from nemo.core.contracts.errors import ConfigError, ToolFailure, first_public_message
from nemo.core.contracts.secrets import SecretValue

DEFAULT_OAUTH_ROOT = Path.home() / ".nemo" / "mcp-oauth"


def identity(server_id, config):
    # URL, application identity and redirect policy changes cannot reuse credentials.
    data = [server_id, config.url, config.oauth_client_id, config.oauth_issuer,
            config.oauth_client_secret_env, config.oauth_redirect_port, config.oauth_client_metadata_url]
    return sha256(json.dumps(data).encode()).hexdigest()


def validate_url(url):
    parsed = urlsplit(str(url))
    if parsed.username or parsed.password or not parsed.hostname:
        raise ToolFailure("Invalid OAuth endpoint")
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}):
        raise ToolFailure("OAuth endpoints require HTTPS")


class _NoOAuthDiagnostics(logging.Filter):
    def filter(self, record):
        # SDK exception diagnostics may contain callback codes or token response bodies.
        return False


logging.getLogger("mcp.client.auth.oauth2").addFilter(_NoOAuthDiagnostics())


class OAuthStorage:
    """Atomic, owner-only per-connection storage with disconnect generation fencing."""
    def __init__(self, root, server_id, config, *, secret_loader=None, sink=None):
        self.root = Path(root)
        self.path = self.root / (identity(server_id, config) + ".json")
        self.config = config
        self.loader = secret_loader or SecretLoader()
        self.sink = sink if sink is not None else []
        self.generation = self.read().get("generation", 0)

    def read(self):
        if not self.path.exists():
            return {}
        if self.path.is_symlink() or self.path.stat().st_mode & 0o077 or self.path.stat().st_size > 262144:
            raise ConfigError("OAuth credential file must be private and regular")
        try:
            value = json.loads(self.path.read_text())
            if not isinstance(value, dict):
                raise ValueError
            return value
        except (ValueError, OSError):
            raise ConfigError("OAuth credentials could not be read; reconnect the account") from None

    @contextmanager
    def locked(self):
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.root.is_symlink():
            raise ConfigError("OAuth credential directory cannot be a symbolic link")
        lock_path = self.path.with_suffix(".lock")
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def write(self, **changes):
        with self.locked():
            value = self.read()
            if value.get("generation", 0) != self.generation:
                raise ToolFailure("OAuth authorization was disconnected; reconnect in MCP settings")
            value.update(changes)
            self._replace(value)

    def _replace(self, value):
        fd, name = tempfile.mkstemp(dir=self.root, prefix=".oauth-")
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(value, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def disconnect(self):
        with self.locked():
            value = self.read()
            self._replace({"generation": value.get("generation", 0) + 1})

    def remember(self, value):
        if value:
            self.sink.append(SecretValue(value))

    async def get_tokens(self):
        value = self.read()
        if not value.get("tokens"):
            return None
        token = OAuthToken.model_validate(value["tokens"])
        if value.get("expires_at") is not None:
            token.expires_in = max(0, int(value["expires_at"] - time.time()))
        self.remember(token.access_token)
        self.remember(token.refresh_token)
        return token

    async def set_tokens(self, tokens):
        self.remember(tokens.access_token)
        self.remember(tokens.refresh_token)
        self.write(tokens=tokens.model_dump(mode="json"),
                   expires_at=time.time() + tokens.expires_in if tokens.expires_in is not None else None)

    async def get_client_info(self):
        value = self.read().get("client")
        if value:
            info = OAuthClientInformationFull.model_validate(value)
        elif self.config.oauth_client_id:
            info = OAuthClientInformationFull(client_id=self.config.oauth_client_id,
                issuer=self.config.oauth_issuer, redirect_uris=[self.read().get("redirect_uri", "http://127.0.0.1/oauth/callback")],
                token_endpoint_auth_method="client_secret_post" if self.config.oauth_client_secret_env else "none")
        else:
            return None
        if self.config.oauth_client_secret_env:
            info.client_secret = self.loader.load(self.config.oauth_client_secret_env).reveal()
        self.remember(info.client_secret)
        return info

    async def set_client_info(self, info):
        self.remember(info.client_secret)
        self.write(client=info.model_dump(mode="json"))

    def status(self):
        value = self.read()
        if not value.get("tokens"):
            return "not_connected"
        if value.get("expires_at") is not None and value["expires_at"] <= time.time() and not value["tokens"].get("refresh_token"):
            return "needs_authorization"
        return "connected"


class BrowserCallback:
    """One-use bounded loopback callback; no raw requests or query strings are logged."""
    def __init__(self, port=0):
        self.port = port
        self.server = None
        self.future = None
        self.state = None
        self.issuer = None
        self.handlers = set()

    async def start(self):
        self.future = asyncio.get_running_loop().create_future()
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", self.port, limit=8192)
        self.port = self.server.sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{self.port}/oauth/callback"

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.handlers.add(task)
        status, message = "400 Bad Request", "Invalid authorization callback."
        try:
            async with asyncio.timeout(5):
                request = await reader.readuntil(b"\r\n\r\n")
            first = request.split(b"\r\n", 1)[0].decode("ascii")
            method, target, _ = first.split(" ", 2)
            parsed = urlsplit(target)
            params = parse_qs(parsed.query, keep_blank_values=True)
            state = params.get("state", [])
            issuer = params.get("iss", [])
            issuer_matches = not issuer or (len(issuer) == 1 and issuer[0] == self.issuer)
            if method == "GET" and parsed.path == "/oauth/callback" and issuer_matches and len(state) == 1 and self.state and secrets.compare_digest(state[0], self.state) and not self.future.done():
                if "error" in params:
                    self.future.set_exception(ToolFailure("Authorization was denied by the user or service"))
                    status, message = "200 OK", "Authorization declined. Return to Nemo."
                elif len(params.get("code", [])) == 1 and params["code"][0]:
                    self.future.set_result((params["code"][0], state[0]))
                    status, message = "200 OK", "Authorization received. You can return to Nemo."
            body = message.encode()
            writer.write(f"HTTP/1.1 {status}\r\nContent-Type: text/plain; charset=utf-8\r\nCache-Control: no-store\r\nReferrer-Policy: no-referrer\r\nConnection: close\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body)
            await writer.drain()
        except (Exception, asyncio.CancelledError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()
            self.handlers.discard(task)

    async def close(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        tasks = list(self.handlers)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.future and self.future.done() and not self.future.cancelled():
            self.future.exception()
        elif self.future:
            self.future.cancel()


class NemoOAuthProvider(OAuthClientProvider):
    def __init__(self, config, storage, lock, *, redirect=None, callback=None, redirect_uri=None):
        self.nemo_storage = storage
        self.nemo_lock = lock
        saved = storage.read().get("redirect_uri")
        super().__init__(config.url, OAuthClientMetadata(client_name="Nemo", redirect_uris=[redirect_uri or saved or "http://127.0.0.1/oauth/callback"],
            grant_types=["authorization_code", "refresh_token"], response_types=["code"], token_endpoint_auth_method="none"),
            storage, redirect_handler=redirect, callback_handler=callback, client_metadata_url=config.oauth_client_metadata_url)
        self.interactive = redirect is not None

    async def _initialize(self):
        await super()._initialize()
        if self.context.current_tokens:
            self.context.update_token_expiry(self.context.current_tokens)
        saved = self.nemo_storage.read()
        if saved.get("metadata"):
            self.context.oauth_metadata = OAuthMetadata.model_validate(saved["metadata"])
            self.context.auth_server_url = saved["metadata"]["issuer"]
        if saved.get("resource_metadata"):
            self.context.protected_resource_metadata = ProtectedResourceMetadata.model_validate(saved["resource_metadata"])

    async def _perform_authorization(self):
        if not self.interactive:
            self.nemo_storage.write(tokens=None, expires_at=None)
            raise ToolFailure("MCP account needs authorization; connect it in MCP settings")
        if self.context.oauth_metadata:
            self.nemo_storage.write(metadata=self.context.oauth_metadata.model_dump(mode="json"),
                resource_metadata=self.context.protected_resource_metadata.model_dump(mode="json") if self.context.protected_resource_metadata else None)
        return await super()._perform_authorization()

    async def _exchange_token_authorization_code(self, *args, **kwargs):
        request = await super()._exchange_token_authorization_code(*args, **kwargs)
        # The SDK parses JSON; some issuers default to form-encoded responses.
        request.headers["Accept"] = "application/json"
        return request

    async def _refresh_token(self):
        request = await super()._refresh_token()
        request.headers["Accept"] = "application/json"
        return request

    async def _handle_token_response(self, response):
        await response.aread()
        try:
            data = response.json()
        except ValueError:
            raise ToolFailure("OAuth token exchange returned an unsupported response format") from None
        error = data.get("error") if isinstance(data, dict) else None
        messages = {
            "incorrect_client_credentials": "OAuth application credentials were rejected; check the client ID and client secret",
            "invalid_client": "OAuth application credentials were rejected; check the client ID and client secret",
            "redirect_uri_mismatch": "OAuth callback address does not match the registered application callback",
            "bad_verification_code": "OAuth authorization code was rejected or expired; connect the account again",
            "invalid_grant": "OAuth authorization code was rejected or expired; connect the account again",
        }
        if error or response.status_code != 200:
            message = messages.get(error) if isinstance(error, str) else None
            raise ToolFailure(message or "OAuth token exchange was rejected by the authorization server")
        try:
            await super()._handle_token_response(response)
        except Exception:
            raise ToolFailure("OAuth token exchange returned an invalid token response") from None

    async def _handle_refresh_response(self, response):
        old_refresh = self.context.current_tokens.refresh_token if self.context.current_tokens else None
        success = await super()._handle_refresh_response(response)
        if success and old_refresh and not self.context.current_tokens.refresh_token:
            self.context.current_tokens.refresh_token = old_refresh
            await self.nemo_storage.set_tokens(self.context.current_tokens)
        return success

    async def _auth_flow(self, request):
        # SDK locks only one provider. This lock coordinates all Runs and tests for the connection.
        async with self.nemo_lock:
            self._initialized = False
            if not self.interactive and not await self.nemo_storage.get_tokens():
                raise ToolFailure("MCP account needs authorization; connect it in MCP settings")
            async for outgoing in self._drive(request):
                response = yield outgoing
                self._response = response

    async def _drive(self, request):
        flow = super()._auth_flow(request)
        try:
            outgoing = await anext(flow)
            while True:
                validate_url(outgoing.url)
                yield outgoing
                outgoing = await flow.asend(self._response)
        except StopAsyncIteration:
            return
        finally:
            await flow.aclose()


class McpOAuthManager:
    def __init__(self, root=DEFAULT_OAUTH_ROOT, *, connection_factory=None, browser_opener=None):
        self.root = Path(root)
        self.connection_factory = connection_factory
        self.locks = {}
        self.attempts = {}
        self.browser_opener = browser_opener

    def storage(self, server_id, config, *, loader=None, sink=None):
        return OAuthStorage(self.root, server_id, config, secret_loader=loader, sink=sink)

    def provider(self, server_id, config, *, loader=None, sink=None, **kwargs):
        storage = self.storage(server_id, config, loader=loader, sink=sink)
        lock = self.locks.setdefault(identity(server_id, config), asyncio.Lock())
        return NemoOAuthProvider(config, storage, lock, **kwargs)

    def status(self, server_id, config):
        attempt = self.attempts.get(server_id)
        if attempt and attempt["key"] == identity(server_id, config) and attempt["status"] != "connected":
            return {k: v for k, v in attempt.items() if k not in {"task", "key"}}
        return {"status": self.storage(server_id, config).status() if config.auth_mode == "oauth" else "not_required"}

    async def start(self, server_id, config, workspace):
        if config.auth_mode != "oauth" or config.transport != "streamable_http":
            raise ConfigError("Select browser authorization for this HTTP connection first")
        if not Path(workspace).is_dir():
            raise ConfigError("workspace must be an existing directory")
        await self.cancel(server_id)
        attempt = {"key": identity(server_id, config), "status": "authorizing"}
        self.attempts[server_id] = attempt
        attempt["task"] = asyncio.create_task(self._authorize(server_id, config, Path(workspace), attempt))
        return self.status(server_id, config)

    async def _authorize(self, server_id, config, workspace, attempt):
        callback = BrowserCallback(config.oauth_redirect_port)
        connection = None
        try:
            async with asyncio.timeout(300):
                storage = self.storage(server_id, config)
                old_uri = storage.read().get("redirect_uri")
                if not callback.port and old_uri:
                    callback.port = urlsplit(old_uri).port or 0
                uri = await callback.start()
                storage.write(redirect_uri=uri)
                async def redirect(url):
                    validate_url(url)
                    params = parse_qs(urlsplit(url).query)
                    callback.state = params["state"][0]
                    callback.issuer = storage.read().get("metadata", {}).get("issuer")
                    attempt["authorization_url"] = url
                    if self.browser_opener:
                        try:
                            await asyncio.to_thread(self.browser_opener, url)
                        except Exception:
                            pass  # The UI offers the same URL if browser launch is unavailable.
                async def receive():
                    return await callback.future
                connection = self.connection_factory(server_id, config, workspace,
                    oauth_manager=self, oauth_interaction={"redirect": redirect, "callback": receive, "redirect_uri": uri})
                await connection.start()
                attempt["status"] = "connected" if storage.status() == "connected" else "not_required"
                attempt["tools"] = [{"name": t.remote_name, "model_name": t.name, "description": t.description,
                    "input_schema": t.input_schema, "read_only": t.read_only, "subagent_allowed": t.subagent_allowed} for t in connection.tools]
        except asyncio.CancelledError:
            attempt["status"] = "cancelled"
        except TimeoutError:
            attempt.update(status="failed", error="Authorization timed out. Try connecting again.")
        except (Exception, BaseExceptionGroup) as exc:
            attempt.update(status="failed", error=first_public_message(exc) or "Authorization failed. Check the service's OAuth registration requirements and callback configuration.")
        finally:
            attempt.pop("authorization_url", None)
            if connection:
                await connection.close()
            await callback.close()

    async def cancel(self, server_id):
        attempt = self.attempts.get(server_id)
        if attempt and not attempt["task"].done():
            attempt["task"].cancel()
            await attempt["task"]

    async def disconnect(self, server_id, config):
        await self.cancel(server_id)
        self.attempts.pop(server_id, None)
        if config.auth_mode != "oauth":
            # Connections without browser authorization must not create credential files.
            return
        self.storage(server_id, config).disconnect()

    async def shutdown(self):
        await asyncio.gather(*(self.cancel(key) for key in list(self.attempts)))
