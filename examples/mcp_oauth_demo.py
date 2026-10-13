"""A real local MCP server that requires OAuth browser sign-in.

Development only: the authorization endpoint approves immediately and every
token lives in memory, so the server must never be exposed to real users. It
exists to verify Nemo's MCP account authorization against the official SDK.
"""
import argparse
import secrets
import time

from pydantic import AnyHttpUrl

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.server.fastmcp import FastMCP
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from mcp.types import ToolAnnotations


class DevOAuthProvider(OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):
    """In-memory authorization server that approves every request immediately."""

    def __init__(self, resource_url):
        self.resource_url = resource_url
        self.clients = {}
        self.codes = {}
        self.access_tokens = {}
        self.refresh_tokens = {}

    async def get_client(self, client_id):
        return self.clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull):
        self.clients[client_info.client_id] = client_info

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        code = secrets.token_urlsafe(32)
        self.codes[code] = AuthorizationCode(
            code=code, scopes=params.scopes or [], expires_at=time.time() + 300,
            client_id=client.client_id, code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource)
        # Auto-approval: go straight back to the client's loopback callback.
        return construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)

    async def load_authorization_code(self, client, authorization_code: str):
        code = self.codes.get(authorization_code)
        return code if code and code.client_id == client.client_id else None

    async def exchange_authorization_code(self, client, authorization_code: AuthorizationCode) -> OAuthToken:
        self.codes.pop(authorization_code.code, None)
        return self._issue(client.client_id, authorization_code.scopes, authorization_code.resource)

    async def load_refresh_token(self, client, refresh_token: str):
        token = self.refresh_tokens.get(refresh_token)
        return token if token and token.client_id == client.client_id else None

    async def exchange_refresh_token(self, client, refresh_token: RefreshToken, scopes: list[str]) -> OAuthToken:
        self.refresh_tokens.pop(refresh_token.token, None)
        return self._issue(client.client_id, scopes or refresh_token.scopes, refresh_token.resource)

    async def load_access_token(self, token: str):
        access = self.access_tokens.get(token)
        if access and (access.expires_at is None or access.expires_at > time.time()):
            return access
        return None

    async def revoke_token(self, token):
        self.access_tokens.pop(token.token, None)
        self.refresh_tokens.pop(token.token, None)

    def _issue(self, client_id, scopes, resource) -> OAuthToken:
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self.access_tokens[access] = AccessToken(token=access, client_id=client_id, scopes=scopes,
            expires_at=int(time.time()) + 3600, resource=resource or self.resource_url)
        self.refresh_tokens[refresh] = RefreshToken(token=refresh, client_id=client_id, scopes=scopes,
            resource=resource or self.resource_url)
        return OAuthToken(access_token=access, token_type="Bearer", expires_in=3600,
                          refresh_token=refresh, scope=" ".join(scopes))


def create_server(port=8768):
    issuer = f"http://127.0.0.1:{port}"
    provider = DevOAuthProvider(f"{issuer}/mcp")
    server = FastMCP("Nemo OAuth demo", host="127.0.0.1", port=port, stateless_http=True,
                     json_response=True, auth_server_provider=provider,
                     auth=AuthSettings(
                         issuer_url=AnyHttpUrl(issuer), resource_server_url=AnyHttpUrl(f"{issuer}/mcp"),
                         required_scopes=["read"], validate_token_resource=True,
                         client_registration_options=ClientRegistrationOptions(
                             enabled=True, valid_scopes=["read"], default_scopes=["read"])))

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def add(a: int, b: int) -> int:
        """Add two integers without changing any files."""
        return a + b

    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8768)
    args = parser.parse_args()
    print(f"Add http://127.0.0.1:{args.port}/mcp with browser authorization in the Nemo MCP panel.")
    create_server(args.port).run(transport="streamable-http")


if __name__ == "__main__":
    main()
