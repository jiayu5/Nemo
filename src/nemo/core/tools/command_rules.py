"""Mechanical classification of shell command text, for approval decisions.

The classifier is conservative in exactly one direction: anything it cannot
*prove* read-only is never reported as read-only. What to do with
``UNCLASSIFIED`` is the caller's decision -- ``ask`` confirms it, ``auto`` runs
it (see :mod:`nemo.core.tools.approval`).

Two properties matter as much as the rules themselves:

1. **Reasons never quote the command back.** A reason travels into a
   ``ToolError`` and therefore reaches the model, and the command can carry a
   credential. Every reason names the *verb* or the *shape*, never an argument.
2. **This is not a sandbox.** It reads a string, so variables, aliases and
   interpreters hide intent; ``python -c ...`` can do anything and is
   deliberately left UNCLASSIFIED. The accepted limits are documented in
   ``docs/design/cli-and-approval.md``.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class CommandClass(StrEnum):
    READ_ONLY = "read_only"
    DESTRUCTIVE = "destructive"
    UNCLASSIFIED = "unclassified"


@dataclass(frozen=True)
class Classification:
    kind: CommandClass
    reason: str


#: Verbs that only read. Membership is a promise, so a verb with a file-writing
#: flag of its own (``sort -o``, ``sed -i``) must stay out of this set.
_READ_ONLY_VERBS = frozenset({
    "ls", "cat", "head", "tail", "wc", "pwd", "grep", "egrep", "fgrep", "rg",
    "file", "stat", "du", "df", "tree", "which", "whoami", "id", "date",
    "hostname", "uname", "printenv", "diff", "cmp", "uniq", "cut", "tr",
    "rev", "basename", "dirname", "realpath", "jq", "yq", "xxd", "od",
    "strings", "shasum", "md5sum", "sha1sum", "sha256sum", "ps", "uptime",
    "sw_vers", "less", "more", "bat", "column", "echo", "printf", "true",
    "false", "test",
})

#: Verbs that can destroy data, reach beyond this machine, or run something we
#: cannot see -- regardless of their arguments, so no path check can rescue them.
#: Shells are here unconditionally: invoking a shell inside a shell is the
#: definition of "the effect cannot be bounded from the text".
#:
#: Verbs whose risk is only about *scope* (``mv``, ``cp``, ``mkdir`` ...) are
#: deliberately absent: they fall through to the path check below, so moving a
#: file inside the workspace stays UNCLASSIFIED while moving one out of it is
#: DESTRUCTIVE. Deletion stays here because it is irreversible.
_DESTRUCTIVE_VERBS = frozenset({
    "rm", "rmdir", "unlink", "shred", "truncate", "dd", "mkfs", "fdisk",
    "diskutil", "chmod", "chown", "chgrp", "kill", "pkill", "killall",
    "shutdown", "reboot", "halt", "launchctl", "crontab", "tee", "install",
    "patch", "sudo", "su", "doas", "curl", "wget", "nc", "ncat", "ssh", "scp",
    "rsync", "telnet", "ftp", "xargs", "osascript", "open", "sh", "bash",
    "zsh", "ksh", "fish", "dash", "csh", "tcsh",
})

#: Verbs that only delegate, so the classification is whatever they run.
_WRAPPER_VERBS = frozenset({
    "env", "nice", "nohup", "timeout", "time", "watch", "command", "exec",
    "stdbuf", "ionice",
})

_READ_ONLY_GIT = frozenset({
    "status", "log", "diff", "show", "rev-parse", "ls-files", "blame",
    "describe", "remote", "shortlog", "whatchanged", "grep", "cat-file",
    "count-objects",
})

_DESTRUCTIVE_GIT = frozenset({
    "push", "reset", "clean", "rebase", "filter-branch", "gc", "prune",
    "submodule", "worktree", "update-ref",
})

_FIND_WRITE_FLAGS = frozenset({
    "-exec", "-execdir", "-delete", "-ok", "-okdir", "-fprint", "-fprint0", "-fls",
})

_COMMAND_SUBSTITUTION = re.compile(r"\$\(|`")
#: ``2>&1`` merges streams instead of writing a file, so a ``&`` after ``>``
#: neither counts as redirection nor ends a segment.
_REDIRECT = re.compile(r">{1,2}(?!&)")
_SEGMENT_SPLIT = re.compile(r"\|\||&&|[|;\n]|(?<![>])&")
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")

_MAX_WRAPPER_DEPTH = 4


def classify(command: str, *, workspace: Path | None = None) -> Classification:
    """Classify one command string. Never raises on malformed input."""

    text = command.strip()
    if not text:
        return Classification(CommandClass.UNCLASSIFIED, "the command is empty")
    if _COMMAND_SUBSTITUTION.search(text):
        return Classification(
            CommandClass.DESTRUCTIVE, "command substitution hides what will actually run"
        )

    # Structure is read from the text with quoted spans removed (a ">" inside
    # quotes is data, not a redirect); tokens come from the same stripped text.
    structure = _QUOTED.sub(" ", text)
    if _REDIRECT.search(structure):
        return Classification(
            CommandClass.DESTRUCTIVE,
            "output redirection writes to a path the arguments do not show",
        )

    verdicts = [
        _classify_tokens(tokens, workspace=workspace)
        for tokens in (_tokenize(segment) for segment in _SEGMENT_SPLIT.split(structure))
        if tokens
    ]
    if not verdicts:
        return Classification(CommandClass.UNCLASSIFIED, "the command is empty")
    for verdict in verdicts:
        if verdict.kind is CommandClass.DESTRUCTIVE:
            return verdict
    if all(verdict.kind is CommandClass.READ_ONLY for verdict in verdicts):
        return Classification(CommandClass.READ_ONLY, "only read-only commands")
    return next(
        verdict for verdict in verdicts if verdict.kind is CommandClass.UNCLASSIFIED
    )


def _tokenize(segment: str) -> list[str]:
    try:
        return shlex.split(segment)
    except ValueError:
        # Unbalanced quoting is not a reason to stop classifying; the verb and
        # the arguments are still visible.
        return segment.split()


def _classify_tokens(
    tokens: list[str], *, workspace: Path | None, depth: int = 0
) -> Classification:
    verb = tokens[0]
    if verb in _DESTRUCTIVE_VERBS:
        return Classification(
            CommandClass.DESTRUCTIVE,
            f"'{verb}' can destroy data or reach beyond this machine",
        )
    if verb in _WRAPPER_VERBS:
        if depth >= _MAX_WRAPPER_DEPTH:
            return Classification(
                CommandClass.DESTRUCTIVE, f"'{verb}' nests too deeply to identify"
            )
        inner = _inner_command(tokens[1:])
        if not inner:
            return Classification(
                CommandClass.DESTRUCTIVE,
                f"'{verb}' runs a command that could not be identified",
            )
        return _classify_tokens(inner, workspace=workspace, depth=depth + 1)
    if verb == "git":
        return _classify_git(tokens[1:])
    if verb == "find":
        if _FIND_WRITE_FLAGS.intersection(tokens[1:]):
            return Classification(
                CommandClass.DESTRUCTIVE, "'find' can delete or execute through a flag"
            )
        return Classification(CommandClass.READ_ONLY, "only read-only commands")
    if verb in _READ_ONLY_VERBS:
        return Classification(CommandClass.READ_ONLY, "only read-only commands")
    if _touches_outside(tokens[1:], workspace):
        return Classification(
            CommandClass.DESTRUCTIVE, "a path argument points outside the workspace"
        )
    return Classification(CommandClass.UNCLASSIFIED, f"'{verb}' is not on the read-only list")


def _classify_git(arguments: list[str]) -> Classification:
    subcommand = next((arg for arg in arguments if not arg.startswith("-")), None)
    if subcommand is None:
        return Classification(CommandClass.UNCLASSIFIED, "git was called without a subcommand")
    if subcommand in _DESTRUCTIVE_GIT:
        return Classification(
            CommandClass.DESTRUCTIVE, f"'git {subcommand}' changes history or the remote"
        )
    if subcommand in _READ_ONLY_GIT:
        return Classification(CommandClass.READ_ONLY, "only read-only commands")
    return Classification(
        CommandClass.UNCLASSIFIED, f"'git {subcommand}' is not on the read-only list"
    )


def _inner_command(arguments: list[str]) -> list[str]:
    """Skip a wrapper's own flags, ``NAME=value`` pairs and numeric limits."""

    for index, token in enumerate(arguments):
        if token.startswith("-") or token.isdigit() or "=" in token:
            continue
        return arguments[index:]
    return []


def _touches_outside(arguments: list[str], workspace: Path | None) -> bool:
    """True when an argument names a path outside ``workspace``.

    Relative paths stay inside by construction -- except ``..``, which is
    treated as outside rather than resolved, because the working directory is a
    separate argument and cannot be trusted here.

    With no workspace there is no boundary to be inside of, so any path-shaped
    argument counts as outside: the caller gets the conservative answer instead
    of a silent pass.
    """

    root = workspace.resolve() if workspace is not None else None
    for argument in arguments:
        token = argument
        if token.startswith("-"):
            _, separator, value = token.partition("=")
            if not separator:
                continue
            token = value
        if token.startswith("~"):
            expanded = Path(token).expanduser()
            if str(expanded).startswith("~"):
                return True
            candidate = expanded
        elif token.startswith("/"):
            candidate = Path(token)
        elif ".." in Path(token).parts:
            return True
        else:
            continue
        if root is None:
            return True
        resolved = candidate.resolve()
        if resolved != root and root not in resolved.parents:
            return True
    return False
