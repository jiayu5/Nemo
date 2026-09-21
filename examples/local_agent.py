"""Run Nemo against the configured model with the local tools enabled.

Usage:
    conda run -n nemo python examples/local_agent.py "在 workspace 里建一个 hello.txt，内容写 pong"
    conda run -n nemo python examples/local_agent.py --workspace /tmp/demo --read-only "这个目录里有什么"

The workspace root is a boundary convention, not a sandbox: tools refuse paths
outside it, but a shell command can still reach wherever the shell can.
"""

import argparse
import asyncio
import sys
from pathlib import Path

from nemo.bootstrap import build_local_tools, build_model_client
from nemo.config.secrets import SecretLoader
from nemo.core.context.builder import ContextBuilder
from nemo.core.contracts.errors import NemoError
from nemo.core.contracts.tools import ExecutionContext
from nemo.core.runtime.agent import AgentRuntime
from nemo.core.tools.policy import AllowAllPolicy, ReadOnlyPolicy
from nemo.core.tools.registry import ToolRegistry
from nemo.prompts.local_agent import build_system_prompt
from nemo.prompts.instructions import load_project_instructions, load_user_instructions

def show(event) -> None:
    payload = event.payload
    if event.type == "model.completed":
        print(
            f"{event.seq:2} {event.type} tools={payload['tool_call_count']} "
            f"prompt={payload['prompt_tokens']} cached={payload['cached_prompt_tokens']} "
            f"completion={payload['completion_tokens']}"
        )
    elif event.type in ("tool.started", "tool.completed", "tool.failed"):
        detail = payload.get("summary") or payload.get("name")
        code = payload.get("error_code")
        print(f"{event.seq:2} {event.type} {detail}" + (f"  [{code}]" if code else ""))
    else:
        print(f"{event.seq:2} {event.type}")


async def run(prompt: str, workspace: Path, selection: str | None, read_only: bool) -> int:
    workspace = workspace.resolve()
    if not workspace.is_dir():
        print(f"workspace is not a directory: {workspace}", file=sys.stderr)
        return 2

    client = build_model_client(run_override=selection)
    resolved = client.resolved
    tools = build_local_tools()
    policy = ReadOnlyPolicy() if read_only else AllowAllPolicy()
    registry = ToolRegistry(
        tools,
        context=ExecutionContext(workspace=workspace),
        policy=policy,
    )
    runtime = AgentRuntime(
        client,
        registry,
        ContextBuilder(
            system_prompt=build_system_prompt(
                workspace=workspace, tools=(tool.name for tool in tools)
            ),
            user_instructions=load_user_instructions(),
            project_instructions=load_project_instructions(workspace),
        ),
    )

    print(f"model    : {resolved.model_name} -> {resolved.model_id}")
    print(f"workspace: {workspace}")
    print(f"policy   : {'read-only' if read_only else 'allow-all (workspace boundary still applies)'}")
    print(f"key      : {resolved.api_key_env} (from {SecretLoader().source(resolved.api_key_env)})")
    print("-" * 60)

    result = await runtime.run(prompt, on_event=show)
    print("-" * 60)
    print(f"status  : {result.state.status.value}")
    if result.state.error:
        print(f"error   : {result.state.error}")
    print(f"output  : {result.state.output}")
    return 0 if result.state.status.value == "completed" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Nemo local agent")
    parser.add_argument("prompt")
    parser.add_argument("--workspace", default=".", help="Directory the tools are confined to")
    parser.add_argument("--model", default=None, help="profile / alias / model name override")
    parser.add_argument("--read-only", action="store_true", help="Deny every mutating tool")
    args = parser.parse_args()
    try:
        return asyncio.run(run(args.prompt, Path(args.workspace), args.model, args.read_only))
    except NemoError as exc:
        print(f"{type(exc).__name__}: {exc.public_message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
