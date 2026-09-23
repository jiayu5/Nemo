"""Local Server address shared by the Python entry points."""

from __future__ import annotations

import os

DEFAULT_SERVER_PORT = 18765


def server_port() -> int:
    raw = os.environ.get("NEMO_SERVER_PORT", str(DEFAULT_SERVER_PORT))
    try:
        port = int(raw)
    except ValueError as exc:
        raise ValueError("NEMO_SERVER_PORT must be an integer from 1 to 65535") from exc
    if not 1 <= port <= 65535:
        raise ValueError("NEMO_SERVER_PORT must be an integer from 1 to 65535")
    return port


def server_url() -> str:
    return f"http://127.0.0.1:{server_port()}"
