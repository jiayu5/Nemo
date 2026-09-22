"""Session transcripts: append-only JSONL, one conversation per file.

Written by the client rather than by ``core``, the same way config and prompt
loading are.

The file is a **redacted copy**. Credential-shaped text is masked before it is
written, so a resumed session replays what the log says rather than what the
model originally saw. ``load_transcript`` reports how many messages were
affected so the client can say that out loud instead of quietly changing
history.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from nemo.core.contracts.types import Message
from nemo.redaction import redact

HEADER_TYPE = "session"
MESSAGE_TYPE = "message"


@dataclass(frozen=True)
class SessionHeader:
    session_id: str
    workspace: str
    model: str
    approval_mode: str
    created_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": HEADER_TYPE,
            "session_id": self.session_id,
            "workspace": self.workspace,
            "model": self.model,
            "approval_mode": self.approval_mode,
            "created_at": self.created_at or datetime.now(timezone.utc).isoformat(),
        }


@dataclass(frozen=True)
class LoadedTranscript:
    header: SessionHeader | None
    messages: tuple[Message, ...]
    redacted: int
    #: Lines that could not be read: a torn write from a killed process, an
    #: unknown record type, or a payload that no longer validates.
    unreadable: int = 0


def _open_append(path: Path) -> Any:
    """Append with owner-only permissions; transcripts hold file contents."""

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    return os.fdopen(descriptor, "a", encoding="utf-8")


def write_header(path: Path, header: SessionHeader) -> None:
    """Write the header once, when the file is created."""

    if path.exists() and path.stat().st_size > 0:
        return
    with _open_append(path) as handle:
        handle.write(json.dumps(header.as_dict(), ensure_ascii=False) + "\n")


def append_messages(path: Path, messages: Iterable[Message]) -> int:
    """Append messages, redacted; return how many had something to hide."""

    redacted = 0
    lines: list[str] = []
    for message in messages:
        payload, changed = _redact_tree(message.model_dump(mode="json"))
        if changed:
            redacted += 1
        # The flag is how a later reader knows history was altered; it cannot be
        # recovered by re-scanning, because the secret is already gone.
        record = {"type": MESSAGE_TYPE, "message": payload}
        if changed:
            record["redacted"] = True
        lines.append(json.dumps(record, ensure_ascii=False))
    if lines:
        with _open_append(path) as handle:
            handle.write("\n".join(lines) + "\n")
            # One write per turn, flushed to the platter: a crash after this
            # point cannot leave the turn sitting in a buffer.
            handle.flush()
            os.fsync(handle.fileno())
    return redacted


def load_transcript(path: Path) -> LoadedTranscript:
    """Read what is readable.

    A transcript is appended to by a process that can be killed mid-write, so a
    single unreadable line -- torn JSON, an unknown record, a payload that no
    longer validates -- must not cost the whole session. Unreadable lines are
    counted and skipped; the caller decides how loudly to say so.
    """

    header: SessionHeader | None = None
    messages: list[Message] = []
    redacted = 0
    unreadable = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            kind = record.get("type")
            if kind == HEADER_TYPE:
                header = SessionHeader(
                    session_id=record.get("session_id", ""),
                    workspace=record.get("workspace", ""),
                    model=record.get("model", ""),
                    approval_mode=record.get("approval_mode", ""),
                    created_at=record.get("created_at", ""),
                )
            elif kind == MESSAGE_TYPE:
                messages.append(Message.model_validate(record["message"]))
                if record.get("redacted"):
                    redacted += 1
            else:
                unreadable += 1
        except (json.JSONDecodeError, KeyError, TypeError, ValidationError):
            unreadable += 1
    return LoadedTranscript(
        header=header, messages=tuple(messages), redacted=redacted, unreadable=unreadable
    )


def _redact_tree(value: Any) -> tuple[Any, bool]:
    """Mask credentials in every string of a JSON-shaped structure."""

    if isinstance(value, str):
        masked = redact(value)
        return masked, masked != value
    if isinstance(value, dict):
        changed = False
        result = {}
        for key, item in value.items():
            result[key], item_changed = _redact_tree(item)
            changed = changed or item_changed
        return result, changed
    if isinstance(value, list):
        changed = False
        result = []
        for item in value:
            masked, item_changed = _redact_tree(item)
            result.append(masked)
            changed = changed or item_changed
        return result, changed
    return value, False


def list_sessions(directory: Path) -> Sequence[Path]:
    if not directory.is_dir():
        return ()
    return tuple(sorted(directory.glob("*.jsonl"), reverse=True))
