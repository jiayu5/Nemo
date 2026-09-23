"""Optional loopback access guard for the packaged desktop sidecar."""

from __future__ import annotations

from hmac import compare_digest

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

TOKEN_HEADER = "X-Nemo-Token"
DESKTOP_ORIGINS = frozenset((
    "tauri://localhost",
    "http://tauri.localhost",
    "http://127.0.0.1:5173",
))


def install_desktop_access(app: FastAPI, token: str) -> None:
    if len(token) < 24:
        raise ValueError("Desktop access token is too short")

    @app.middleware("http")
    async def desktop_access(request: Request, call_next) -> Response:
        origin = request.headers.get("origin")
        if origin is not None and origin not in DESKTOP_ORIGINS:
            return JSONResponse({"detail": "origin not allowed"}, status_code=403)

        cors_headers = {"Access-Control-Allow-Origin": origin, "Vary": "Origin"} if origin else {}
        if request.method == "OPTIONS" and origin:
            return Response(status_code=204, headers={
                **cors_headers,
                "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, X-Nemo-Token, Last-Event-ID",
            })

        supplied = request.headers.get(TOKEN_HEADER, "")
        if not compare_digest(supplied, token):
            return JSONResponse({"detail": "unauthorized"}, status_code=401,
                                headers=cors_headers)

        response = await call_next(request)
        response.headers.update(cors_headers)
        return response
