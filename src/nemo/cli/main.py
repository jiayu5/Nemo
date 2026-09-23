"""Nemo command-line entry point and mode dispatch."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from nemo.bootstrap import build_model_client
from nemo.cli.direct import serve_direct
from nemo.cli.remote import serve_remote
from nemo.cli.server_client import ServerClientError
from nemo.cli.streams import Streams
from nemo.cli.transcript import list_sessions
from nemo.config.server_endpoint import server_url
from nemo.core.contracts.errors import NemoError
from nemo.core.tools.approval import ApprovalMode


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
    parser.add_argument(
        "--server",
        default=server_url(),
        help="Nemo Server base URL (default: http://127.0.0.1:18765; NEMO_SERVER_PORT overrides it)",
    )
    parser.add_argument(
        "--direct",
        action="store_true",
        help="legacy embedded mode without Server; intended for tests and recovery",
    )
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
    use_server = not args.direct and client_factory is build_model_client
    if args.sessions and not use_server:
        for path in list_sessions(directory):
            output.write(path.stem)
        return 0
    try:
        if use_server:
            return asyncio.run(serve_remote(args, output))
        return asyncio.run(serve_direct(args, output, directory, client_factory))
    except KeyboardInterrupt:
        output.write("")
        return 130
    except NemoError as exc:
        output.write(f"{type(exc).__name__}: {exc.public_message}")
        return 2
    except ServerClientError as exc:
        output.write(f"ServerClientError: {exc}")
        output.write("start it with: python -m nemo.server")
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
