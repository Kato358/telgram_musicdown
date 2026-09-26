"""搜索路由（FR-SEARCH-01~04）：可搜来源清单 + 关键词搜索。

「可搜来源」是音乐源频道与在线源平台的**同一份清单**（SDD §2.7）：scope 唯一、
接口一致，界面不必为在线源单开一套选择器。单独开这个端点而不是并进
``GET /api/sources``，是因为那张表是「可增删改的音乐源」，在线源不在其列——
混进去会让音乐源页出现删不掉的行。
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI

from app.domain import card_to_dict
from app.ports.music import is_online
from app.web.routes import schemas
from app.web.routes.context import RouteContext


def _source_dicts(ctx: RouteContext) -> list[dict[str, Any]]:
    """来源索引 → 界面要的来源行（scope 即前端勾选用的 id）。"""
    return [
        {
            "id": target.scope_id,
            "title": target.label,
            "provider": target.provider,
            "online": is_online(target.provider),
        }
        for target in ctx.registry.targets()
    ]


def register(app: FastAPI, ctx: RouteContext) -> None:
    """注册搜索路由。"""
    search = ctx.search

    @app.get("/api/search/sources")
    async def search_sources(_: None = Depends(ctx.check_session)) -> dict[str, Any]:
        """当前可搜的来源：启用的音乐源频道 + 已开启的在线源平台。"""
        return {"sources": _source_dicts(ctx)}

    @app.post("/api/search")
    async def search_ep(
        req: schemas.SearchRequest, _: None = Depends(ctx.check_session)
    ) -> dict[str, Any]:
        resp = await search.search(
            req.q,
            req.source_ids,
            page=req.page,
            page_size=req.page_size,
            fields=req.fields,
            filters=req.filters,
            sort=req.sort,
            refresh=req.refresh,
        )
        return {
            "results": [card_to_dict(c) for c in resp.results],
            "meta": resp.meta,
        }
