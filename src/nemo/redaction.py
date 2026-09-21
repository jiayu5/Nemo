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


def redact(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text
