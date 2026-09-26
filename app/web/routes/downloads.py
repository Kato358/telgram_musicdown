"""下载任务路由（FR-DL-01~06）：入队 / 列表 / 暂停恢复取消 / 重试 / 删除。"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, HTTPException

from app.domain import DownloadRequest, meta_from_dict
from app.errors import AppError
from app.utils.linkparse import parse_link
from app.web.routes import schemas
from app.web.routes.context import RouteContext
from app.web.routes.presenters import task_dict


def register(app: FastAPI, ctx: RouteContext) -> None:
    """注册下载任务路由。"""
    store, downloads = ctx.store, ctx.downloads

    @app.post("/api/downloads")
    async def create_downloads(
        req: schemas.DownloadsRequest, _: None = Depends(ctx.check_session)
    ) -> dict[str, Any]:
        """入队（FR-LINK-01/02、FR-DL-01）：urls 逐条解析，message_refs 直接建任务。

        message_refs 里带的元数据（title/artist/ext…）原样进 TrackMeta，
        搜索结果直接下载时模板才有真实字段可用。
        """
        out: list[dict[str, Any]] = []
        refs: list[dict[str, Any]] = []
        if req.message_refs:
            refs.extend(req.message_refs)
        if req.urls:
            for url in req.urls:
                try:
                    link = parse_link(url)
                except AppError as e:
                    out.append({"url": url, "error": e.message})
                    continue
                if link.chat_id is None or link.message_id is None:
                    out.append({"url": url, "error": "link needs chat and message id"})
                    continue
                refs.append({"chat_id": link.chat_id, "message_id": link.message_id})
        for ref in refs:
            meta = meta_from_dict(ref)
            task_id = await downloads.enqueue(DownloadRequest(meta=meta, force=req.force))
            out.append({"chat_id": meta.chat_id, "message_id": meta.message_id, "task_id": task_id})
        return {"items": out}

    @app.get("/api/downloads")
    async def list_downloads(
        status: str | None = None, page: int = 0, _: None = Depends(ctx.check_session)
    ) -> list[dict[str, Any]]:
        tasks = store.list_tasks(status=status, limit=50)
        out: list[dict[str, Any]] = []
        for task in tasks:
            history = store.get_history(task.history_id) if task.history_id else None
            out.append(task_dict(task, history))
        return out

    @app.post("/api/downloads/{task_id}/cancel")
    async def cancel_download(
        task_id: int, _: None = Depends(ctx.check_session)
    ) -> dict[str, bool]:
        await downloads.cancel_task(task_id)
        return {"ok": True}

    @app.post("/api/downloads/{task_id}/pause")
    async def pause_download(task_id: int, _: None = Depends(ctx.check_session)) -> dict[str, bool]:
        await downloads.pause_task(task_id)
        return {"ok": True}

    @app.post("/api/downloads/{task_id}/resume")
    async def resume_download(
        task_id: int, _: None = Depends(ctx.check_session)
    ) -> dict[str, bool]:
        await downloads.resume_task(task_id)
        return {"ok": True}

    @app.post("/api/downloads/{task_id}/retry")
    async def retry_download(task_id: int, _: None = Depends(ctx.check_session)) -> dict[str, bool]:
        if store.get_task(task_id) is None:
            raise HTTPException(status_code=404, detail="task not found")
        await downloads.retry_task(task_id)
        return {"ok": True}

    @app.delete("/api/downloads/{task_id}")
    async def delete_download(
        task_id: int, _: None = Depends(ctx.check_session)
    ) -> dict[str, bool]:
        if store.get_task(task_id) is None:
            raise HTTPException(status_code=404, detail="task not found")
        await downloads.delete_task(task_id)
        return {"ok": True}

    @app.post("/api/downloads/retry-failed")
    async def retry_failed(_: None = Depends(ctx.check_session)) -> dict[str, int]:
        return {"retried": await downloads.retry_failed()}
