"""Filesystem tools: read, write, edit and list inside the workspace."""

import asyncio
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from nemo.core.contracts.errors import ToolFailure
from nemo.core.contracts.tools import ExecutionContext
from nemo.core.tools.limits import truncate_text
from nemo.core.tools.paths import display_path, resolve_in_workspace

_BINARY_PROBE_BYTES = 8192


class ReadFileArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, description="Path relative to the workspace")
    offset: int = Field(default=1, ge=1, description="First line to return, 1-based")
    limit: int = Field(default=400, ge=1, le=5000, description="Maximum number of lines")


class ReadFileTool:
    name = "read_file"
    description = (
        "Read a UTF-8 text file inside the workspace. Returns at most `limit` lines "
        "starting at `offset` (1-based), and reports the total line count so you can page."
    )
    arguments_type = ReadFileArguments
    read_only = True

    async def execute(self, arguments: ReadFileArguments, context: ExecutionContext) -> str:
        path = resolve_in_workspace(context.workspace, arguments.path)
        if not path.exists():
            raise ToolFailure(f"file not found: {arguments.path}")
        if not path.is_file():
            raise ToolFailure(f"not a file: {arguments.path}")
        content, total_lines = await asyncio.to_thread(
            _read_window, path, arguments.offset, arguments.limit
        )
        last = min(arguments.offset + arguments.limit - 1, total_lines)
        shown = display_path(path, context.workspace)
        header = f"[{shown}: lines {arguments.offset}-{last} of {total_lines}]"
        return f"{header}\n{truncate_text(content, context.max_output_chars)}"

    def summarize(self, arguments: ReadFileArguments) -> str:
        return f"read {arguments.path}"


class WriteFileArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, description="Path relative to the workspace")
    content: str = Field(description="Full file content")
    overwrite: bool = Field(
        default=False, description="Must be true to replace an existing file"
    )


class WriteFileTool:
    name = "write_file"
    description = (
        "Create a file with the given content. Refuses to replace an existing file "
        "unless `overwrite` is true; prefer edit_file for changing existing files."
    )
    arguments_type = WriteFileArguments
    read_only = False

    async def execute(self, arguments: WriteFileArguments, context: ExecutionContext) -> str:
        path = resolve_in_workspace(context.workspace, arguments.path)
        if path.exists() and not arguments.overwrite:
            raise ToolFailure(
                f"{arguments.path} already exists; set overwrite=true to replace it, "
                "or use edit_file to change it surgically"
            )
        if path.is_dir():
            raise ToolFailure(f"not a file: {arguments.path}")
        await asyncio.to_thread(_write_text, path, arguments.content)
        shown = display_path(path, context.workspace)
        return f"wrote {len(arguments.content)} characters to {shown}"

    def summarize(self, arguments: WriteFileArguments) -> str:
        action = "overwrite" if arguments.overwrite else "create"
        return f"{action} {arguments.path}"


class EditFileArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, description="Path relative to the workspace")
    old_string: str = Field(min_length=1, description="Exact text to replace")
    new_string: str = Field(description="Replacement text")
    replace_all: bool = Field(
        default=False, description="Replace every occurrence instead of exactly one"
    )


class EditFileTool:
    name = "edit_file"
    description = (
        "Replace an exact string in an existing file. The match must be unique unless "
        "`replace_all` is true, so include enough surrounding context to disambiguate."
    )
    arguments_type = EditFileArguments
    read_only = False

    async def execute(self, arguments: EditFileArguments, context: ExecutionContext) -> str:
        path = resolve_in_workspace(context.workspace, arguments.path)
        if not path.is_file():
            raise ToolFailure(f"file not found: {arguments.path}")
        text = await asyncio.to_thread(_read_text, path)
        occurrences = text.count(arguments.old_string)
        if occurrences == 0:
            raise ToolFailure(f"old_string was not found in {arguments.path}")
        if occurrences > 1 and not arguments.replace_all:
            raise ToolFailure(
                f"old_string appears {occurrences} times in {arguments.path}; "
                "include more context or set replace_all=true"
            )
        replaced = occurrences if arguments.replace_all else 1
        updated = (
            text.replace(arguments.old_string, arguments.new_string)
            if arguments.replace_all
            else text.replace(arguments.old_string, arguments.new_string, 1)
        )
        await asyncio.to_thread(_write_text, path, updated)
        shown = display_path(path, context.workspace)
        return f"replaced {replaced} occurrence(s) in {shown}"

    def summarize(self, arguments: EditFileArguments) -> str:
        return f"edit {arguments.path}"


class ListDirArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(default=".", min_length=1, description="Directory relative to the workspace")


class ListDirTool:
    name = "list_dir"
    description = (
        "List the entries of a directory inside the workspace. Directories are shown "
        "with a trailing slash; hidden entries are included."
    )
    arguments_type = ListDirArguments
    read_only = True

    async def execute(self, arguments: ListDirArguments, context: ExecutionContext) -> str:
        target = resolve_in_workspace(context.workspace, arguments.path)
        if not target.is_dir():
            raise ToolFailure(f"not a directory: {arguments.path}")
        entries = await asyncio.to_thread(_listing, target)
        shown = display_path(target, context.workspace)
        if not entries:
            return f"[{shown}] empty directory"
        body = truncate_text("\n".join(entries), context.max_output_chars)
        return f"[{shown}] {len(entries)} entries\n{body}"

    def summarize(self, arguments: ListDirArguments) -> str:
        return f"list {arguments.path}"


def _read_window(path: Path, offset: int, limit: int) -> tuple[str, int]:
    """Return the requested window plus the total number of lines."""

    with path.open("rb") as probe:
        if b"\x00" in probe.read(_BINARY_PROBE_BYTES):
            raise ToolFailure(f"{path.name} looks like a binary file")
    selected: list[str] = []
    total = 0
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        for number, line in enumerate(handle, start=1):
            total = number
            if offset <= number < offset + limit:
                selected.append(line)
    return "".join(selected).rstrip("\n"), total


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _listing(directory: Path) -> list[str]:
    entries = sorted(
        directory.iterdir(), key=lambda item: (item.is_file(), item.name.lower())
    )
    return [f"{entry.name}/" if entry.is_dir() else entry.name for entry in entries]
