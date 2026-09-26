"""音乐源路由（FR-SRC-01~05）：增删改查 / 候选源。"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, HTTPException

from app.errors import AppError
from app.utils.linkparse import parse_link
from app.web.routes import schemas
from app.web.routes.context import RouteContext
from app.web.routes.presenters import candidate_dict, src_dict


def register(app: FastAPI, ctx: RouteContext) -> None:
    """注册音乐源路由。"""
    store, sources, tg = ctx.store, ctx.sources, ctx.tg

    @app.get("/api/sources")
    async def list_sources(_: None = Depends(ctx.check_session)) -> list[dict[str, Any]]:
        return [src_dict(s) for s in sources.list_sources()]

    @app.post("/api/sources")
    async def add_source(
        req: schemas.SourceUpsertRequest, _: None = Depends(ctx.check_session)
    ) -> dict[str, Any]:
        """添加源（FR-SRC-01）：只加源，不做任何拉取——入库由搜索/粘贴链接按需触发。"""
        src = await sources.add_source(parse_link(req.link))
        return src_dict(src)

    @app.put("/api/sources/{source_id}")
    async def update_source(
        source_id: int, req: schemas.SourceUpdateRequest, _: None = Depends(ctx.check_session)
    ) -> dict[str, Any]:
        """编辑源（FR-SRC-03）：今天只有启用开关。"""
        existing = store.get_source(source_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="source not found")
        if req.enabled is not None:
            existing.enabled = int(req.enabled)
        return src_dict(sources.update_source(existing))

    @app.delete("/api/sources/{source_id}")
    async def delete_source(
        source_id: int, with_history: bool = False, _: None = Depends(ctx.check_session)
    ) -> dict[str, bool]:
        sources.delete_source(source_id, with_history)
        # 源没了，它名下的搜索缓存条目也跟着没了意义（键里带 source_id）
        await ctx.search.invalidate_source(source_id)
        return {"ok": True}

    @app.get("/api/sources/discover")
    async def discover(_: None = Depends(ctx.check_session)) -> dict[str, Any]:
        """候选源（FR-SRC-05）：账号对话中标题/用户名含音乐关键词、尚未添加的。"""
        if not tg.authorized:
            raise AppError("not_connected", "先完成 Telegram 登录，才能扫你的会话")
        items = await sources.discover_candidates()
        return {"items": [candidate_dict(c) for c in items]}
