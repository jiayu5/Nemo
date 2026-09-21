"""Adapter for the OpenAI-compatible chat-completions protocol.

Covers any service that speaks ``POST {base_url}/chat/completions`` with
``Authorization: Bearer <key>``. Vendor names belong in configuration, never here.
"""

import json
from typing import Any

from pydantic import ValidationError

from nemo.core.contracts.errors import NemoError, ProviderError, ResponseFormatError
from nemo.core.contracts.model_config import ResolvedModel
from nemo.core.contracts.model_transport import EncodedRequest, HttpResponse
from nemo.core.contracts.secrets import SecretValue
from nemo.core.contracts.types import (
    Message,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    ToolCall,
)

_CHAT_PATH = "/chat/completions"
_MAX_ERROR_DETAIL = 300


class OpenAICompatibleAdapter:
    protocol = "openai_compatible"

    def encode_request(
        self, request: ModelRequest, resolved: ResolvedModel, secret: SecretValue
    ) -> EncodedRequest:
        body: dict[str, Any] = {
            "model": resolved.model_id,
            "messages": [_encode_message(message) for message in request.messages],
        }
        if request.tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
                for tool in request.tools
            ]
        # Caller-provided parameters come last so profiles can tune the request,
        # but the loader already rejected any attempt to override adapter keys.
        body.update(resolved.parameters)
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {secret.reveal()}",
            **resolved.headers,
        }
        return EncodedRequest(
            url=f"{resolved.base_url.rstrip('/')}{_CHAT_PATH}", headers=headers, body=body
        )

    def decode_response(self, response: HttpResponse, secret: SecretValue) -> ModelResponse:
        message = _first_message(response)
        content = message.get("content") or ""
        if not isinstance(content, str):
            raise ResponseFormatError("Provider returned non-text message content")
        raw_calls = message.get("tool_calls") or []
        if not isinstance(raw_calls, list):
            raise ResponseFormatError("Provider returned a malformed tool_calls field")
        calls = tuple(_decode_tool_call(raw) for raw in raw_calls)
        try:
            return ModelResponse(
                content=content, tool_calls=calls, usage=_decode_usage(response.body)
            )
        except ValidationError:
            raise ResponseFormatError("Provider returned duplicate tool call IDs") from None

    def decode_error(self, response: HttpResponse, secret: SecretValue) -> NemoError:
        detail = _error_detail(response.body, response.text) or f"HTTP {response.status}"
        return ProviderError(
            f"Provider returned HTTP {response.status}: {secret.redact(detail)}"
        )


def _encode_message(message: Message) -> dict[str, Any]:
    if message.role == "tool":
        result = message.tool_result
        payload: Any = (
            {"error": {"code": result.error.code, "message": result.error.message}}
            if result.error
            else result.output
        )
        return {
            "role": "tool",
            "tool_call_id": result.tool_call_id,
            "content": payload if isinstance(payload, str) else _dumps(payload),
        }
    if message.tool_calls:
        return {
            "role": "assistant",
            # Providers expect null, not an empty string, next to tool calls.
            "content": message.content or None,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": _dumps(call.arguments)},
                }
                for call in message.tool_calls
            ],
        }
    return {"role": message.role, "content": message.content}


def _decode_tool_call(raw: Any) -> ToolCall:
    if not isinstance(raw, dict) or not isinstance(raw.get("function"), dict):
        raise ResponseFormatError("Provider returned a malformed tool call")
    function = raw["function"]
    name = function.get("name")
    if not isinstance(name, str) or not name:
        raise ResponseFormatError("Provider returned a tool call without a name")
    arguments = function.get("arguments") or "{}"
    if not isinstance(arguments, str):
        raise ResponseFormatError(f"Tool call '{name}' returned non-text arguments")
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError:
        raise ResponseFormatError(f"Tool call '{name}' returned invalid JSON arguments") from None
    if not isinstance(parsed, dict):
        raise ResponseFormatError(f"Tool call '{name}' arguments must be a JSON object")
    call_id = raw.get("id")
    if not isinstance(call_id, str) or not call_id:
        raise ResponseFormatError(f"Tool call '{name}' is missing an id")
    return ToolCall(id=call_id, name=name, arguments=parsed)


def _first_message(response: HttpResponse) -> dict[str, Any]:
    body = response.body
    if not isinstance(body, dict):
        raise ResponseFormatError("Provider returned a non-JSON response body")
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ResponseFormatError("Provider returned no choices")
    first = choices[0]
    if not isinstance(first, dict) or not isinstance(first.get("message"), dict):
        raise ResponseFormatError("Provider returned a malformed first choice")
    return first["message"]


def _error_detail(body: Any, text: str = "") -> str:
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            detail = error.get("message") or error.get("type") or error.get("code")
            if detail:
                return str(detail)[:_MAX_ERROR_DETAIL]
        elif isinstance(error, str):
            return error[:_MAX_ERROR_DETAIL]
    # Not every provider answers errors with JSON; fall back to the raw excerpt.
    return text.strip()[:_MAX_ERROR_DETAIL]


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _decode_usage(body: Any) -> ModelUsage | None:
    """Read token accounting, tolerating whatever subset a provider reports.

    DeepSeek reports both ``prompt_cache_hit_tokens`` and the OpenAI-style
    ``prompt_tokens_details.cached_tokens``; OpenAI reports only the latter.
    Anything unreadable becomes ``None`` rather than a fake zero, so a consumer
    can tell "no usage reported" from "nothing was cached".
    """

    if not isinstance(body, dict):
        return None
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return None
    prompt = _as_int(usage.get("prompt_tokens"))
    completion = _as_int(usage.get("completion_tokens"))
    total = _as_int(usage.get("total_tokens")) or prompt + completion
    cached = _as_int(usage.get("prompt_cache_hit_tokens"))
    if cached == 0:
        details = usage.get("prompt_tokens_details")
        if isinstance(details, dict):
            cached = _as_int(details.get("cached_tokens"))
    if not (prompt or completion or total):
        return None
    return ModelUsage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
        # A provider must never be able to make the hit rate exceed 100%.
        cached_prompt_tokens=min(cached, prompt),
    )


def _as_int(value: Any) -> int:
    # bool is an int subclass; a stray true must not become "1 token".
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value
