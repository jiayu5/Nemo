"""Run Nemo Server on the loopback interface."""

from __future__ import annotations

import argparse
import http.client
import socket
from pathlib import Path

import uvicorn

from nemo.config.server_endpoint import server_port
from nemo.server.app import DEFAULT_DATABASE_PATH, create_app


def _port_occupied(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _nemo_running(host: str, port: int) -> bool:
    connection = http.client.HTTPConnection(host, port, timeout=1)
    try:
        connection.request("GET", "/openapi.json")
        response = connection.getresponse()
        return response.status == 200 and b'"title":"Nemo Server"' in response.read()
    except (OSError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m nemo.server")
    parser.add_argument("--host", choices=("127.0.0.1", "localhost", "::1"), default="127.0.0.1")
    parser.add_argument("--port", type=int, default=server_port())
    parser.add_argument("--database", default=str(DEFAULT_DATABASE_PATH))
    args = parser.parse_args()
    if _port_occupied(args.host, args.port):
        if _nemo_running(args.host, args.port):
            parser.exit(0, f"Nemo Server is already running at http://{args.host}:{args.port}\n")
        parser.exit(2, f"Port {args.port} is occupied by another process; use --port or NEMO_SERVER_PORT.\n")
    app = create_app(database_path=Path(args.database))
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
