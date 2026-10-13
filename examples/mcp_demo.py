"""A real local MCP server for both supported transports, without external data."""
import argparse
import asyncio
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations


def create_server(port=8767):
    server = FastMCP("Nemo demo", host="127.0.0.1", port=port,
                     stateless_http=True, json_response=True)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def add(a: int, b: int) -> int:
        """Add two integers without changing any files."""
        return a + b

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def wait(seconds: float = 0.1) -> str:
        """Wait briefly, to verify cancellation and timeouts."""
        await asyncio.sleep(min(max(seconds, 0), 60))
        return "finished"

    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()
    create_server(args.port).run(transport=args.transport)


if __name__ == "__main__":
    main()
