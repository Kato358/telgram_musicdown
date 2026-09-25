"""历史与播放路由（FR-PLAY-01~03）：历史列表 / 文件流 / 打开目录。"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from app.web.routes.context import RouteContext
from app.web.routes.presenters import history_dict

logger = logging.getLogger(__name__)


def _open_in_file_manager(folder: Path) -> None:
    """用系统文件管理器打开目录（Windows / macOS / Linux 各自的原生方式）。"""
    if sys.platform == "win32":
        os.startfile(folder)  # type: ignore[attr-defined]  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.run(["/usr/bin/open", str(folder)], check=True)  # noqa: S603
    else:
        subprocess.run(["/usr/bin/xdg-open", str(folder)], check=True)  # noqa: S603


def register(app: FastAPI, ctx: RouteContext) -> None:
    """注册历史路由。"""
    store = ctx.store
    preview = ctx.preview

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
    async def history_stream(
        history_id: int, _: None = Depends(ctx.check_session)
    ) -> StreamingResponse:
        h = store.get_history(history_id)
        if h is None or not h.save_path:
            raise HTTPException(status_code=404, detail="history not found")
        p = Path(h.save_path)
        if not p.exists():  # noqa: ASYNC240  路由级存在性检查非热路径
            raise HTTPException(status_code=404, detail="file missing")
        # 流式句柄惰性打开，非阻塞下载（noqa 覆盖整语句）
        return StreamingResponse(
            p.open("rb"),  # noqa: ASYNC230, ASYNC240
            media_type="application/octet-stream",
        )

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
            await asyncio.to_thread(_open_in_file_manager, folder)
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

    @app.get("/api/history/{history_id}/cover")
    async def history_cover(
        history_id: int, _: None = Depends(ctx.check_session)
    ) -> StreamingResponse:
        """这条记录的封面（下载页行首图标；Telegram 内嵌缩略图按需缓存）。

        没有封面或下载失败 → 404，前端退回音符占位（封面是纯装饰）。
        """
        h = store.get_history(history_id)
        if h is None:
            raise HTTPException(status_code=404, detail="history not found")
        try:
            path = await preview.thumb_path(h.chat_id, h.message_id)
        except Exception:  # noqa: BLE001  封面是装饰，失败退回 404（前端有占位）
            raise HTTPException(status_code=404, detail="no cover") from None
        if path is None or not path.exists():  # noqa: ASYNC240  路由级检查非热路径
            raise HTTPException(status_code=404, detail="no cover")
        return StreamingResponse(path.open("rb"), media_type="image/jpeg")  # noqa: ASYNC230
