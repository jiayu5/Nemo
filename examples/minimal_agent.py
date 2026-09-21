import asyncio
from nemo.core.runtime.agent import AgentRuntime
from nemo.core.tools.registry import ToolRegistry
from nemo.testing.fakes import AdditionModel, AddTool


async def main():
    runtime = AgentRuntime(AdditionModel(), ToolRegistry((AddTool(),)))
    result = await runtime.run("计算 12 + 30", on_event=lambda e: print(e.seq, e.type))
    print(result.state.status.value, result.state.output)


if __name__ == "__main__":
    asyncio.run(main())
