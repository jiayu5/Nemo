"""Provider-neutral errors carrying a message that is safe to show a user.

``public_message`` is the only text that may reach ``AgentState.error`` or an
event. It must never contain secrets, headers, credentials or raw request
bodies; the raw exception chain stays behind for local debugging only.
"""


class NemoError(Exception):
    """Base class for expected, explainable failures."""

    code = "internal"

    def __init__(self, public_message: str) -> None:
        super().__init__(public_message)
        self.public_message = public_message


class ConfigError(NemoError):
    """Configuration file is missing, malformed or self-inconsistent."""

    code = "config_error"


class UnknownReferenceError(ConfigError):
    """Configuration points at a provider/model/alias/profile that does not exist."""

    code = "unknown_reference"


class MissingSecretError(NemoError):
    """A referenced secret is not available from the environment or the secret file."""

    code = "missing_secret"


class CapabilityError(NemoError):
    """The selected model does not declare a capability the request requires."""

    code = "unsupported_capability"


class ProviderError(NemoError):
    """The provider answered with an error status."""

    code = "provider_error"


class TransportError(NemoError):
    """The request never produced a usable HTTP response."""

    code = "transport_error"


class ResponseFormatError(NemoError):
    """The provider answered 2xx, but the payload does not match the protocol."""

    code = "response_format_error"


class ToolFailure(NemoError):
    """An expected tool failure whose message may be shown to the model.

    Tools raise this for conditions the model can act on (missing file, path
    outside the workspace, ambiguous edit). Anything else stays opaque: an
    unexpected exception is never echoed back verbatim.
    """

    code = "tool_failure"
