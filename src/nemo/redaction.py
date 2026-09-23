"""Shared best-effort credential redaction for every persistence/output boundary."""

from __future__ import annotations

import re

MASK = "[redacted]"

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)(bearer)\s+\S{6,}"), rf"\1 {MASK}"),
    (
        re.compile(
            r"(?i)((?:api[_-]?key|access[_-]?token|auth[_-]?token|secret|password)"
            r"\s*[:=]\s*)\S{6,}"
        ),
        rf"\1{MASK}",
    ),
    (re.compile(r"sk-[A-Za-z0-9_\-]{6,}"), f"sk-{MASK}"),
)

_SENSITIVE_VALUE_PREFIX = re.compile(
    r"(?i)(?:bearer\s+|(?:api[_-]?key|access[_-]?token|auth[_-]?token|secret|password)"
    r"\s*[:=]\s*)$"
)


def redact(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class StreamingTextRedactor:
    """Release complete text tokens so credential patterns cannot straddle events."""

    def __init__(self) -> None:
        self._pending = ""
        self._context = ""

    def feed(self, text: str) -> str:
        result: list[str] = []
        for char in text:
            if char.isspace():
                result.append(self._release(self._pending + char))
                self._pending = ""
            elif not char.isascii() and not self._pending and not _SENSITIVE_VALUE_PREFIX.search(self._context):
                result.append(self._release(char))
            else:
                self._pending += char
        return "".join(result)

    def finish(self) -> str:
        result = self._release(self._pending)
        self._pending = ""
        return result

    def _release(self, segment: str) -> str:
        if not segment:
            return ""
        prefix = redact(self._context)
        safe = redact(self._context + segment)
        self._context = (self._context + segment)[-128:]
        return safe[len(prefix):]
