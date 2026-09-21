"""Shell tool: one command per call, in its own process group."""

import asyncio
import os
import signal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from nemo.core.contracts.errors import ToolFailure
from nemo.core.contracts.tools import ExecutionContext
from nemo.core.tools.limits import truncate_text
from nemo.core.tools.paths import resolve_in_workspace

#: Keep subprocesses from blocking on a pager or emitting terminal control codes.
_SANITIZED_ENV = {"PAGER": "cat", "GIT_PAGER": "cat", "TERM": "dumb", "NO_COLOR": "1"}
_KILL_GRACE_SECONDS = 5.0


class RunShellArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: str = Field(min_length=1, description="Command to run with /bin/sh -c")
    workdir: str = Field(
        default=".", min_length=1, description="Directory relative to the workspace"
    )
    timeout_seconds: float | None = Field(
        default=None,
        gt=0,
        description="Overrides the default timeout, capped by the runtime",
    )


class RunShellTool:
    name = "run_shell"
    description = (
        "Run a shell command with /bin/sh -c inside the workspace and return its exit "
        "code, stdout and stderr. Each call is a fresh process: no state carries over "
        "between calls. A non-zero exit code is normal output, not an error."
    )
    arguments_type = RunShellArguments
    read_only = False

    async def execute(self, arguments: RunShellArguments, context: ExecutionContext) -> str:
        workdir = resolve_in_workspace(context.workspace, arguments.workdir)
        if not workdir.is_dir():
            raise ToolFailure(f"not a directory: {arguments.workdir}")
        requested = arguments.timeout_seconds or context.default_timeout_seconds
        timeout = min(requested, context.max_timeout_seconds)
        return await run_command(
            arguments.command,
            workdir,
            timeout=timeout,
            max_output_chars=context.max_output_chars,
        )

    def summarize(self, arguments: RunShellArguments) -> str:
        return f"$ {arguments.command}"

    def shell_command(self, arguments: RunShellArguments) -> str:
        """Declares the exact text to the approval layer (see ``Tool``)."""

        return arguments.command


async def run_command(
    command: str, workdir: Path, *, timeout: float, max_output_chars: int
) -> str:
    environment = {**os.environ, **_SANITIZED_ENV}
    try:
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=str(workdir),
            env=environment,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # Own process group, so a timeout kills the whole tree instead of
            # leaving orphaned grandchildren behind (sh -c "cmd" spawns a child).
            start_new_session=True,
        )
    except OSError as exc:
        raise ToolFailure(f"could not start the command: {type(exc).__name__}") from None

    communication = asyncio.ensure_future(process.communicate())
    try:
        # Shield so the timeout does not cancel the drain; the pipes still need
        # to be emptied after the kill, otherwise the process cannot be reaped.
        stdout, stderr = await asyncio.wait_for(asyncio.shield(communication), timeout)
    except asyncio.TimeoutError:
        await _kill_process_group(process)
        await communication
        raise ToolFailure(
            f"command timed out after {timeout:g}s and its process group was killed"
        ) from None
    except asyncio.CancelledError:
        await _kill_process_group(process)
        await communication
        raise

    body = _format_streams(stdout, stderr)
    bounded = truncate_text(body, max_output_chars, keep="middle")
    return f"exit_code: {process.returncode}\n{bounded}"


async def _kill_process_group(process: asyncio.subprocess.Process) -> None:
    """Kill the whole group, then reap it so no zombie is left behind."""

    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            process.kill()
        except ProcessLookupError:
            pass
    try:
        await asyncio.wait_for(process.wait(), _KILL_GRACE_SECONDS)
    except asyncio.TimeoutError:  # pragma: no cover - depends on OS scheduling
        pass


def _format_streams(stdout: bytes, stderr: bytes) -> str:
    sections: list[str] = []
    if stdout:
        sections.append("stdout:\n" + stdout.decode("utf-8", errors="replace").rstrip("\n"))
    if stderr:
        sections.append("stderr:\n" + stderr.decode("utf-8", errors="replace").rstrip("\n"))
    return "\n".join(sections) if sections else "(no output)"
