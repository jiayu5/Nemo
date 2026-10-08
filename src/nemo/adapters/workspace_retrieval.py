"""Bounded, local lexical retrieval over workspace text files."""

from __future__ import annotations

import asyncio
import os
import re
import time
from collections import Counter
from pathlib import Path

from nemo.core.context.retrieval import RetrievedSnippet
from nemo.redaction import redact

MAX_FILES = 1500
MAX_SCAN_BYTES = 8_000_000
MAX_FILE_BYTES = 256_000
CHUNK_LINES = 36
CHUNK_CHARS = 1600
CHUNK_OVERLAP_LINES = 4
TEXT_SUFFIXES = frozenset({
    ".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".md", ".txt", ".toml",
    ".json", ".yaml", ".yml", ".css", ".html", ".sh", ".sql", ".xml",
    ".ini", ".cfg", ".go", ".swift", ".java", ".c", ".h", ".cpp",
})
SKIP_DIRS = frozenset({
    "node_modules", "dist", "build", "coverage", "__pycache__", ".git",
    ".venv", "venv", "target", "vendor",
})
SENSITIVE_MARKERS = ("secret", "credential", "token", "private_key")
STOP_WORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "does",
    "for", "from", "how", "i", "in", "is", "it", "me", "of", "on", "or",
    "please", "show", "the", "this", "to", "what", "where", "which", "with",
    "you", "your", "怎么", "什么", "一下", "我们", "这个", "那个", "请问", "看看",
})
_WORDS = re.compile(r"[a-zA-Z][a-zA-Z0-9_]*|[\u4e00-\u9fff]+")


class WorkspaceRetriever:
    def __init__(self, workspace: Path):
        self.workspace = workspace.expanduser().resolve()

    async def retrieve(self, query: str, scope: Path | None = None,
                       timeout_seconds: float | None = None) -> tuple[RetrievedSnippet, ...]:
        return await asyncio.to_thread(self._retrieve, query, scope, timeout_seconds)

    def _retrieve(self, query: str, scope: Path | None = None,
                  timeout_seconds: float | None = None) -> tuple[RetrievedSnippet, ...]:
        terms = _terms(query)
        if not terms:
            return ()
        deadline = time.monotonic() + timeout_seconds if timeout_seconds is not None else None
        ranked: list[RetrievedSnippet] = []
        scanned_files = 0
        scanned_bytes = 0
        for path in self._files(scope):
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("workspace search timed out")
            if scanned_files >= MAX_FILES or scanned_bytes >= MAX_SCAN_BYTES:
                break
            try:
                size = path.stat().st_size
                if size > MAX_FILE_BYTES or size == 0:
                    continue
                with path.open("rb") as handle:
                    raw = handle.read(min(size, MAX_FILE_BYTES) + 1)
                if b"\x00" in raw[:8192] or len(raw) > MAX_FILE_BYTES:
                    continue
                content = raw.decode("utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            scanned_files += 1
            scanned_bytes += len(raw)
            relative = path.relative_to(self.workspace).as_posix()
            for start, end, text in _chunks(content):
                if deadline is not None and time.monotonic() >= deadline:
                    raise TimeoutError("workspace search timed out")
                score = _score(terms, query, relative, text)
                if score > 0:
                    ranked.append(RetrievedSnippet(relative, start, end, redact(text), score))
        ranked.sort(key=lambda item: (-item.score, item.path, item.start_line))
        chosen: list[RetrievedSnippet] = []
        per_file: Counter[str] = Counter()
        for candidate in ranked:
            if len(chosen) >= 12:
                break
            if per_file[candidate.path] >= 2:
                continue
            if any(item.path == candidate.path and candidate.start_line <= item.end_line
                   and item.start_line <= candidate.end_line for item in chosen):
                continue
            chosen.append(candidate)
            per_file[candidate.path] += 1
        return tuple(chosen)

    def _files(self, scope: Path | None = None):
        root = (scope or self.workspace).resolve()
        if not root.is_relative_to(self.workspace):
            return
        if root.is_file():
            candidates = (root,)
        elif root.is_dir():
            candidates = self._walk_files(root)
        else:
            return
        for path in candidates:
            name = path.name
            if name.startswith(".") or name in {"package-lock.json", "requirements-lock.txt"}:
                continue
            if any(marker in name.lower() for marker in SENSITIVE_MARKERS):
                continue
            if path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            try:
                if not path.resolve().is_relative_to(self.workspace):
                    continue
            except OSError:
                continue
            yield path

    def _walk_files(self, root: Path):
        for directory, dirs, filenames in os.walk(root, followlinks=False):
            dirs[:] = sorted(name for name in dirs
                             if name not in SKIP_DIRS and not name.startswith(".")
                             and not any(marker in name.lower() for marker in SENSITIVE_MARKERS))
            for name in sorted(filenames):
                yield Path(directory) / name


def _terms(text: str) -> set[str]:
    found: set[str] = set()
    for match in _WORDS.findall(text.lower()):
        if match in STOP_WORDS:
            continue
        if re.fullmatch(r"[\u4e00-\u9fff]+", match):
            if len(match) == 1:
                continue
            found.update(match[index:index + 2] for index in range(len(match) - 1))
        else:
            found.add(match)
            found.update(part for part in match.split("_") if len(part) > 1)
    return found


def _chunks(text: str):
    lines = text.splitlines()
    start = 0
    while start < len(lines):
        end = min(start + CHUNK_LINES, len(lines))
        while end > start + 1 and len("\n".join(lines[start:end])) > CHUNK_CHARS:
            end -= 1
        body = "\n".join(lines[start:end])
        if len(body) > CHUNK_CHARS:
            body = body[:CHUNK_CHARS] + "\n[long line truncated]"
        yield start + 1, end, body
        if end == len(lines):
            break
        start = max(start + 1, end - CHUNK_OVERLAP_LINES)


def _score(terms: set[str], query: str, path: str, text: str) -> float:
    content_terms = _terms(text)
    path_terms = _terms(path)
    matched = terms & content_terms
    path_matched = terms & path_terms
    if not matched and not path_matched:
        return 0.0
    score = float(len(matched) + 2 * len(path_matched))
    if len(terms) > 1:
        score += 2 * len(matched | path_matched) / len(terms)
    normalized_query = query.strip().lower()
    if len(normalized_query) >= 5 and normalized_query in text.lower():
        score += 3
    return score
