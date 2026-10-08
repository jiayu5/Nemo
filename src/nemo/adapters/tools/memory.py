"""Model-facing, scoped memory tools. Persistence is an injected port."""
import asyncio
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from nemo.core.contracts.errors import ToolFailure
from nemo.core.contracts.tools import ExecutionContext
from nemo.core.memory import MemoryDraft, MemoryScope, MemoryStore
from nemo.redaction import redact


class MemoryQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(default="", max_length=200)
    scope: Literal["user", "project", "all"] = "all"
    offset: int = Field(default=0, ge=0, le=200)
    limit: int = Field(default=5, ge=1, le=10)


class MemoryRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: MemoryScope
    id: str = Field(pattern=r"^[a-f0-9]{32}$")


class MemoryWrite(MemoryDraft):
    scope: MemoryScope = "project"
    id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    revision: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class MemoryForget(MemoryRead):
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")


def bounded(data: object, context: ExecutionContext) -> str:
    text = redact(json.dumps(data, ensure_ascii=False))
    if len(text) > context.max_output_chars:
        raise ToolFailure("result exceeds output limit; reduce the requested range")
    return text


def terms(query: str) -> set[str]:
    words = set(re.findall(r"[a-z0-9_]+", query.lower()))
    for run in re.findall(r"[\u4e00-\u9fff]+", query):
        words.update(run[i:i+2] for i in range(max(1, len(run)-1)))
    return words


class MemoryTool:
    def __init__(self, store: MemoryStore):
        self.store = store

    def check(self):
        if not self.store.enabled():
            raise ToolFailure("memory is disabled; enable it in the memory manager")

    def summarize(self, arguments) -> str:
        return f"{self.name} ({arguments.scope})"


class SearchMemoryTool(MemoryTool):
    name = "search_memory"
    description = "Find saved user/project memories when prior preferences, corrections or decisions matter. Empty query lists entries. Does not search other projects. Read an entry for full content and current revision."
    arguments_type = MemoryQuery
    read_only = True

    async def execute(self, arguments: MemoryQuery, context: ExecutionContext):
        def search():
            self.check()
            scopes = ("user", "project") if arguments.scope == "all" else (arguments.scope,)
            entries = [entry for scope in scopes for entry in self.store.list(context.workspace, scope)]
            keywords = terms(arguments.query)
            ranked = [(sum(term in (e.title + " " + e.content).lower() for term in keywords), e)
                      for e in entries]
            ranked = [(score, e) for score, e in ranked if not keywords or score]
            ranked.sort(key=lambda pair: (-pair[0], pair[1].id))
            selected = ranked[arguments.offset:arguments.offset + arguments.limit]
            return bounded({"total": len(ranked), "entries": [
                {"id": e.id, "scope": e.scope, "title": e.title, "preview": e.content[:160]}
                for _, e in selected], "next_offset": arguments.offset + len(selected)
                if arguments.offset + len(selected) < len(ranked) else None}, context)
        try:
            return await asyncio.wait_for(asyncio.to_thread(search), context.default_timeout_seconds)
        except TimeoutError:
            raise ToolFailure("memory search timed out; retry shortly") from None


class ReadMemoryTool(MemoryTool):
    name = "read_memory"
    description = "Read a saved memory and its source session/message and current revision. Old memory can be stale; check current project files when facts matter."
    arguments_type = MemoryRead
    read_only = True

    async def execute(self, arguments: MemoryRead, context: ExecutionContext):
        def read():
            self.check()
            entry = next((e for e in self.store.list(context.workspace, arguments.scope) if e.id == arguments.id), None)
            if entry is None:
                raise ToolFailure("memory not found in this scope")
            return bounded(entry.model_dump(), context)
        try:
            return await asyncio.wait_for(asyncio.to_thread(read), context.default_timeout_seconds)
        except TimeoutError:
            raise ToolFailure("memory read timed out; retry shortly") from None


class SaveMemoryTool(MemoryTool):
    name = "save_memory"
    description = "Save durable user preferences, corrections or project background for future sessions. Avoid secrets, transient progress and facts already in AGENTS.md or project docs. To update, read first and supply id/revision. Scope project is current workspace; user is shared across projects."
    arguments_type = MemoryWrite
    read_only = False

    async def execute(self, arguments: MemoryWrite, context: ExecutionContext):
        def save():
            self.check()
            entry = self.store.save(context.workspace, arguments.scope,
                MemoryDraft(title=arguments.title, content=arguments.content, kind=arguments.kind),
                entry_id=arguments.id, revision=arguments.revision,
                source_session_id=context.session_id, source_message_index=context.source_message_index,
                source_run_id=context.run_id)
            return {"id": entry.id, "scope": entry.scope, "revision": entry.revision, "status": "saved"}
        return await asyncio.to_thread(save)


class ForgetMemoryTool(MemoryTool):
    name = "forget_memory"
    description = "Retire a saved memory from future recall. Read first and supply current revision. Original chat history and a local retired copy remain; this is not secure erasure."
    arguments_type = MemoryForget
    read_only = False

    async def execute(self, arguments: MemoryForget, context: ExecutionContext):
        def forget():
            self.check()
            self.store.forget(context.workspace, arguments.scope, arguments.id, arguments.revision)
            return {"status": "forgotten", "id": arguments.id}
        return await asyncio.to_thread(forget)
