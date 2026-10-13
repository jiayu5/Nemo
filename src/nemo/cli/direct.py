"""Legacy in-process CLI mode used for recovery and focused testing."""

from __future__ import annotations

import asyncio
import signal
from pathlib import Path

from nemo.bootstrap import build_local_tools, build_memory_store, build_skill_catalog, build_skill_tools, build_mcp_tools
from nemo.core.skills import skill_directory
from nemo.core.memory import MemoryStore, memory_messages
from nemo.core.contracts.events import EventType
from nemo.cli.approver import TerminalApprover
from nemo.cli.render import render_event
from nemo.cli.streams import Streams
from nemo.cli.transcript import SessionHeader, append_compaction, append_messages, list_sessions, load_transcript, write_header
from nemo.config.secrets import SecretLoader
from nemo.config.loader import load_config
from nemo.core.context.builder import DEFAULT_MAX_INSTRUCTIONS_CHARS, ContextBuilder
from nemo.core.context.compaction import CompactionCheckpoint, ContextWindow
from nemo.core.contracts.tools import ExecutionContext
from nemo.core.runtime.agent import AgentRuntime
from nemo.core.runtime.subagents import SubagentManager
from nemo.core.session import Session
from nemo.core.tools.approval import ApprovalMode, ApprovalPolicy
from nemo.core.tools.registry import ToolRegistry
from nemo.prompts.instructions import load_project_instructions, load_user_instructions
from nemo.prompts.system_prompt import build_subagent_system_prompt, build_system_prompt
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

    session, path, resumed, checkpoint = open_session(args, workspace, directory, output)
    if session is None:
        return 2

    # Hold transport contexts outside AgentRuntime; child cleanup completes first.
    from contextlib import AsyncExitStack
    client = client_factory(run_override=args.model)
    try:
        async with AsyncExitStack() as stack:
            mcp_tools = await stack.enter_async_context(build_mcp_tools(workspace))
            return await _serve_connected(args, output, directory, client, workspace,
                                          session, path, resumed, checkpoint, mcp_tools)
    finally:
        closer = getattr(client, "aclose", None)
        if closer is not None:
            await closer()


async def _serve_connected(args, output, directory, client, workspace,
                           session, path, resumed, checkpoint, mcp_tools):
    resolved = client.resolved
    mode = ApprovalMode(args.mode)
    policy = ApprovalPolicy(mode, approver=TerminalApprover(output))
    memory_store = build_memory_store()
    skills = build_skill_catalog(workspace)
    tools = (*build_local_tools(memory_store=memory_store), *build_skill_tools(skills), *mcp_tools)
    user_instructions = load_user_instructions()
    project_instructions = load_project_instructions(workspace)
    window = (
        ContextWindow(resolved.context_window_tokens, resolved.response_reserve_tokens)
        if resolved.context_window_tokens is not None else None
    )
    child_tools = tuple(tool for tool in tools if tool.read_only and getattr(tool, "subagent_allowed", True) and tool.name != "run_shell")
    subagents = SubagentManager(
        client, child_tools, policy=policy, context_window=window,
        limits=load_config().subagents,
        context=ContextBuilder(
            system_prompt=build_subagent_system_prompt(
                workspace=workspace, tools=(tool.name for tool in child_tools),
            ),
            user_instructions=user_instructions,
            project_instructions=project_instructions,
            skills_directory=skill_directory(skills),
        ),
    )
    tools = (*tools, *subagents.tools())
    registry = ToolRegistry(tools, context=ExecutionContext(workspace=workspace), policy=policy)
    runtime = AgentRuntime(
        subagents.model,
        registry,
        ContextBuilder(
            system_prompt=build_system_prompt(
                workspace=workspace, tools=(tool.name for tool in tools)
            ),
            user_instructions=user_instructions,
            project_instructions=project_instructions,
            skills_directory=skill_directory(skills),
        ),
        context_window=window,
        checkpoint=checkpoint,
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
        return exit_code(await run_turn(runtime, session, path, prompt, args, output, memory_store=memory_store))

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
        exit_code(await run_turn(runtime, session, path, line, args, output, memory_store=memory_store))


def open_session(args, workspace: Path, directory: Path, output: Streams):
    if args.session:
        path = directory / f"{args.session}.jsonl"
        if not path.exists():
            output.write(f"unknown session: {args.session}")
            known = ", ".join(candidate.stem for candidate in list_sessions(directory)) or "none"
            output.write(f"sessions in {directory}: {known}")
            return None, path, False, None
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
        return session, path, True, loaded.checkpoint

    session = Session(workspace=workspace)
    path = directory / f"{session.session_id}.jsonl"
    while path.exists():
        session = Session(workspace=workspace)
        path = directory / f"{session.session_id}.jsonl"
    return session, path, False, None


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
    runtime: AgentRuntime, session: Session, path: Path, prompt: str, args, output: Streams,
    *, memory_store: MemoryStore | None = None,
):
    cancel = asyncio.Event()
    loop = asyncio.get_running_loop()
    installed = False
    streamed = False

    def on_event(event):
        nonlocal streamed
        if event.type == EventType.CONTEXT_COMPACTED:
            append_compaction(path, CompactionCheckpoint(
                covered_messages=event.payload["covered_messages"],
                history_digest=event.payload["history_digest"],
                summary=event.payload["summary"],
            ))
        if event.type == EventType.MODEL_DELTA:
            chunk = event.payload.get("text")
            if isinstance(chunk, str) and chunk:
                (output.write_chunk or output.write)(redact(chunk))
                streamed = True
            return
        if streamed and event.type == EventType.MODEL_COMPLETED:
            output.write("")
        output.write(render_event(event))
    try:
        loop.add_signal_handler(signal.SIGINT, cancel.set)
        installed = True
    except (NotImplementedError, RuntimeError):
        pass
    try:
        history = session.history()
        if memory_store:
            catalog = await asyncio.to_thread(memory_store.catalog, session.workspace)
            history = (*history, *memory_messages(history, catalog))
        runtime.tools.context = runtime.tools.context.model_copy(update={
            "session_id": session.session_id, "source_message_index": len(history),
        })
        result = await runtime.run(
            prompt,
            max_steps=args.max_steps,
            cancel=cancel,
            on_event=on_event,
            history=history,
        )
    finally:
        if installed:
            loop.remove_signal_handler(signal.SIGINT)

    added = session.record(result)
    hidden = append_messages(path, added)
    output.write(f"status  : {result.state.status.value}")
    if result.state.error:
        output.write(f"error   : {redact(result.state.error)}")
    if result.state.output and not streamed:
        output.write(f"\n{redact(result.state.output)}\n")
    if hidden:
        output.write(f"note: {hidden} message(s) were redacted before being written")
    return result


def exit_code(result) -> int:
    return 0 if result is None or result.state.status.value == "completed" else 1
