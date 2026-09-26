"""统计路由（`GET /api/stats`）：控制台统计卡与系统状态的唯一数据源。"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from fastapi import Depends, FastAPI

from app.web.routes import schemas
from app.web.routes.context import RouteContext


def _disk_bytes(paths: list[str]) -> int:
    """已入库文件的磁盘占用；文件不在磁盘上（被移走、被删）就不算——占用是事实，不是账目。"""
    total = 0
    for raw in paths:
        try:
            total += Path(raw).stat().st_size
        except OSError:
            continue
    return total


def register(app: FastAPI, ctx: RouteContext) -> None:
    """注册统计路由。"""

    @app.get("/api/stats")
    async def stats_ep(_: None = Depends(ctx.check_session)) -> schemas.StatsResponse:
        """控制台统计：统计卡与系统状态的唯一数据源（uptime 自应用装配起算）。

        曲库占用去磁盘上量：DB 里的 `file_size` 在链接/转发入队时经常是空的，
        拿它求和会让「占用」永远显示 0。逐个 stat 放线程里做，别堵事件循环。
        """
        counts = ctx.store.counts()
        local = ctx.store.count_local_tracks()
        return schemas.StatsResponse(
            tasks=schemas.TaskCounts(
                queued=counts["tasks_queued"],
                downloading=counts["tasks_downloading"],
                paused=counts["tasks_paused"],
                failed=counts["tasks_failed"],
                success=counts["tasks_success"],
            ),
            library=schemas.LibraryCounts(
                tracks=counts["library_tracks"],
                bytes=await asyncio.to_thread(_disk_bytes, ctx.store.library_paths()),
                failed=counts["library_failed"],
                # 本地曲库台账读数：侧栏曲库卡与曲库页同一套数字（SUM 比逐文件 stat 便宜）
                local_present=local["present"],
                local_missing=local["missing"],
                local_bytes=ctx.store.local_track_bytes(),
            ),
            sources=schemas.SourceCounts(
                total=counts["sources_total"], enabled=counts["sources_enabled"]
            ),
            uptime_sec=round(time.monotonic() - ctx.started_at, 1),
        )
