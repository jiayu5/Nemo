"""Loading the instruction files that go into the stable prefix.

Instruction files share one name and are told apart by *scope*:

- ``~/.nemo/AGENTS.md`` -- how the user wants this agent to behave everywhere.
- Git-root-to-workspace ``AGENTS.md`` files -- what this project expects.

Both scopes are optional and snapshotted once per session. Re-reading them
every turn would make the stable prefix drift, throwing away the provider's
prompt cache and handing the model a different set of rules from one turn to
the next.

This is file I/O, so it stays outside ``core/`` -- the same boundary that keeps
``core/models`` from reading configuration. ``ContextBuilder`` receives text and
never touches the filesystem.
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_INSTRUCTIONS_FILENAME = "AGENTS.md"


def default_user_instructions_path(home: Path | None = None) -> Path:
    """``~/.nemo/AGENTS.md``.

    Resolved per call rather than at import time, so tests (and any future
    ``--home``-style override) can point somewhere else.
    """

    return (home or Path.home()) / ".nemo" / DEFAULT_INSTRUCTIONS_FILENAME


def load_instructions(path: Path | str) -> str | None:
    """Return the file's text, or ``None`` when there is nothing to inject.

    Missing, empty and unreadable all collapse to ``None``: an absent
    instruction file is normal, not an error, and injecting an empty heading
    would only add noise to the prefix.
    """

    path = Path(path)
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    return text or None


def load_user_instructions(path: Path | str | None = None) -> str | None:
    """The agent-wide instructions the user wrote for Nemo itself."""

    return load_instructions(path or default_user_instructions_path())


def load_project_instructions(
    workspace: Path | str,
    *,
    filename: str = DEFAULT_INSTRUCTIONS_FILENAME,
) -> str | None:
    """Snapshot root-to-workspace rules, stopping at the nearest Git root.

    A .git file also marks worktrees/submodules. Outside a repository, only the
    workspace itself applies. Instruction symlinks may not leave this root.
    """

    if Path(filename).name != filename:
        raise ValueError("instruction filename must be a single path component")
    workspace = Path(workspace).expanduser().resolve()
    chain = [workspace, *workspace.parents]
    root_index = next((i for i, folder in enumerate(chain)
                       if (folder / ".git").is_dir() or (folder / ".git").is_file()), None)
    folders = list(reversed(chain[:root_index + 1])) if root_index is not None else [workspace]
    root = folders[0]
    loaded = []
    seen = set()
    for folder in folders:
        path = folder / filename
        try:
            resolved = path.resolve()
            if not resolved.is_relative_to(root) or resolved in seen:
                continue
        except (OSError, RuntimeError):
            continue
        text = load_instructions(path)
        if text:
            seen.add(resolved)
            loaded.append((path.relative_to(root).as_posix(), text))
    if not loaded:
        return None
    if len(loaded) == 1 and folders[-1] / filename == root / loaded[0][0]:
        return loaded[0][1]
    return "\n\n".join(f"## {path}\n{text}" for path, text in loaded)
