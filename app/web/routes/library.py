"""本地曲库路由（FR-LIB）：扫描索引的列表 / 播放 / 封面 / 重扫 / 移除记录。

数据源是 local_tracks 台账（启动与手动触发的扫描写入）：文件被删后记录仍在
（missing=1），曲库继续可见；还挂着下载记录的行可以一键重新入队。
列表端点在服务端做筛选（关键词 / 歌手 / 在库状态）、排序（白名单）与分页——
曲库上万首时前端也只拉屏上那一页（懒加载）。
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, Response

from app.db.models import History, LocalTrack
from app.services.tags import image_media_type
from app.utils.platform import open_in_file_manager
from app.web.routes.context import RouteContext
from app.web.routes.presenters import local_track_dict

logger = logging.getLogger(__name__)

SORT_KEYS = {"title", "artist", "album", "duration", "size", "bitrate", "created"}
MAX_LIMIT = 200


def register(app: FastAPI, ctx: RouteContext) -> None:
    """注册本地曲库路由：列表/扫描归列表组，流/封面/目录/删除归单行组。"""
    store, library = ctx.store, ctx.library
    tags = ctx.tags

    root = Path(library.save_path)

    def _track_or_404(track_id: int) -> LocalTrack:
        track = store.get_local_track(track_id)
        if track is None:
            raise HTTPException(status_code=404, detail="track not found")
        return track

    def _existing_file(track: LocalTrack) -> Path:
        """台账行 → 在磁盘上的绝对路径；记录标了 missing 或文件不在 → 404。"""
        if track.missing:
            raise HTTPException(status_code=404, detail="file missing")
        path = root / track.rel_path
        if not path.exists():  # noqa: ASYNC240  路由级存在性检查非热路径
            raise HTTPException(status_code=404, detail="file missing")
        return path

    @app.get("/api/local-library")
    async def library_list(
        q: str | None = None,
        artist: str | None = None,
        missing: str | None = None,
        sort: str = "created",
        order: str = "desc",
        limit: int = 50,
        offset: int = 0,
        _: None = Depends(ctx.check_session),
    ) -> dict[str, Any]:
        """曲库列表：服务端筛选 + 排序 + 分页（懒加载每次一页）。

        counts/bytes 是全局读数（不随当前页变化）；total 是当前筛选下的行数。
        """
        limit = max(1, min(limit, MAX_LIMIT))
        offset = max(0, offset)
        missing_filter: bool | None = None
        if missing == "1":
            missing_filter = True
        elif missing == "0":
            missing_filter = False
        keyword = q.strip() if q else None
        singer = artist.strip() if artist else None
        rows = store.list_local_tracks(
            q=keyword,
            artist=singer,
            missing=missing_filter,
            sort=sort if sort in SORT_KEYS else "created",
            order=order if order in ("asc", "desc") else "desc",
            limit=limit,
            offset=offset,
        )
        counts = store.count_local_tracks(q=keyword, artist=singer)
        total = counts["all"]
        if missing_filter is not None:
            total = counts["missing"] if missing_filter else counts["present"]
        # 本页挂着下载记录的行带上 chat/message（「重新下载」按消息入队用）
        history_rows: dict[int, History] = {}
        for row in rows:
            if row.history_id is not None and row.history_id not in history_rows:
                found = store.get_history(row.history_id)
                if found is not None:
                    history_rows[row.history_id] = found
        return {
            "items": [local_track_dict(row, history_rows.get(row.history_id or 0)) for row in rows],
            "total": total,
            "counts": counts,
            "bytes": store.local_track_bytes(),
        }

    @app.post("/api/local-library/scan")
    async def library_scan(_: None = Depends(ctx.check_session)) -> dict[str, bool]:
        """触发一次后台重扫；扫描中再触发按忽略处理（结果经 SSE library.scan 推送）。"""
        asyncio.create_task(_scan_quietly())
        return {"ok": True}

    async def _scan_quietly() -> None:
        try:
            await library.rescan()
        except Exception:  # noqa: BLE001  手动扫描失败只记日志，不影响请求方
            logger.exception("manual library scan failed")

    @app.get("/api/local-library/artists")
    async def library_artists(_: None = Depends(ctx.check_session)) -> list[dict[str, Any]]:
        """在库歌手聚合（筛选下拉）。"""
        return [{"artist": name, "count": count} for name, count in store.local_track_artists()]

    _register_track_routes(app, ctx, _track_or_404, _existing_file, tags)


def _register_track_routes(
    app: FastAPI,
    ctx: RouteContext,
    track_or_404: Any,
    existing_file: Any,
    tags: Any,
) -> None:
    """单行端点：播放流 / 封面 / 打开目录 / 移除记录（依赖列表组的定位辅助）。"""
    root = Path(ctx.library.save_path)

    @app.get("/api/local-library/{track_id}/stream")
    async def library_stream(track_id: int, _: None = Depends(ctx.check_session)) -> FileResponse:
        """曲库文件流（FileResponse 自带 Range 206，播放器拖进度条按字节段取）。"""
        path = existing_file(track_or_404(track_id))
        return FileResponse(path, media_type="application/octet-stream")

    @app.get("/api/local-library/{track_id}/cover")
    async def library_cover(track_id: int, _: None = Depends(ctx.check_session)) -> Response:
        """曲库封面：本文件内嵌封面 → api.lrc.cx 兜底 → 404（前端退音符占位）。"""
        track = track_or_404(track_id)
        if not track.missing:
            path = root / track.rel_path
            if path.exists():  # noqa: ASYNC240  路由级存在性检查非热路径
                try:
                    data = await asyncio.to_thread(tags.read_cover, path)
                except Exception:  # noqa: BLE001  封面是装饰，读失败走 api 兜底
                    data = None
                if data:
                    return Response(
                        data,
                        media_type=image_media_type(data) or "image/jpeg",
                        headers={"Cache-Control": "max-age=86400"},
                    )
        try:
            fallback = await ctx.preview.cover_path(track.title, track.artist)
        except Exception:  # noqa: BLE001  封面是装饰，失败退 404
            raise HTTPException(status_code=404, detail="no cover") from None
        if fallback is None or not fallback.exists():  # noqa: ASYNC240
            raise HTTPException(status_code=404, detail="no cover")
        # 封面磁盘缓存图很小，整读直出
        return Response(
            fallback.read_bytes(),  # noqa: ASYNC230
            media_type="image/jpeg",
            headers={"Cache-Control": "max-age=86400"},
        )

    @app.post("/api/local-library/{track_id}/reveal")
    async def library_reveal(
        track_id: int, _: None = Depends(ctx.check_session)
    ) -> dict[str, bool]:
        """在系统文件管理器里打开这条曲库记录所在的目录。"""
        path = existing_file(track_or_404(track_id))
        try:
            await asyncio.to_thread(open_in_file_manager, path.parent)
        except (OSError, subprocess.SubprocessError) as e:
            raise HTTPException(status_code=500, detail=f"cannot open folder: {e}") from e
        return {"ok": True}

    @app.delete("/api/local-library/{track_id}")
    async def library_delete(
        track_id: int, _: None = Depends(ctx.check_session)
    ) -> dict[str, bool]:
        """移除这条曲库记录（磁盘文件不动）；文件还在的话下次扫描会重新入库。"""
        if not ctx.store.delete_local_track(track_id):
            raise HTTPException(status_code=404, detail="track not found")
        return {"ok": True}
