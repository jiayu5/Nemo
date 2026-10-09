"""Read-only tools over an explicitly injected session skill catalog."""

import asyncio
import json

from pydantic import BaseModel, ConfigDict, Field

from nemo.core.contracts.errors import ToolFailure
from nemo.core.contracts.tools import ExecutionContext
from nemo.core.skills import SkillCatalog


class ListSkillsArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=10, ge=1, le=20)


class ReadSkillArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    skill_id: str = Field(min_length=1, max_length=80)


class ReadSkillBodyArguments(ReadSkillArguments):
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=4000, ge=1, le=8000)


class ReadSkillResourceArguments(ReadSkillArguments):
    path: str = Field(min_length=1, max_length=500)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=4000, ge=1, le=8000)


class _SkillTool:
    read_only = True

    def __init__(self, catalog: SkillCatalog):
        self.catalog = catalog

    async def execute(self, arguments: BaseModel, context: ExecutionContext) -> dict:
        if context.workspace.resolve() != self.catalog.workspace:
            raise ToolFailure("Skill catalog belongs to a different workspace")
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(self._read, arguments),
                timeout=context.default_timeout_seconds,
            )
        except TimeoutError:
            raise ToolFailure("Skill read timed out") from None
        if len(json.dumps(result, ensure_ascii=False)) > context.max_output_chars:
            raise ToolFailure("Skill output exceeds the context output limit; narrow the resource request")
        return result

    def summarize(self, arguments: BaseModel) -> str:
        return self.name + " " + getattr(arguments, "skill_id", "directory")


class ListSkillsTool(_SkillTool):
    name = "list_skills"
    description = "Inspect the frozen skill directory and discovery diagnostics; this does not rescan files."
    arguments_type = ListSkillsArguments

    def _read(self, arguments: ListSkillsArguments) -> dict:
        entries = self.catalog.entries[arguments.offset:arguments.offset + arguments.limit]
        return {"skills": [{"id": item.id, "name": item.name, "description": item.description}
                           for item in entries], "total": len(self.catalog.entries),
                "diagnostics": self.catalog.diagnostics[arguments.offset:arguments.offset + arguments.limit],
                "diagnostic_total": len(self.catalog.diagnostics)}


class ReadSkillTool(_SkillTool):
    name = "read_skill"
    description = "Load a skill workflow by directory ID. Continue with next_offset until the entire body is read before using it; offsets count characters."
    arguments_type = ReadSkillBodyArguments

    def _read(self, arguments: ReadSkillBodyArguments) -> dict:
        result = self.catalog.read(arguments.skill_id)
        body = result["body"]
        if arguments.offset > len(body):
            raise ToolFailure("Skill body offset is beyond the text")
        end = min(len(body), arguments.offset + arguments.limit)
        return {**result, "body": body[arguments.offset:end],
                "next_offset": end if end < len(body) else None}


class ReadSkillResourceTool(_SkillTool):
    name = "read_skill_resource"
    description = "Read a relative text resource listed by read_skill, only when needed; offsets count characters."
    arguments_type = ReadSkillResourceArguments

    def _read(self, arguments: ReadSkillResourceArguments) -> dict:
        return self.catalog.read_resource(arguments.skill_id, arguments.path, arguments.offset, arguments.limit)
