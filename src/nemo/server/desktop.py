"""Packaged desktop Server: ephemeral loopback port and per-launch access token."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import uvicorn

from nemo.server.app import create_app


DESKTOP_DATABASE_PATH = Path.home() / ".nemo" / "desktop.db"


async def serve() -> None:
    token = os.environ.pop("NEMO_DESKTOP_TOKEN", "")
    if len(token) < 24:
        raise RuntimeError("NEMO_DESKTOP_TOKEN is required for the desktop sidecar")

    config = uvicorn.Config(
        create_app(database_path=DESKTOP_DATABASE_PATH, desktop_token=token),
        host="127.0.0.1",
        port=0,
        access_log=False,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    parent_pid = os.getppid()

    async def watch_parent() -> None:
        while not server.should_exit:
            await asyncio.sleep(0.5)
            if os.getppid() != parent_pid:
                server.should_exit = True
                return

    task = asyncio.create_task(server.serve())
    watcher = asyncio.create_task(watch_parent())
    try:
        while not server.started:
            if task.done():
                await task
                raise RuntimeError("Desktop Server exited before startup")
            await asyncio.sleep(0.05)
        sockets = [sock for listener in server.servers for sock in listener.sockets]
        if len(sockets) != 1:
            raise RuntimeError("Desktop Server did not bind one loopback socket")
        port = sockets[0].getsockname()[1]
        print(f"NEMO_READY:{port}", flush=True)
        await task
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
        if not task.done():
            server.should_exit = True
            await task


def main() -> None:
    try:
        asyncio.run(serve())
    except (OSError, RuntimeError):
        print("Desktop Server could not start", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
