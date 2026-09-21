"""Nemo's command line client.

One process, one session, one turn at a time. Everything the CLI does with the
world lives in this package (arguments, terminal, transcript, redaction); the
runtime it drives stays unaware that a human exists.

    python -m nemo.cli "在 workspace 里建一个 hello.txt"
    python -m nemo.cli --mode auto            # interactive
    python -m nemo.cli --session 20260918-141530-a1b2c3
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from nemo.bootstrap import build_local_tools, build_model_client
from nemo.cli.approver import TerminalApprover
from nemo.cli.render import redact, render_event
from nemo.cli.streams import Streams
from nemo.cli.transcript import (
    SessionHeader,
    append_messages,
    list_sessions,
    load_transcript,
    write_header,
)
from nemo.config.secrets import SecretLoader
from nemo.core.context.builder import DEFAULT_MAX_INSTRUCTIONS_CHARS, ContextBuilder
from nemo.core.contracts.errors import NemoError
from nemo.core.contracts.tools import ExecutionContext
from nemo.core.runtime.agent import AgentRuntime
from nemo.core.session import Session
from nemo.core.tools.approval import ApprovalMode, ApprovalPolicy
from nemo.core.tools.registry import ToolRegistry
from nemo.prompts.local_agent import build_system_prompt
from nemo.prompts.instructions import load_project_instructions, load_user_instructions

MODE_LABELS = {
    ApprovalMode.ASK: "Codex: Read Only",
    ApprovalMode.AUTO: "Codex: Auto",
    ApprovalMode.FULL: "Codex: Full Access",
}

HELP = """commands:
  :help            show this
  :mode            show the approval mode
  :mode ask|auto|full
  :exit, :quit     leave (the transcript stays on disk)"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m nemo.cli", description="Nemo command line client")
    parser.add_argument("prompt", nargs="*", help="run one turn and exit; omit for interactive")
    parser.add_argument("--workspace", default=".", help="Directory the tools are confined to")
    parser.add_argument("--model", default=None, help="profile / alias / model name override")
    parser.add_argument(
        "--mode",
        default=ApprovalMode.ASK.value,
        choices=[mode.value for mode in ApprovalMode],
        help="ask (default): confirm every change; auto: confirm only recognised danger; full: never confirm",
    )
    parser.add_argument("--max-steps", type=int, default=10, help="model calls allowed in one turn")
    parser.add_argument("--session", default=None, help="resume the session with this id")
    parser.add_argument("--sessions", action="store_true", help="list known sessions and exit")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    client_factory: Callable[..., object] = build_model_client,
    streams: Streams | None = None,
    session_dir: Path | str | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    output = streams or Streams.from_stdin()
    directory = Path(session_dir or Path.home() / ".nemo" / "sessions").expanduser()
    if args.sessions:
        for path in list_sessions(directory):
            output.write(path.stem)
        return 0
    try:
        return asyncio.run(_serve(args, output, directory, client_factory))
    except KeyboardInterrupt:
        output.write("")
        return 130
    except NemoError as exc:
        output.write(f"{type(exc).__name__}: {exc.public_message}")
        return 2


async def _serve(args, output: Streams, directory: Path, client_factory) -> int:
    workspace = Path(args.workspace).expanduser().resolve()
    if not workspace.is_dir():
        output.write(f"workspace is not a directory: {workspace}")
        return 2

    session, path, resumed = _open_session(args, workspace, directory, output)
    if session is None:
        return 2

    client = client_factory(run_override=args.model)
    resolved = client.resolved
    mode = ApprovalMode(args.mode)
    policy = ApprovalPolicy(mode, approver=TerminalApprover(output))
    tools = build_local_tools()
    user_instructions = load_user_instructions()
    project_instructions = load_project_instructions(workspace)
    registry = ToolRegistry(
        tools, context=ExecutionContext(workspace=workspace), policy=policy
    )
    runtime = AgentRuntime(
        client,
        registry,
        ContextBuilder(
            system_prompt=build_system_prompt(
                workspace=workspace, tools=(tool.name for tool in tools)
            ),
            user_instructions=user_instructions,
            project_instructions=project_instructions,
        ),
    )

    write_header(
        path,
        SessionHeader(
            session_id=session.session_id,
            workspace=str(workspace),
            model=f"{resolved.model_name} -> {resolved.model_id}",
            approval_mode=mode.value,
        ),
    )
    output.write(f"session  : {session.session_id}"
                 + (f" (resumed, {len(session.messages)} messages)" if resumed else " (new)"))
    output.write(f"model    : {resolved.model_name} -> {resolved.model_id}")
    output.write(f"workspace: {workspace}")
    output.write(f"mode     : {mode.value} ({MODE_LABELS[mode]})")
    output.write(f"key      : {resolved.api_key_env} "
                 f"(from {SecretLoader().source(resolved.api_key_env)})")
    output.write(_describe_instructions(user_instructions, project_instructions))
    output.write(f"transcript: {path}")

    prompt = " ".join(args.prompt).strip()
    if prompt:
        return _exit_code(await _turn(runtime, session, path, prompt, args, output))

    output.write("type :help for commands, :exit to leave")
    while True:
        line = await asyncio.to_thread(output.read_line, "nemo> ")
        if line is None:
            output.write("")
            return 0
        line = line.strip()
        if not line:
            continue
        if line in (":exit", ":quit"):
            return 0
        if line == ":help":
            output.write(HELP)
            continue
        if line.startswith(":mode"):
            _switch_mode(policy, line, output)
            continue
        if line.startswith(":"):
            output.write(f"unknown command: {line}")
            continue
        # A failed or cancelled turn ends the turn, not the session.
        _exit_code(await _turn(runtime, session, path, line, args, output))


