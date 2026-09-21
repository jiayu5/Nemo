"""Send one prompt through the configured model, end to end.

Usage:
    conda run -n nemo python examples/deepseek_agent.py "你好"
    conda run -n nemo python examples/deepseek_agent.py --model fast "1+1=?"

Reads ``~/.nemo/config.toml`` for routing and ``~/.nemo/.env`` for the API key.
Never prints the key; only the secret *name* and its source appear in output.
"""

import argparse
import asyncio
import sys

from nemo.bootstrap import build_model_client
from nemo.config.secrets import SecretLoader
from nemo.core.contracts.errors import NemoError
from nemo.core.runtime.agent import AgentRuntime
from nemo.core.tools.registry import ToolRegistry
from nemo.testing.fakes import AddTool


def show(event) -> None:
    """Print the trace, including token accounting when the provider reports it."""

    if event.type == "model.completed":
        payload = event.payload
        print(
            f"{event.seq} {event.type} "
            f"tools={payload['tool_call_count']} "
            f"prompt={payload['prompt_tokens']} "
            f"cached={payload['cached_prompt_tokens']} "
            f"completion={payload['completion_tokens']}"
        )
        return
    print(event.seq, event.type)


async def run(prompt: str, selection: str | None) -> int:
    client = build_model_client(run_override=selection)
    resolved = client.resolved
    print(f"model   : {resolved.model_name} -> {resolved.model_id}")
    print(f"protocol: {resolved.protocol} @ {resolved.base_url}")
    print(f"key     : {resolved.api_key_env} (from {SecretLoader().source(resolved.api_key_env)})")

    warning = SecretLoader().permission_warning()
    if warning:
        print(f"warning : {warning}")

    tools = ToolRegistry((AddTool(),)) if "tool_calling" in resolved.capabilities else ToolRegistry()
    if not tools.schemas():
        print("tools   : none (this model does not declare tool_calling)")
    print("-" * 40)

    result = await AgentRuntime(client, tools).run(prompt, on_event=show)
    print("-" * 40)
    print(f"status  : {result.state.status.value}")
    if result.state.error:
        print(f"error   : {result.state.error}")
    print(f"output  : {result.state.output}")
    return 0 if result.state.status.value == "completed" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Nemo model smoke test")
    parser.add_argument("prompt", nargs="?", default="用一句话介绍你自己")
    parser.add_argument("--model", default=None, help="profile / alias / model name override")
    args = parser.parse_args()
    try:
        return asyncio.run(run(args.prompt, args.model))
    except NemoError as exc:
        print(f"{type(exc).__name__}: {exc.public_message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
