"""搜索路由（FR-SEARCH-01~04）：已启用源内关键词搜索 / 搜索结果封面。"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from app.domain import card_to_dict
from app.web.routes import schemas
from app.web.routes.context import RouteContext


def register(app: FastAPI, ctx: RouteContext) -> None:
    """注册搜索路由。"""
    search = ctx.search
    preview = ctx.preview

    @app.post("/api/search")
    async def search_ep(
        req: schemas.SearchRequest, _: None = Depends(ctx.check_session)
    ) -> dict[str, Any]:
        resp = await search.search(req.q, req.source_ids, page=req.page)
        return {
            "results": [card_to_dict(c) for c in resp.results],
            "meta": resp.meta,
        }

    @app.get("/api/search/cover")
    async def search_cover(
        chat_id: int, message_id: int, _: None = Depends(ctx.check_session)
    ) -> StreamingResponse:
        """搜索结果行首的封面（Telegram 内嵌缩略图按需缓存，与下载页封面同一条链路）。

        没有封面或下载失败 → 404，前端退回音符占位（封面是纯装饰）。
        """
        try:
            path = await preview.thumb_path(chat_id, message_id)
        except Exception:  # noqa: BLE001  封面是装饰，失败退回 404（前端有占位）
            raise HTTPException(status_code=404, detail="no cover") from None
        if path is None or not path.exists():  # noqa: ASYNC240  路由级存在性检查非热路径
            raise HTTPException(status_code=404, detail="no cover")
        return StreamingResponse(path.open("rb"), media_type="image/jpeg")  # noqa: ASYNC230
