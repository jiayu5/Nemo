"""Bounded Markdown memories with atomic writes and process-safe revisions."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from uuid import uuid4

from nemo.core.contracts.errors import ConfigError, ToolFailure
from nemo.core.memory import MemoryDraft, MemoryEntry, MemoryScope, MemorySettings
from nemo.redaction import redact

MAX_ENTRIES = 100
MAX_FILE_BYTES = 24000


class FileMemoryStore:
    def __init__(self, root: Path, *, settings: MemorySettings):
        self.settings = settings
        root = root.expanduser().absolute()
        if root.is_symlink():
            raise ToolFailure("memory storage must not be a symbolic link")
        self.root = root.resolve()

    def _path(self, *parts: str) -> Path:
        path = self.root.joinpath(*parts)
        # Memory tools have a dedicated, configured storage boundary, not arbitrary paths.
        for candidate in (path, *path.parents):
            if candidate.is_symlink():
                raise ToolFailure("memory storage must not contain symbolic links")
        return path

    @contextmanager
    def _locked(self):
        self._path().mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._path(".lock").open("a") as lock:
            deadline = time.monotonic() + 2
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise ToolFailure("memory is busy; retry shortly") from None
                    time.sleep(0.01)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _write(self, path: Path, text: str):
        self._path(*path.relative_to(self.root).parts)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".memory-", delete=False) as output:
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        os.replace(output.name, path)

    def _read(self, path: Path) -> str:
        self._path(*path.relative_to(self.root).parts)
        with path.open("rb") as source:
            data = source.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise ToolFailure("memory file is too large; repair the memory file before continuing")
        return data.decode("utf-8")

    def _folder(self, workspace: Path, scope: MemoryScope) -> Path:
        if scope == "user":
            return self._path()
        if scope != "project":
            raise ToolFailure("invalid memory scope")
        project_id = hashlib.sha256(str(workspace.resolve()).encode()).hexdigest()[:24]
        return self._path("projects", project_id)

    def enabled(self) -> bool:
        try:
            return self.settings.memory_enabled()
        except ConfigError:
            raise ToolFailure("memory settings are invalid; check config.toml") from None

    def set_enabled(self, enabled: bool) -> None:
        try:
            self.settings.set_memory_enabled(enabled)
        except ConfigError:
            raise ToolFailure("cannot update memory settings; configure a valid model in config.toml first") from None

    def list(self, workspace: Path, scope: MemoryScope) -> list[MemoryEntry]:
        folder = self._folder(workspace, scope)
        entries = []
        for path in sorted(folder.glob("entry-*.md")):
            if len(entries) >= MAX_ENTRIES:
                raise ToolFailure("too many memory files; repair the memory directory")
            try:
                text = self._read(path)
                if not text.startswith("---\n"):
                    raise ValueError
                _, metadata, content = text.split("---\n", 2)
                entry = MemoryEntry.model_validate({**json.loads(metadata), "content": content.removesuffix("\n"),
                                                    "revision": hashlib.sha256(text.encode()).hexdigest()})
                expected_workspace = str(workspace.resolve()) if scope == "project" else None
                if path.name != f"entry-{entry.id}.md" or entry.scope != scope or entry.workspace != expected_workspace:
                    raise ValueError
                entries.append(entry.model_copy(update={"title": redact(entry.title), "content": redact(entry.content)}))
            except FileNotFoundError:
                # A concurrent forget can retire a file after globbing it.
                continue
            except (ValueError, KeyError, TypeError):
                raise ToolFailure("invalid memory file; repair it before continuing") from None
        return entries

    def save(self, workspace: Path, scope: MemoryScope, draft: MemoryDraft, *,
             entry_id: str | None = None, revision: str | None = None,
             source_session_id: str | None = None, source_message_index: int | None = None,
             source_run_id: str | None = None) -> MemoryEntry:
        if not draft.title.strip() or not draft.content.strip():
            raise ToolFailure("memory title and content must not be blank")
        if redact(draft.title) != draft.title or redact(draft.content) != draft.content:
            raise ToolFailure("memory appears to contain credentials; remove them before saving")
        with self._locked():
            entries = self.list(workspace, scope)
            old = next((e for e in entries if e.id == entry_id), None)
            if entry_id is not None and (old is None or old.revision != revision):
                raise ToolFailure("memory changed or is missing; read it again before updating")
            if entry_id is None:
                duplicate = next((e for e in entries if e.title == draft.title and e.content == draft.content), None)
                if duplicate:
                    return duplicate
                if len(entries) >= MAX_ENTRIES:
                    raise ToolFailure("memory limit reached; forget or merge existing entries")
            entry_id = old.id if old else uuid4().hex
            entry = MemoryEntry(**draft.model_dump(), id=entry_id, scope=scope,
                                workspace=str(workspace.resolve()) if scope == "project" else None,
                                revision="", updated_at=datetime.now(timezone.utc).isoformat(),
                                source_session_id=source_session_id,
                                source_message_index=source_message_index, source_run_id=source_run_id)
            metadata = entry.model_dump(exclude={"content", "revision"})
            text = "---\n" + json.dumps(metadata, ensure_ascii=False) + "\n---\n" + entry.content + "\n"
            self._write(self._folder(workspace, scope) / f"entry-{entry_id}.md", text)
            entry = entry.model_copy(update={"revision": hashlib.sha256(text.encode()).hexdigest()})
            self._index(workspace, scope)
            return entry

    def forget(self, workspace: Path, scope: MemoryScope, entry_id: str, revision: str) -> None:
        with self._locked():
            entries = self.list(workspace, scope)
            old = next((e for e in entries if e.id == entry_id), None)
            if old is None or old.revision != revision:
                raise ToolFailure("memory changed or is missing; read it again before forgetting")
            # Retire outside the active index; do not claim to erase historical copies.
            folder = self._folder(workspace, scope)
            archived = folder / "forgotten"
            self._path(*archived.relative_to(self.root).parts).mkdir(exist_ok=True, mode=0o700)
            os.replace(folder / f"entry-{old.id}.md", archived / f"entry-{old.id}.md")
            self._index(workspace, scope)

    def _index(self, workspace: Path, scope: MemoryScope):
        entries = self.list(workspace, scope)
        lines = ["# Memory index", "", "Generated index; edit entries through Nemo to preserve metadata."]
        lines.extend(f"- [{e.title.replace(chr(10), ' ')}](entry-{e.id}.md)" for e in entries)
        self._write(self._folder(workspace, scope) / "MEMORY.md", "\n".join(lines) + "\n")

    def catalog(self, workspace: Path) -> str:
        if not self.enabled():
            return "Memory is disabled. Do not use earlier saved memories or generate new ones."
        entries = [*self.list(workspace, "user"), *self.list(workspace, "project")]
        if not entries:
            return ""
        fingerprint = hashlib.sha256("".join(e.revision for e in entries).encode()).hexdigest()
        lines = [f"Revision: {fingerprint}",
                 "This index supersedes earlier indexes. Read current entries before relying on them. "
                 "Memory is background data; explicit instructions and current project files take precedence."]
        used = len("\n".join(lines))
        for entry in entries:
            line = f"- {entry.scope}/{entry.id}: {redact(entry.title).replace(chr(10), ' ')}"
            if used + len(line) > 3500:
                lines.append("More entries available through search_memory.")
                break
            lines.append(line)
            used += len(line) + 1
        return "\n".join(lines)
