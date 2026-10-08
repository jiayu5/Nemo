"""On-demand, bounded workspace text search."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from nemo.adapters.workspace_retrieval import SENSITIVE_MARKERS, SKIP_DIRS, WorkspaceRetriever
from nemo.core.contracts.errors import ToolFailure
from nemo.core.contracts.tools import ExecutionContext
from nemo.core.tools.paths import resolve_in_workspace

MAX_PREVIEW_CHARS = 320
MAX_SEARCH_OUTPUT_CHARS = 3200


class SearchWorkspaceArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=2, max_length=200,
                       description="Keywords, code symbol, or exact phrase to find")
    path: str = Field(default=".", min_length=1,
                      description="Workspace-relative file or directory to search")
    max_results: int = Field(default=5, ge=1, le=10,
                             description="Maximum number of matching excerpts")


class SearchWorkspaceTool:
    name = "search_workspace"
    description = (
        "Search workspace text when the task needs to locate relevant content. "
        "Returns short excerpts with paths and line ranges; use read_file with "
        "offset and limit to inspect a result. Narrow path or query if needed."
    )
    arguments_type = SearchWorkspaceArguments
    read_only = True

    async def execute(self, arguments: SearchWorkspaceArguments,
                      context: ExecutionContext) -> str:
        scope = resolve_in_workspace(context.workspace, arguments.path)
        relative = scope.relative_to(context.workspace.resolve())
        if any(
            part.startswith(".") or part in SKIP_DIRS
            or any(marker in part.lower() for marker in SENSITIVE_MARKERS)
            for part in relative.parts
        ):
            raise ToolFailure("this path is not searchable")
        if not scope.exists():
            raise ToolFailure(f"path not found: {arguments.path}")
        if not (scope.is_file() or scope.is_dir()):
            raise ToolFailure(f"not a file or directory: {arguments.path}")
        try:
            matches = await WorkspaceRetriever(context.workspace).retrieve(
                arguments.query, scope=scope,
                timeout_seconds=context.default_timeout_seconds,
            )
        except TimeoutError:
            raise ToolFailure("workspace search timed out; narrow the path") from None
        if not matches:
            return "No matching workspace text found. Try a different query or path."
        max_chars = min(context.max_output_chars, MAX_SEARCH_OUTPUT_CHARS)
        lines: list[str] = []
        used = 0
        for match in matches[:arguments.max_results]:
            preview = match.text[:MAX_PREVIEW_CHARS]
            entry = f"[{match.path}: lines {match.start_line}-{match.end_line}]\n{preview}"
            if used + len(entry) + 2 > max_chars:
                break
            lines.append(entry)
            used += len(entry) + 2
        return "\n\n".join(lines) if lines else (
            "Matches were found, but the output limit is too small to show them."
        )

    def summarize(self, arguments: SearchWorkspaceArguments) -> str:
        return f"search workspace text in {arguments.path}"
