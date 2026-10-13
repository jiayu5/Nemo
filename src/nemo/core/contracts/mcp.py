"""Validated user configuration for external tool connections."""
from urllib.parse import urlsplit
from pydantic import Field, model_validator
from typing import Annotated
from pydantic import StringConstraints
EnvName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]
NonEmptyStr = Annotated[str, StringConstraints(min_length=1)]
from nemo.core.contracts.types import Contract
from typing import Literal


class McpServerConfig(Contract):
    transport: Literal["stdio", "streamable_http"] = "stdio"
    enabled: bool = False
    command: str | None = None
    args: list[str] = Field(default_factory=list, max_length=64)
    url: str | None = None
    proxy_env: EnvName | None = None
    env: dict[EnvName, EnvName] = Field(default_factory=dict)
    headers: dict[str, EnvName] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=30, gt=0, le=300)
    read_only_tools: list[NonEmptyStr] = Field(default_factory=list, max_length=64)
    subagent_tools: list[NonEmptyStr] = Field(default_factory=list, max_length=64)
    auth_mode: Literal["none", "oauth"] = "none"
    oauth_client_id: str | None = None
    oauth_client_secret_env: EnvName | None = None
    oauth_issuer: str | None = None
    oauth_redirect_port: int = Field(default=0, ge=0, le=65535)
    oauth_client_metadata_url: str | None = None

    @model_validator(mode="after")
    def validate_connection(self):
        if self.auth_mode == "oauth":
            if self.transport != "streamable_http" or any(k.lower() == "authorization" for k in self.headers):
                raise ValueError("OAuth requires HTTP and forbids an Authorization header")
            if bool(self.oauth_client_id) != bool(self.oauth_issuer):
                raise ValueError("Pre-registered OAuth requires both client ID and issuer")
            if self.oauth_client_secret_env and not self.oauth_client_id:
                raise ValueError("OAuth client secret requires a client ID")
            for address in (self.url, self.oauth_issuer, self.oauth_client_metadata_url):
                if not address:
                    continue
                parsed = urlsplit(address)
                if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}):
                    raise ValueError("OAuth requires HTTPS except for loopback development")
            if self.oauth_client_metadata_url:
                parsed = urlsplit(self.oauth_client_metadata_url)
                if parsed.scheme != "https" or parsed.path in {"", "/"}:
                    raise ValueError("Client metadata URL requires HTTPS and a document path")
        if self.transport == "stdio":
            if not self.command or not self.command.strip() or self.url or self.headers or self.proxy_env:
                raise ValueError("stdio requires command and forbids url/headers")
        else:
            parsed = urlsplit(self.url or "")
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("HTTP requires an http(s) URL without credentials, query or fragment")
            if self.command or self.args or self.env:
                raise ValueError("HTTP forbids command/args/env")
        if not set(self.subagent_tools).issubset(self.read_only_tools):
            raise ValueError("subagent_tools must be explicitly listed in read_only_tools")
        for name in self.headers:
            if not name or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-" for c in name):
                raise ValueError("invalid HTTP header name")
        return self
