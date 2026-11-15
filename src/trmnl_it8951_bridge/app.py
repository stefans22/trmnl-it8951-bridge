"""HTTP API and status page (design §3.9)."""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib import resources
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from . import __version__
from .mailbox import Command
from .pipeline import ImageRejectedError, to_png
from .runtime import NotReadyError, Runtime


def create_app(runtime: Runtime) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await runtime.start()
        try:
            yield
        finally:
            await runtime.stop()

    app = FastAPI(
        title="trmnl-it8951-bridge",
        version=__version__,
        lifespan=lifespan,
        description="Shows TRMNL HA add-on images on an IT8951 e-paper panel.",
    )
    push_cfg = runtime.settings.push

    def require_token(request: Request) -> None:
        if push_cfg.token is None:
            return
        expected = f"Bearer {push_cfg.token.get_secret_value()}"
        given = request.headers.get("authorization", "")
        if not secrets.compare_digest(given.encode(), expected.encode()):
            raise HTTPException(401, "missing or invalid bearer token", {"WWW-Authenticate": "Bearer"})

    auth = [Depends(require_token)]

    async def read_body(request: Request) -> bytes:
        limit = push_cfg.max_body_bytes
        declared = request.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            raise HTTPException(413, f"body larger than {limit} bytes")
        chunks: list[bytes] = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > limit:
                raise HTTPException(413, f"body larger than {limit} bytes")
            chunks.append(chunk)
        return b"".join(chunks)

    @app.post("/api/image", status_code=202, dependencies=auth, summary="Push an image (raw PNG/JPEG body)")
    async def push_image(
        request: Request,
        refresh: Annotated[Literal["auto", "full"], Query()] = "auto",
    ) -> dict[str, Any]:
        if not push_cfg.enabled:
            raise HTTPException(404, "push is disabled")
        data = await read_body(request)
        try:
            accepted = await runtime.submit(data, "push", force_full=refresh == "full")
        except ImageRejectedError as e:
            raise HTTPException(e.status_code, str(e)) from e
        except NotReadyError as e:
            raise HTTPException(503, str(e)) from e
        return {"frame_id": accepted.frame.id, "hash": accepted.frame.hash, "duplicate": accepted.duplicate}

    @app.post("/api/refresh", status_code=202, dependencies=auth, summary="Full refresh of the current image")
    async def refresh() -> dict[str, str]:
        runtime.command(Command.REFRESH_FULL)
        return {"status": "queued"}

    @app.post("/api/clear", status_code=202, dependencies=auth, summary="Clear the panel to white")
    async def clear() -> dict[str, str]:
        runtime.command(Command.CLEAR)
        return {"status": "queued"}

    @app.get("/health", summary="Liveness/readiness (ignores add-on availability)")
    async def health() -> JSONResponse:
        ok, reason = runtime.healthy
        if ok:
            return JSONResponse({"status": "ok"})
        status = "starting" if reason == "starting" else "error"
        return JSONResponse({"status": status, "reason": reason}, status_code=503)

    @app.get("/api/status", summary="Runtime status")
    async def status() -> dict[str, Any]:
        return runtime.status_json()

    @app.get(
        "/api/image/current.png",
        summary="Image currently on the panel (logical orientation)",
        response_class=Response,
        responses={200: {"content": {"image/png": {}}}, 404: {"description": "nothing shown"}},
    )
    async def current_png(request: Request) -> Response:
        frame = runtime.worker.current
        if frame is None:
            raise HTTPException(404, "nothing is shown")
        etag = f'"{frame.hash}"'
        headers = {"ETag": etag, "Cache-Control": "no-cache"}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        return Response(to_png(frame.preview), media_type="image/png", headers=headers)

    index_html = resources.files(__package__).joinpath("static/index.html").read_text()

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def index() -> HTMLResponse:
        return HTMLResponse(index_html)

    return app
