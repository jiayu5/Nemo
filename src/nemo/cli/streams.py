"""The CLI's only door to a terminal.

Input is blocking, so every read goes through ``asyncio.to_thread`` at the call
site. Keeping reads behind one object is what makes the approval flow and the
REPL testable without a real terminal.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Streams:
    #: Return the line, or ``None`` at end of input.
    read_line: Callable[[str], str | None]
    write: Callable[[str], None]
    is_tty: bool

    @classmethod
    def from_stdin(cls) -> "Streams":
        def read_line(prompt: str) -> str | None:
            try:
                return input(prompt)
            except EOFError:
                return None

        return cls(
            read_line=read_line,
            write=lambda text: print(text),
            is_tty=sys.stdin.isatty(),
        )

    @classmethod
    def scripted(
        cls, lines: list[str], *, written: list[str] | None = None, is_tty: bool = False
    ) -> "Streams":
        """A test double: answers from ``lines``, records everything written.

        Pass your own ``written`` list to inspect the output afterwards.
        """

        remaining = list(lines)
        sink = written if written is not None else []

        def read_line(prompt: str) -> str | None:
            sink.append(prompt)
            return remaining.pop(0) if remaining else None

        return cls(read_line=read_line, write=sink.append, is_tty=is_tty)
