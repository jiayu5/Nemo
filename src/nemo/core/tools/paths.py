"""Workspace path resolution shared by every path-shaped argument."""

from pathlib import Path

from nemo.core.contracts.errors import ToolFailure

_MAX_SHOWN_PATH = 200


def resolve_in_workspace(workspace: Path, candidate: str) -> Path:
    """Resolve ``candidate`` under ``workspace``, refusing anything outside it.

    ``Path.resolve()`` follows symlinks and collapses ``..``, so a symlink that
    points outside the workspace is rejected too. The check is a boundary
    convention, not a security sandbox: see ``ExecutionContext``.
    """

    root = Path(workspace).resolve()
    raw = Path(candidate)
    target = raw if raw.is_absolute() else root / raw
    resolved = target.resolve()
    if resolved != root and root not in resolved.parents:
        raise ToolFailure(f"path escapes the workspace: {_shown(candidate)}")
    return resolved


def display_path(path: Path, workspace: Path) -> str:
    """Render a path relative to the workspace when possible."""

    try:
        relative = path.relative_to(Path(workspace).resolve())
    except ValueError:
        return str(path)
    return "." if str(relative) == "." else str(relative)


def _shown(candidate: str) -> str:
    # The model wrote this string, but keep the bound so a pathological argument
    # cannot inflate every error message (and the trace) without limit.
    return candidate if len(candidate) <= _MAX_SHOWN_PATH else candidate[:_MAX_SHOWN_PATH] + "..."
