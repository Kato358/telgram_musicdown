"""搜索路由（FR-SEARCH-01~04）：已启用源内关键词搜索。"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI

from app.domain import card_to_dict
from app.web.routes import schemas
from app.web.routes.context import RouteContext


def register(app: FastAPI, ctx: RouteContext) -> None:
    """注册搜索路由。"""
    search = ctx.search

    @app.post("/api/search")
    async def search_ep(
        req: schemas.SearchRequest, _: None = Depends(ctx.check_session)
    ) -> dict[str, Any]:
        resp = await search.search(req.q, req.source_ids, page=req.page)
        return {
            "results": [card_to_dict(c) for c in resp.results],
            "meta": resp.meta,
        }
