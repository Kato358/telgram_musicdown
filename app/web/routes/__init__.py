"""FastAPI 应用装配（SDD §4）：错误包络、SSE、静态资源；路由按资源分模块注册。

装配本身不写任何业务端点——端点在各资源模块（auth/setup/stats/sources/
downloads/history/preview/settings/search），本文件只负责应用级横切面（SRP）。
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.errors import AppError
from app.web.routes import schemas  # noqa: F401  兼容旧 import 路径（tests 直接 import）
from app.web.routes.context import RouteContext, register_all

logger = logging.getLogger(__name__)

# 前端产物缺失时的首屏说明（首次部署的唯一出路就是把产物建出来）
_FRONTEND_MISSING = """前端产物缺失，暂时打不开界面。

在源码根构建一次再启动：
  cd web && npm install && npm run build
或用 ./run.sh（缺产物时会自动构建）。

Docker：镜像构建前要先有 web/dist（Dockerfile 会 COPY web/dist）。
产物在别处时用 TGM_STATIC_DIR 指过去。
"""


def _envelope(code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=400, content={"error": {"code": code, "message": message}})


def create_app(  # noqa: PLR0915  应用级横切面注册
    store: object,
    events: object,
    downloads: object,
    sources: object,
    search: object,
    preview: object,
    tg: object,
    *,
    library: object | None = None,
    base_dir: object,
    web_host: str = "127.0.0.1",
    web_login_secret: str = "",
    static_dir: object = None,
) -> FastAPI:
    """装配 FastAPI 应用：错误包络 + SSE + 静态资源 + 按资源注册的路由。

    ``library`` 未给时按默认布局兜底装配（save_path = base_dir/downloads），
    曲库路由因此始终可用；生产由容器传入。
    """
    library_service = library
    if library_service is None:
        from pathlib import Path  # noqa: PLC0415  仅兜底装配需要

        from app.services.local_library import LocalLibraryService  # noqa: PLC0415

        tags = getattr(downloads, "tags", None)
        library_service = LocalLibraryService(
            store,  # type: ignore[arg-type]
            Path(str(base_dir)) / "downloads",  # type: ignore[arg-type]
            tags,  # type: ignore[arg-type]
            events,  # type: ignore[arg-type]
        )
    ctx = RouteContext(
        store=store,  # type: ignore[arg-type]  # IStore 协议（duck-type）
        events=events,
        downloads=downloads,  # type: ignore[arg-type]
        sources=sources,  # type: ignore[arg-type]
        search=search,  # type: ignore[arg-type]
        preview=preview,  # type: ignore[arg-type]
        library=library_service,  # type: ignore[arg-type]
        tg=tg,
        base_dir=base_dir,  # type: ignore[arg-type]
        web_host=web_host,
        web_login_secret=web_login_secret,
        static_dir=static_dir,  # type: ignore[arg-type]
    )
    app = FastAPI(title="telegram-musicdown")

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        payload: dict[str, object] = {"code": exc.code, "message": exc.message}
        if hasattr(exc, "reason"):
            payload["reason"] = exc.reason
        return JSONResponse(status_code=400, content={"error": payload})

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        """Pydantic 校验失败也走同一错误包络（NFR-08/§4），前端只需认一种错误形状。"""
        first = exc.errors()[0] if exc.errors() else {}
        field = ".".join(str(p) for p in first.get("loc", ()) if p != "body")
        message = f"{field}: {first.get('msg', 'invalid request')}".lstrip(": ")
        return JSONResponse(
            status_code=400, content={"error": {"code": "validation_error", "message": message}}
        )

    # ---- events (SSE) ----

    @app.get("/api/events")
    async def events_sse(request: Request) -> StreamingResponse:
        async def gen():
            q = await events.subscribe()  # type: ignore[union-attr]
            try:
                while True:
                    ev = await q.get()
                    yield ev.to_sse()
            finally:
                await events.unsubscribe(q)  # type: ignore[union-attr]

        return StreamingResponse(gen(), media_type="text/event-stream")

    # ---- 按资源注册路由（auth/setup/stats/sources/downloads/history/preview/settings/search）----
    register_all(app, ctx)

    # ---- static ----
    static = ctx.static_dir
    if static and static.exists():
        # /assets 等静态文件；其余非 /api 路径全部回退 index.html（SPA 路由，SDD §4.3）
        app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

        @app.exception_handler(404)
        async def spa_fallback(request: Request, exc: object) -> object:
            if request.url.path.startswith("/api"):
                return JSONResponse(status_code=404, content={"detail": "Not Found"})
            return FileResponse(static / "index.html")
    else:
        # 前端产物缺失：首次部署最常见的原因（本地还没构建、Docker 镜像里没 COPY 进去）。
        # 这里给可操作说明而不是 404 裸 JSON——否则首屏只有一行 {"detail":"Not Found"}，
        # 用户没有任何入口去进初始化向导。
        logger.error("web frontend missing at %s: run a build (npm run build) first", static)

        @app.exception_handler(404)
        async def spa_missing(request: Request, exc: object) -> object:
            if request.url.path.startswith("/api"):
                return JSONResponse(status_code=404, content={"detail": "Not Found"})
            return PlainTextResponse(_FRONTEND_MISSING, status_code=503)

    return app
