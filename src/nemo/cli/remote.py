"""Server-backed CLI session and turn handling."""

from __future__ import annotations

import asyncio
import signal
from pathlib import Path

from nemo.cli.approver import TerminalApprover
from nemo.cli.render import render_event
from nemo.cli.server_client import ServerClient
from nemo.cli.streams import Streams
from nemo.core.contracts.events import EventType
from nemo.core.tools.approval import ApprovalMode, ApprovalRequest
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


async def serve_remote(args, output: Streams) -> int:
    workspace = Path(args.workspace).expanduser().resolve()
    if not workspace.is_dir():
        output.write(f"workspace is not a directory: {workspace}")
        return 2
    async with ServerClient(args.server) as client:
        if args.sessions:
            for session in await client.list_sessions():
                output.write(session["session_id"])
            return 0
        if args.session:
            session = await client.get_session(args.session)
            resumed = True
            if session["workspace"] != str(workspace):
                output.write(
                    f"note: this session belongs to {session['workspace']}; "
                    f"--workspace {workspace} is ignored"
                )
        else:
            session = await client.create_session(
                workspace=str(workspace), model=args.model, approval_mode=ApprovalMode(args.mode)
            )
            resumed = False
        mode = ApprovalMode(session["approval_mode"])
        output.write(
            f"session  : {session['session_id']}"
            + (f" (resumed, {session['message_count']} messages)" if resumed else " (new)")
        )
        output.write(f"server   : {args.server}")
        output.write(f"model    : {session['model'] or 'default'}")
        output.write(f"workspace: {session['workspace']}")
        output.write(f"mode     : {mode.value} ({MODE_LABELS[mode]})")

        prompt = " ".join(args.prompt).strip()
        if prompt:
            return await remote_turn(client, session["session_id"], prompt, args, output)

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
                mode = await switch_remote_mode(client, session["session_id"], mode, line, output)
                continue
            if line.startswith(":"):
                output.write(f"unknown command: {line}")
                continue
            await remote_turn(client, session["session_id"], line, args, output)


async def switch_remote_mode(
    client: ServerClient,
    session_id: str,
    current: ApprovalMode,
    line: str,
    output: Streams,
) -> ApprovalMode:
    requested = line[len(":mode"):].strip()
    if not requested:
        output.write(f"mode     : {current.value} ({MODE_LABELS[current]})")
        return current
    try:
        mode = ApprovalMode(requested)
    except ValueError:
        output.write("mode must be one of: ask, auto, full")
        return current
    await client.update_mode(session_id, mode)
    output.write(f"mode     : {mode.value} ({MODE_LABELS[mode]})")
    return mode


async def remote_turn(
    client: ServerClient, session_id: str, prompt: str, args, output: Streams
) -> int:
    run = await client.start_run(session_id, prompt=prompt, max_steps=args.max_steps)
    run_id = run["run_id"]
    loop = asyncio.get_running_loop()
    installed = False

    def request_cancel() -> None:
        asyncio.create_task(client.cancel_run(run_id))

    try:
        loop.add_signal_handler(signal.SIGINT, request_cancel)
        installed = True
    except (NotImplementedError, RuntimeError):
        pass
    approver = TerminalApprover(output)
    streamed = False
    try:
        async for event in client.events(run_id):
            if event.type == EventType.MODEL_DELTA:
                chunk = event.payload.get("text")
                if isinstance(chunk, str) and chunk:
                    (output.write_chunk or output.write)(redact(chunk))
                    streamed = True
                continue
            if streamed and event.type == EventType.MODEL_COMPLETED:
                output.write("")
            output.write(render_event(event))
            if event.type == EventType.APPROVAL_REQUESTED:
                payload = event.payload
                outcome = await approver(
                    ApprovalRequest(
                        tool_name=payload.get("tool_name", "unknown"),
                        summary=payload.get("summary", ""),
                        reason=payload.get("reason", "approval required"),
                    )
                )
                await client.answer_approval(run_id, payload["request_id"], outcome)
    finally:
        if installed:
            loop.remove_signal_handler(signal.SIGINT)
    run = await client.get_run(run_id)
    output.write(f"status  : {run['status']}")
    if run.get("error"):
        output.write(f"error   : {redact(run['error'])}")
    if run.get("output") and not streamed:
        output.write(f"\n{redact(run['output'])}\n")
    return 0 if run["status"] == "completed" else 1
