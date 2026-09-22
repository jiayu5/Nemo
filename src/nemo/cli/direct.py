"""Legacy in-process CLI mode used for recovery and focused testing."""

from __future__ import annotations

import asyncio
import signal
from pathlib import Path

from nemo.bootstrap import build_local_tools
from nemo.cli.approver import TerminalApprover
from nemo.cli.render import render_event
from nemo.cli.streams import Streams
from nemo.cli.transcript import SessionHeader, append_messages, list_sessions, load_transcript, write_header
from nemo.config.secrets import SecretLoader
from nemo.core.context.builder import DEFAULT_MAX_INSTRUCTIONS_CHARS, ContextBuilder
from nemo.core.contracts.tools import ExecutionContext
from nemo.core.runtime.agent import AgentRuntime
from nemo.core.session import Session
from nemo.core.tools.approval import ApprovalMode, ApprovalPolicy
from nemo.core.tools.registry import ToolRegistry
from nemo.prompts.instructions import load_project_instructions, load_user_instructions
from nemo.prompts.system_prompt import build_system_prompt
from nemo.redaction import redact

MODE_LABELS = {
    ApprovalMode.ASK: "Codex: Read Only",
    ApprovalMode.AUTO: "Codex: Auto",
    ApprovalMode.FULL: "Codex: Full Access",
}

HELP = """commands:
  :help            show this
  :mode            show the approval mode
  :mode ask|auto|full
  :exit, :quit     leave (the session stays saved)"""


async def serve_direct(args, output: Streams, directory: Path, client_factory) -> int:
    workspace = Path(args.workspace).expanduser().resolve()
    if not workspace.is_dir():
        output.write(f"workspace is not a directory: {workspace}")
        return 2

    session, path, resumed = open_session(args, workspace, directory, output)
    if session is None:
        return 2

    client = client_factory(run_override=args.model)
    resolved = client.resolved
    mode = ApprovalMode(args.mode)
    policy = ApprovalPolicy(mode, approver=TerminalApprover(output))
    tools = build_local_tools()
    user_instructions = load_user_instructions()
    project_instructions = load_project_instructions(workspace)
    registry = ToolRegistry(tools, context=ExecutionContext(workspace=workspace), policy=policy)
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
    output.write(
        f"session  : {session.session_id}"
        + (f" (resumed, {len(session.messages)} messages)" if resumed else " (new)")
    )
    output.write(f"model    : {resolved.model_name} -> {resolved.model_id}")
    output.write(f"workspace: {workspace}")
    output.write(f"mode     : {mode.value} ({MODE_LABELS[mode]})")
    output.write(
        f"key      : {resolved.api_key_env} (from {SecretLoader().source(resolved.api_key_env)})"
    )
    output.write(describe_instructions(user_instructions, project_instructions))
    output.write(f"transcript: {path}")

    prompt = " ".join(args.prompt).strip()
    if prompt:
        return exit_code(await run_turn(runtime, session, path, prompt, args, output))

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
            switch_mode(policy, line, output)
            continue
        if line.startswith(":"):
            output.write(f"unknown command: {line}")
            continue
        exit_code(await run_turn(runtime, session, path, line, args, output))


def open_session(args, workspace: Path, directory: Path, output: Streams):
    if args.session:
        path = directory / f"{args.session}.jsonl"
        if not path.exists():
            output.write(f"unknown session: {args.session}")
            known = ", ".join(candidate.stem for candidate in list_sessions(directory)) or "none"
            output.write(f"sessions in {directory}: {known}")
            return None, path, False
        loaded = load_transcript(path)
        session = Session(session_id=args.session, workspace=workspace, messages=list(loaded.messages))
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
    while path.exists():
        session = Session(workspace=workspace)
        path = directory / f"{session.session_id}.jsonl"
    return session, path, False


def describe_instructions(user: str | None, project: str | None) -> str:
    parts = []
    for label, path, content in (
        ("user", "~/.nemo/AGENTS.md", user),
        ("project", "AGENTS.md", project),
    ):
        if not content:
            continue
        note = " (truncated)" if len(content) > DEFAULT_MAX_INSTRUCTIONS_CHARS else ""
        parts.append(f"{label} {path} ({len(content)} chars{note})")
    return "instructions: " + ", ".join(parts) if parts else "instructions: none"


def switch_mode(policy: ApprovalPolicy, line: str, output: Streams) -> None:
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


async def run_turn(
    runtime: AgentRuntime, session: Session, path: Path, prompt: str, args, output: Streams
):
    cancel = asyncio.Event()
    loop = asyncio.get_running_loop()
    installed = False
    try:
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


def exit_code(result) -> int:
    return 0 if result is None or result.state.status.value == "completed" else 1
