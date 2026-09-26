"""全局封面路由：一条链路服务所有入口——本地标签 > api.lrc.cx > 无封面。

搜索结果行、下载历史行、播放器队列共用 ``GET /api/cover``：
1. 本地：按标题找最近一条已落盘的成功记录，读文件的内嵌封面（ETag/304）；
2. api：按歌名/歌手查公共封面接口（磁盘缓存 + 失败负缓存，见 PreviewService.cover_path）；
3. 都没有 → 404，前端退回音符占位（封面是纯装饰）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from app.services.tags import TagService, image_media_type
from app.web.routes.context import RouteContext


async def _local_cover_response(
    history_id: int | None, request: Request, audio: Path, tags: TagService
) -> Response | None:
    """本地文件的标签内嵌封面：命中带 ETag，浏览器带 If-None-Match 重访直接 304。

    mutagen 在 to_thread 里读（APIC / FLAC pictures / MP4 covr）；没有内嵌封面 → None，
    调用方回退 api.lrc.cx。
    """
    stat = audio.stat()  # noqa: ASYNC240  路由级存在性检查非热路径
    etag = f'"{history_id}-{stat.st_size}-{int(stat.st_mtime)}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    data = await asyncio.to_thread(tags.read_cover, audio)
    if not data:
        return None
    return Response(
        data,
        media_type=image_media_type(data) or "image/jpeg",
        headers={"ETag": etag},
    )


def register(app: FastAPI, ctx: RouteContext) -> None:
    """注册全局封面路由。"""
    store = ctx.store
    preview = ctx.preview
    tags = ctx.tags

    @app.get("/api/cover")
    async def cover_ep(
        request: Request,
        title: str | None = None,
        artist: str | None = None,
        _: None = Depends(ctx.check_session),
    ) -> Response:
        """全局封面（本地 > api > 无封面）：前端所有封面入口共用这一条链路。

        title/artist 都缺省时无可查依据 → 404；本地命中后文件/标签损坏按没有封面处理。
        """
        if title:
            row = store.find_history_cover_source(title)
            if row is not None and row.save_path:
                audio = Path(row.save_path)
                if audio.exists():  # noqa: ASYNC240  路由级存在性检查非热路径
                    try:
                        cover = await _local_cover_response(row.id, request, audio, tags)
                    except Exception:  # noqa: BLE001  封面是装饰，读失败回退 api
                        cover = None
                    if cover is not None:
                        return cover
        try:
            path = await preview.cover_path(title, artist)
        except Exception:  # noqa: BLE001  封面是装饰，失败退回 404（前端有占位）
            raise HTTPException(status_code=404, detail="no cover") from None
        if path is None or not path.exists():  # noqa: ASYNC240  路由级存在性检查非热路径
            raise HTTPException(status_code=404, detail="no cover")
        return StreamingResponse(path.open("rb"), media_type="image/jpeg")  # noqa: ASYNC230
