"""Adapter for the OpenAI-compatible chat-completions protocol.

Covers any service that speaks ``POST {base_url}/chat/completions`` with
``Authorization: Bearer <key>``. Vendor names belong in configuration, never here.
"""

import json
from collections.abc import AsyncIterator
from typing import Any

from pydantic import ValidationError

from nemo.core.context.reminder import REMINDER_CLOSE, REMINDER_OPEN
from nemo.core.contracts.errors import NemoError, ProviderError, ResponseFormatError
from nemo.core.contracts.model_config import ResolvedModel
from nemo.core.contracts.model_transport import EncodedRequest, HttpResponse
from nemo.core.contracts.secrets import SecretValue
from nemo.core.contracts.types import (
    Message,
    ModelRequest,
    ModelResponse,
    ModelReasoningDelta,
    ModelTextDelta,
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
        last_user = max((index for index, item in enumerate(request.messages)
                         if item.role == "user" and not (
                             item.content.startswith(REMINDER_OPEN)
                             and item.content.endswith(REMINDER_CLOSE)
                         )), default=-1)
        body: dict[str, Any] = {
            "model": resolved.model_id,
            "messages": [_encode_message(message, include_reasoning=index > last_user)
                         for index, message in enumerate(request.messages)],
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
        reasoning = message.get("reasoning_content") or ""
        if not isinstance(reasoning, str):
            raise ResponseFormatError("Provider returned non-text reasoning content")
        raw_calls = message.get("tool_calls") or []
        if not isinstance(raw_calls, list):
            raise ResponseFormatError("Provider returned a malformed tool_calls field")
        calls = tuple(_decode_tool_call(raw) for raw in raw_calls)
        try:
            return ModelResponse(
                content=content, reasoning_content=reasoning,
                tool_calls=calls, usage=_decode_usage(response.body)
            )
        except ValidationError:
            raise ResponseFormatError("Provider returned duplicate tool call IDs") from None

    def decode_error(self, response: HttpResponse, secret: SecretValue) -> NemoError:
        detail = _error_detail(response.body, response.text) or f"HTTP {response.status}"
        return ProviderError(
            f"Provider returned HTTP {response.status}: {secret.redact(detail)}"
        )

    async def decode_stream(
        self, lines: AsyncIterator[str]
    ) -> AsyncIterator[ModelTextDelta | ModelReasoningDelta | ModelResponse]:
        """Assemble chat-completion SSE, validating calls only after all fragments."""

        content: list[str] = []
        reasoning: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        usage: ModelUsage | None = None
        saw_frame = False
        ended = False
        async for line in lines:
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                ended = True
                break
            if not data:
                continue
            try:
                frame = json.loads(data)
            except json.JSONDecodeError:
                raise ResponseFormatError("Provider returned invalid streaming JSON") from None
            if not isinstance(frame, dict):
                raise ResponseFormatError("Provider returned a malformed streaming frame")
            saw_frame = True
            frame_usage = _decode_usage(frame)
            if frame_usage is not None:
                usage = frame_usage
            choices = frame.get("choices")
            if not isinstance(choices, list):
                raise ResponseFormatError("Provider returned malformed streaming choices")
            if not choices:
                continue
            first = choices[0]
            if not isinstance(first, dict):
                raise ResponseFormatError("Provider returned a malformed streaming choice")
            delta = first.get("delta") or {}
            if not isinstance(delta, dict):
                raise ResponseFormatError("Provider returned a malformed streaming delta")
            fragment = delta.get("content")
            reasoning_fragment = delta.get("reasoning_content")
            if reasoning_fragment is not None:
                if not isinstance(reasoning_fragment, str):
                    raise ResponseFormatError("Provider returned non-text streaming reasoning")
                if reasoning_fragment:
                    reasoning.append(reasoning_fragment)
                    yield ModelReasoningDelta(text=reasoning_fragment)
            if fragment is not None:
                if not isinstance(fragment, str):
                    raise ResponseFormatError("Provider returned non-text streaming content")
                if fragment:
                    content.append(fragment)
                    yield ModelTextDelta(text=fragment)
            raw_calls = delta.get("tool_calls") or []
            if not isinstance(raw_calls, list):
                raise ResponseFormatError("Provider returned malformed streaming tool calls")
            for raw in raw_calls:
                if (not isinstance(raw, dict) or isinstance(raw.get("index"), bool)
                        or not isinstance(raw.get("index"), int)):
                    raise ResponseFormatError("Provider returned a tool call without an index")
                index = raw["index"]
                if index < 0:
                    raise ResponseFormatError("Provider returned a negative tool call index")
                call = calls.setdefault(index, {"id": "", "function": {"name": "", "arguments": ""}})
                call_id = raw.get("id")
                if call_id is not None:
                    if not isinstance(call_id, str):
                        raise ResponseFormatError("Provider returned malformed tool call id")
                    call["id"] += call_id
                function = raw.get("function") or {}
                if not isinstance(function, dict):
                    raise ResponseFormatError("Provider returned malformed tool call function")
                for key in ("name", "arguments"):
                    piece = function.get(key)
                    if piece is not None:
                        if not isinstance(piece, str):
                            raise ResponseFormatError("Provider returned non-text tool call fragment")
                        call["function"][key] += piece
        if not saw_frame or not ended:
            raise ResponseFormatError("Provider streaming response ended before [DONE]")
        decoded_calls = tuple(_decode_tool_call(calls[index]) for index in sorted(calls))
        try:
            yield ModelResponse(content="".join(content), reasoning_content="".join(reasoning),
                                tool_calls=decoded_calls, usage=usage)
        except ValidationError:
            raise ResponseFormatError("Provider returned duplicate tool call IDs") from None


def _encode_message(message: Message, *, include_reasoning: bool = True) -> dict[str, Any]:
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
        encoded = {
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
        if include_reasoning and message.reasoning_content:
            encoded["reasoning_content"] = message.reasoning_content
        return encoded
    encoded = {"role": message.role, "content": message.content}
    if include_reasoning and message.reasoning_content:
        encoded["reasoning_content"] = message.reasoning_content
    return encoded


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
