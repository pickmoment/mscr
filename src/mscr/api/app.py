from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from ..db import init_db
from .routes import router


class AppFiles(StaticFiles):
    """Serve hashed assets normally but always revalidate the HTML shell."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        if response.media_type == "text/html":
            response.headers["cache-control"] = "no-cache, must-revalidate"
        return response


def create_app() -> FastAPI:
    init_db()
    app = FastAPI(title="mscr", version="0.1.0")
    app.include_router(router)
    dist = Path(__file__).parents[3] / "frontend" / "dist"
    if dist.exists():
        app.mount("/", AppFiles(directory=dist, html=True), name="frontend")
    else:
        @app.get("/", response_class=PlainTextResponse)
        def missing_frontend():
            return "frontend/dist 가 없습니다. cd frontend && bun install && bun run build 를 실행하세요."
    return app

app = create_app()
