"""Run Nemo Server on the loopback interface."""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from nemo.server.app import DEFAULT_DATABASE_PATH, create_app


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m nemo.server")
    parser.add_argument("--host", choices=("127.0.0.1", "localhost", "::1"), default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--database", default=str(DEFAULT_DATABASE_PATH))
    args = parser.parse_args()
    app = create_app(database_path=Path(args.database))
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
