"""Terminal rendering, with credentials removed.

Everything the CLI prints or stores goes through :func:`redact`. The rule is
crude on purpose: a secret that reaches a terminal scrollback or a log file
cannot be taken back, while a masked value costs a look at the source.

These patterns are a safety net, not a guarantee -- a key in an unusual shape
still gets through. The real rule is upstream: never print raw tool arguments.

The rule for a replacement is **keep the shape, mask the value**: ``Bearer
[redacted]``, ``api_key=[redacted]``, ``sk-[redacted]``. The reader still learns
what kind of thing was there, which is exactly the context the model needs on a
resumed session.
"""

from __future__ import annotations

from nemo.core.contracts.types import Event
from nemo.redaction import MASK, redact

__all__ = ["MASK", "redact", "render_event"]


def render_event(event: Event) -> str:
    payload = event.payload
    if event.type == "model.completed":
        line = (
            f"{event.seq:>3} model.completed tools={payload.get('tool_call_count', 0)} "
            f"prompt={_count(payload.get('prompt_tokens'))} "
            f"cached={_count(payload.get('cached_prompt_tokens'))} "
            f"completion={_count(payload.get('completion_tokens'))}"
        )
    elif event.type in ("tool.started", "tool.completed", "tool.failed"):
        detail = payload.get("summary") or payload.get("name") or "?"
        code = payload.get("error_code")
        line = f"{event.seq:>3} {event.type} {detail}" + (f"  [{code}]" if code else "")
    else:
        line = f"{event.seq:>3} {event.type}"
    return redact(line)


def _count(value: int | None) -> str:
    """``None`` means "the provider did not report it", which is not zero."""

    return "-" if value is None else str(value)
