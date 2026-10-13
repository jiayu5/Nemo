"""Connection settings and explicit discovery tests, without model calls."""
import asyncio
from pathlib import Path
from nemo.core.contracts.errors import ConfigError, first_public_message


class McpService:
    def __init__(self, editor, *, probe=None, oauth=None):
        self.editor, self.probe = editor, probe
        self._results = {}
        self._versions = {}
        self._testing = set()
        self._tasks = set()
        self.oauth = oauth

    async def shutdown(self):
        if self.oauth:
            await self.oauth.shutdown()
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def list(self):
        config = self.editor.load_optional()
        return {"servers": [{"id": key, **server.model_dump(mode="json"),
                             "authorization": self.oauth.status(key, server) if self.oauth else {"status": "not_required" if server.auth_mode == "none" else "unavailable"},
                             **(self._results.get(key, {"status": "not_tested", "tools": []}) if self._versions.get(key) == server.model_dump_json() else {"status": "not_tested", "tools": []})}
                            for key, server in (config.mcp_servers.items() if config else [])]}

    def save(self, server_id, config):
        self.editor.save_mcp(server_id, config)
        self._results.pop(server_id, None)
        self._versions.pop(server_id, None)
        return {"status": "saved"}

    def delete(self, server_id):
        self.editor.delete_mcp(server_id)
        self._results.pop(server_id, None)
        self._versions.pop(server_id, None)
        return {"status": "deleted"}

    def _oauth_config(self, server_id):
        config = self.editor.load_optional()
        if config is None or server_id not in config.mcp_servers:
            raise ConfigError("MCP connection not found")
        if self.oauth is None:
            raise ConfigError("MCP OAuth is unavailable")
        return config.mcp_servers[server_id]

    async def authorize(self, server_id, workspace):
        return await self.oauth.start(server_id, self._oauth_config(server_id), workspace)

    def authorization(self, server_id):
        return self.oauth.status(server_id, self._oauth_config(server_id))

    async def cancel_authorization(self, server_id):
        config = self._oauth_config(server_id)
        await self.oauth.cancel(server_id)
        return self.oauth.status(server_id, config)

    async def disconnect(self, server_id):
        config = self._oauth_config(server_id)
        await self.oauth.disconnect(server_id, config)
        # A disconnected account invalidates the previous discovery result.
        self._results.pop(server_id, None)
        self._versions.pop(server_id, None)
        return {"status": "not_connected"}

    async def test(self, server_id, workspace):
        config = self.editor.load_optional()
        if config is None or server_id not in config.mcp_servers:
            raise ConfigError("MCP connection not found")
        workspace = Path(workspace).expanduser().resolve()
        if not workspace.is_dir():
            raise ConfigError("workspace must be an existing directory")
        if self.probe is None:
            raise ConfigError("MCP connection testing is unavailable")
        if server_id in self._testing:
            raise ConfigError("MCP connection test is already running")
        self._testing.add(server_id)
        task = asyncio.current_task()
        self._tasks.add(task)
        self._versions[server_id] = config.mcp_servers[server_id].model_dump_json()
        self._results[server_id] = {"status": "connecting", "tools": []}
        try:
            tools = await self.probe(server_id, config.mcp_servers[server_id], workspace)
            result = {"status": "ok", "tools": tools}
        except asyncio.CancelledError:
            self._results[server_id] = {"status": "cancelled", "tools": []}
            raise
        except (Exception, BaseExceptionGroup) as exc:
            result = {"status": "failed", "tools": [], "error": first_public_message(exc)
                      or "MCP connection or discovery failed; check configuration and credentials"}
        finally:
            self._testing.discard(server_id)
            self._tasks.discard(task)
        self._results[server_id] = result
        return result
