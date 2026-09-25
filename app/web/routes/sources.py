"""音乐源路由（FR-SRC-01~05）：增删改查 / 初始导入 / 回溯 / 候选源。"""

from __future__ import annotations

import json
from typing import Any

from fastapi import Depends, FastAPI, HTTPException

from app.db.models import Task
from app.errors import AppError
from app.events import Event
from app.services.sync import INITIAL_IMPORT_LIMIT
from app.utils.linkparse import parse_link
from app.web.routes import schemas
from app.web.routes.context import RouteContext
from app.web.routes.presenters import candidate_dict, src_dict


def _enqueue_sync(store: Any, source_id: int, **payload: Any) -> int:
    """建一条 sync 任务（FR-SRC-04）：由下载 Worker 池执行，Web 请求不阻塞。"""
    return store.create_task(
        Task(
            id=None,
            type="sync",
            payload_json=json.dumps({"source_id": source_id, **payload}, ensure_ascii=False),
        )
    )


def register(app: FastAPI, ctx: RouteContext) -> None:
    """注册音乐源路由。"""
    store, sources, events, tg = ctx.store, ctx.sources, ctx.events, ctx.tg

    @app.get("/api/sources")
    async def list_sources(_: None = Depends(ctx.check_session)) -> list[dict[str, Any]]:
        return [src_dict(s) for s in sources.list_sources()]

    @app.post("/api/sources")
    async def add_source(
        req: schemas.SourceUpsertRequest, _: None = Depends(ctx.check_session)
    ) -> dict[str, Any]:
        """添加源（FR-SRC-01）+ 一次初始导入（最近 200 条，向导第 3 步的承诺）。"""
        src = await sources.add_source(parse_link(req.link))
        task_id = _enqueue_sync(
            store,
            int(src.id or 0),
            direction="backward",
            limit=INITIAL_IMPORT_LIMIT,
            initial=True,
        )
        await events.publish(
            Event("task.status", {"task_id": task_id, "status": "queued", "error": None})
        )
        return {**src_dict(src), "import_task_id": task_id}

    @app.put("/api/sources/{source_id}")
    async def update_source(
        source_id: int, req: schemas.SourceUpdateRequest, _: None = Depends(ctx.check_session)
    ) -> dict[str, Any]:
        existing = store.get_source(source_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="source not found")
        if req.enabled is not None:
            existing.enabled = int(req.enabled)
        if req.auto_sync is not None:
            existing.auto_sync = int(req.auto_sync)
        if req.sync_interval_sec is not None:
            existing.sync_interval_sec = req.sync_interval_sec
        if req.media_scope is not None:
            existing.media_scope = json.dumps(req.media_scope, ensure_ascii=False)
        if req.filters is not None:
            existing.filters_json = json.dumps(req.filters, ensure_ascii=False)
        existing.save_path_override = req.save_path_override
        existing.dir_template_override = req.dir_template_override
        existing.file_template_override = req.file_template_override
        existing.note = req.note
        return src_dict(sources.update_source(existing))

    @app.delete("/api/sources/{source_id}")
    async def delete_source(
        source_id: int, with_history: bool = False, _: None = Depends(ctx.check_session)
    ) -> dict[str, bool]:
        sources.delete_source(source_id, with_history)
        return {"ok": True}

    @app.post("/api/sources/{source_id}/backfill")
    async def backfill(
        source_id: int, req: schemas.BackfillRequest, _: None = Depends(ctx.check_session)
    ) -> dict[str, int]:
        """历史回溯（FR-SRC-04）：交给 sync 任务；方向与锚点原样入 payload。"""
        if store.get_source(source_id) is None:
            raise HTTPException(status_code=404, detail="source not found")
        task_id = _enqueue_sync(
            store,
            source_id,
            direction=req.direction,
            limit=req.limit,
            to_message_id=req.to_message_id,
            to_date=req.to_date,
        )
        await events.publish(
            Event("task.status", {"task_id": task_id, "status": "queued", "error": None})
        )
        return {"task_id": task_id}

    @app.get("/api/sources/discover")
    async def discover(_: None = Depends(ctx.check_session)) -> dict[str, Any]:
        """候选源（FR-SRC-05）：账号对话中标题/用户名含音乐关键词、尚未添加的。"""
        if not tg.authorized:
            raise AppError("not_connected", "先完成 Telegram 登录，才能扫你的会话")
        items = await sources.discover_candidates()
        return {"items": [candidate_dict(c) for c in items]}
