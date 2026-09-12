from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .. import market
from ..db import init_db
from .routes import router

# 국내 전용 기능. 미국 모드에서 호출하면 화면이 조용히 빈 값을 그리는 대신 이유를 돌려준다.
KR_ONLY_PREFIXES = ("/api/trading", "/api/review")


class MarketContextMiddleware:
    """요청마다 시장 모드를 컨텍스트에 세운다 — 헤더(X-Market) > 쿼리(?market=) > 저장된 기본값.

    BaseHTTPMiddleware가 아니라 순수 ASGI 미들웨어로 둔다. 같은 태스크 컨텍스트에서 라우트를
    호출해야 contextvar가 엔드포인트(스레드풀 포함)까지 그대로 전달된다.
    """

    def __init__(self, app):
        self.app = app

    @staticmethod
    def _requested(scope) -> str | None:
        for key, value in scope.get("headers") or []:
            if key == b"x-market":
                return value.decode("latin-1")
        query = parse_qs((scope.get("query_string") or b"").decode("latin-1"))
        return (query.get("market") or [None])[0]

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        requested = self._requested(scope)
        try:
            token = market.set_active(requested) if requested else market.set_active(market.stored_default())
        except ValueError as exc:
            await JSONResponse({"detail": str(exc)}, status_code=422)(scope, receive, send)
            return
        try:
            path = scope.get("path", "")
            if not market.active().trading and path.startswith(KR_ONLY_PREFIXES):
                message = f"{market.active().label} 주식 모드에서는 지원하지 않는 기능입니다. 한국 주식 모드로 전환하세요."
                await JSONResponse({"detail": message}, status_code=409)(scope, receive, send)
                return
            await self.app(scope, receive, send)
        finally:
            market.reset(token)


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
    app.add_middleware(MarketContextMiddleware)
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
