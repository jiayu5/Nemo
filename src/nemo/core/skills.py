"""Skill catalog and manual-management contracts; I/O belongs to adapters."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol
import json


@dataclass(frozen=True)
class SkillEntry:
    id: str
    name: str
    description: str
    revision: str


class SkillCatalog(Protocol):
    workspace: Path
    entries: tuple[SkillEntry, ...]
    diagnostics: tuple[str, ...]

    def read(self, skill_id: str) -> dict: ...

    def read_resource(self, skill_id: str, path: str, offset: int, limit: int) -> dict: ...


def skill_directory(catalog: SkillCatalog) -> str:
    """Only stable discovery metadata enters the model's prefix."""
    if not catalog.entries and not catalog.diagnostics:
        return ""
    return json.dumps({
        "skills": [{"id": entry.id, "name": entry.name,
                    "description": entry.description} for entry in catalog.entries],
        "diagnostics": catalog.diagnostics,
    }, ensure_ascii=False, sort_keys=True)


class SkillManagementStore(Protocol):
    def list_installed(self, workspace: Path | None) -> dict: ...

    def detail(self, workspace: Path | None, skill_id: str) -> dict: ...

    def preview(self, workspace: Path | None, scope: Literal["user", "project"], content: str) -> dict: ...

    def install(self, workspace: Path | None, scope: Literal["user", "project"], content: str) -> dict: ...
