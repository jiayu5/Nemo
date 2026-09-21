"""Loading the instruction files that go into the stable prefix.

Two files share one name and are told apart by *scope*:

- ``~/.nemo/AGENTS.md`` -- how the user wants this agent to behave everywhere.
- ``<workspace>/AGENTS.md`` -- what this particular project expects.

Both are optional and both are snapshotted once per session. Re-reading them
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
    """The workspace's own instructions, if it has any."""

    return load_instructions(Path(workspace) / filename)