def _open_session(args, workspace: Path, directory: Path, output: Streams):
    if args.session:
        path = directory / f"{args.session}.jsonl"
        if not path.exists():
            output.write(f"unknown session: {args.session}")
            known = ", ".join(candidate.stem for candidate in list_sessions(directory)) or "none"
            output.write(f"sessions in {directory}: {known}")
            return None, path, False
        loaded = load_transcript(path)
        session = Session(
            session_id=args.session, workspace=workspace, messages=list(loaded.messages)
        )
        if loaded.header and loaded.header.workspace != str(workspace):
            output.write(
                f"note: this session started in {loaded.header.workspace}; "
                f"the workspace is now {workspace}"
            )
        if loaded.redacted:
            output.write(
                f"note: {loaded.redacted} message(s) were redacted when written, "
                "so the resumed history is not byte-identical to what the model saw"
            )
        if loaded.unreadable:
            output.write(
                f"note: {loaded.unreadable} transcript line(s) could not be read and "
                "were skipped (a killed process can leave a torn last line); "
                "the resumed history may be missing its tail"
            )
        return session, path, True

    session = Session(workspace=workspace)
    path = directory / f"{session.session_id}.jsonl"
    while path.exists():  # a generated id must never adopt someone else's file
        session = Session(workspace=workspace)
        path = directory / f"{session.session_id}.jsonl"
    return session, path, False


def _describe_instructions(user: str | None, project: str | None) -> str:
    """Say which instruction files were loaded, and how big they are.

    Without this line the user cannot tell what the model was told -- and
    "which AGENTS.md did it read?" is exactly the question a silent default
    invites.
    """

    parts = []
    for label, path, text in (
        ("user", "~/.nemo/AGENTS.md", user),
        ("project", "AGENTS.md", project),
    ):
        if not text:
            continue
        note = " (truncated)" if len(text) > DEFAULT_MAX_INSTRUCTIONS_CHARS else ""
        parts.append(f"{label} {path} ({len(text)} chars{note})")
    return "instructions: " + ", ".join(parts) if parts else "instructions: none"


def _switch_mode(policy: ApprovalPolicy, line: str, output: Streams) -> None:
    requested = line[len(":mode"):].strip()
    if not requested:
        output.write(f"mode     : {policy.mode.value} ({MODE_LABELS[policy.mode]})")
        return
    try:
        policy.mode = ApprovalMode(requested)
    except ValueError:
        output.write("mode must be one of: ask, auto, full")
        return
    output.write(f"mode     : {policy.mode.value} ({MODE_LABELS[policy.mode]})")


async def _turn(
    runtime: AgentRuntime, session: Session, path: Path, prompt: str, args, output: Streams
):
    cancel = asyncio.Event()
    loop = asyncio.get_running_loop()
    installed = False
    try:
        # Ctrl-C cancels the turn instead of killing the process, so the
        # transcript keeps whatever already happened.
        loop.add_signal_handler(signal.SIGINT, cancel.set)
        installed = True
    except (NotImplementedError, RuntimeError):
        pass
    try:
        result = await runtime.run(
            prompt,
            max_steps=args.max_steps,
            cancel=cancel,
            on_event=lambda event: output.write(render_event(event)),
            history=session.history(),
        )
    finally:
        if installed:
            loop.remove_signal_handler(signal.SIGINT)

    added = session.record(result)
    hidden = append_messages(path, added)
    output.write(f"status  : {result.state.status.value}")
    if result.state.error:
        output.write(f"error   : {redact(result.state.error)}")
    if result.state.output:
        output.write(f"\n{redact(result.state.output)}\n")
    if hidden:
        output.write(f"note: {hidden} message(s) were redacted before being written")
    return result


def _exit_code(result) -> int:
    return 0 if result is None or result.state.status.value == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
