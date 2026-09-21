"""The one place that asks a human for approval."""

from __future__ import annotations

import asyncio

from nemo.cli.render import redact
from nemo.cli.streams import Streams
from nemo.core.tools.approval import ApprovalOutcome, ApprovalRequest

PROMPT = "  allow? [y] once / [a] this session / [n] deny > "


def parse_answer(text: str | None) -> ApprovalOutcome:
    """Anything that is not a clear yes is a no."""

    if text is None:
        return ApprovalOutcome.DENY
    head = text.strip()[:1].lower()
    if head == "y":
        return ApprovalOutcome.ALLOW_ONCE
    if head == "a":
        return ApprovalOutcome.ALLOW_SESSION
    return ApprovalOutcome.DENY


class TerminalApprover:
    def __init__(self, streams: Streams):
        self._streams = streams

    async def __call__(self, request: ApprovalRequest) -> ApprovalOutcome:
        if not self._streams.is_tty:
            # Nobody is present to answer. Saying no is the only honest answer,
            # and it keeps a piped-in script from silently bypassing approval.
            return ApprovalOutcome.DENY
        self._streams.write("")
        self._streams.write(f"  approval needed: {redact(request.summary) or request.tool_name}")
        self._streams.write(f"  why: {redact(request.reason)}")
        # Blocking input must leave the event loop free: otherwise cancellation
        # and timeouts stop working for as long as the user thinks.
        answer = await asyncio.to_thread(self._streams.read_line, PROMPT)
        return parse_answer(answer)
