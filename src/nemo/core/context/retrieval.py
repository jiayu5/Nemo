"""Provider-neutral workspace search results."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RetrievedSnippet:
    path: str
    start_line: int
    end_line: int
    text: str
    score: float
