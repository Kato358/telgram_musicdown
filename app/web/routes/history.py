"""历史与播放路由（FR-PLAY-01~03）：历史列表 / 文件流 / 打开目录。"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse

from app.utils.platform import open_in_file_manager
from app.web.routes.context import RouteContext
from app.web.routes.presenters import history_dict

logger = logging.getLogger(__name__)


def register(app: FastAPI, ctx: RouteContext) -> None:
    """注册历史路由。"""
    store = ctx.store

    @app.get("/api/history")
    async def history_ep(
        status: str | None = None,
        source_id: int | None = None,
        q: str | None = None,
        page: int = 0,
        _: None = Depends(ctx.check_session),
    ) -> list[dict[str, Any]]:
        # `status` 可给逗号分隔的一组状态（下载页「进行中」页签 = queued,downloading,paused）
        rows = store.list_history(
            status=status, source_id=source_id, q=q, limit=50, offset=page * 50
        )
        # 每行带上它当前挂着的任务：下载页按 task_id 取实时读数、执行暂停/继续/取消
        ids = [int(h.id) for h in rows if h.id is not None]
        task_ids = store.latest_task_ids(ids)
        return [history_dict(h, task_ids.get(int(h.id or 0))) for h in rows]

    @app.get("/api/history/{history_id}/stream")
    async def history_stream(history_id: int, _: None = Depends(ctx.check_session)) -> FileResponse:
        h = store.get_history(history_id)
        if h is None or not h.save_path:
            raise HTTPException(status_code=404, detail="history not found")
        p = Path(h.save_path)
        if not p.exists():  # noqa: ASYNC240  路由级存在性检查非热路径
            raise HTTPException(status_code=404, detail="file missing")
        # FileResponse 自带 Range 支持（206）：播放器拖进度条按字节段取，
        # 不再整首重传，也避免媒体引擎反复开满连接挤占其他 API 请求
        return FileResponse(p, media_type="application/octet-stream")

    @app.post("/api/history/{history_id}/reveal")
    async def history_reveal(
        history_id: int, _: None = Depends(ctx.check_session)
    ) -> dict[str, bool]:
        """在系统文件管理器里打开这条记录的落盘目录（下载页行尾的「打开文件夹」）。"""
        h = store.get_history(history_id)
        if h is None or not h.save_path:
            raise HTTPException(status_code=404, detail="history not found")
        folder = Path(h.save_path).parent
        if not folder.exists():  # noqa: ASYNC240  路由级存在性检查非热路径
            raise HTTPException(status_code=404, detail="file missing")
        try:
            await asyncio.to_thread(open_in_file_manager, folder)
        except (OSError, subprocess.SubprocessError) as e:
            raise HTTPException(status_code=500, detail=f"cannot open folder: {e}") from e
        return {"ok": True}

    @app.delete("/api/history/{history_id}")
    async def history_delete(
        history_id: int, _: None = Depends(ctx.check_session)
    ) -> dict[str, bool]:
        """删除这条历史记录（FR-DL-06）：落盘文件保留，列表随即重取。

        记录还挂着在跑的任务（queued/downloading/paused）→ 先取消并删掉台账，
        不留一个永远不会动的「等待」；找不到记录 → 404。
        """
        h = store.get_history(history_id)
        if h is None:
            raise HTTPException(status_code=404, detail="history not found")
        task_id = store.latest_task_ids([history_id]).get(history_id)
        if task_id is not None and store.get_task(task_id) is not None:
            try:
                await ctx.downloads.delete_task(task_id)
            except Exception:  # noqa: BLE001  台账清理失败不阻断记录删除
                logger.warning("cleanup of task %s for history %s failed", task_id, history_id)
        store.delete_history(history_id)
        return {"ok": True}
