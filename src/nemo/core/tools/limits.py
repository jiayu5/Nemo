"""Output bounding for tool results."""

from typing import Literal

Keep = Literal["head", "tail", "middle"]


def truncate_text(text: str, limit: int, *, keep: Keep = "head") -> str:
    """Bound ``text`` to roughly ``limit`` characters, marking what was cut.

    The marker is always appended, even when it pushes the result slightly over
    ``limit``: a silently shortened tool result is worse than a slightly long one,
    because the model would reason over a truncated world without knowing it.
    """

    if limit <= 0 or len(text) <= limit:
        return text
    total = len(text)
    if keep == "tail":
        kept = text[-limit:]
        return f"[truncated: showing the last {limit} of {total} characters]\n{kept}"
    if keep == "middle":
        head = limit // 2
        tail = limit - head
        omitted = total - limit
        return (
            f"{text[:head]}\n\n[truncated: {omitted} characters omitted from the middle]\n\n"
            f"{text[-tail:]}"
        )
    kept = text[:limit]
    return f"{kept}\n\n[truncated: showing the first {limit} of {total} characters]"
