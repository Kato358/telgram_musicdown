"""试听路由（FR-PLAY-01~03）：请求预览 / 试听流。"""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from app.web.routes import schemas
from app.web.routes.context import RouteContext


def register(app: FastAPI, ctx: RouteContext) -> None:
    """注册试听路由。"""
    preview = ctx.preview

    @app.post("/api/preview")
    async def preview_ep(
        req: schemas.DownloadsRequest, _: None = Depends(ctx.check_session)
    ) -> dict[str, int]:
        refs = req.message_refs or []
        if not refs:
            raise HTTPException(status_code=422, detail="message_refs required")
        r = refs[0]
        pid = await preview.request_preview(
            int(r["chat_id"]), int(r["message_id"]), r.get("file_size")
        )
        return {"preview_id": pid}

    @app.get("/api/preview/{preview_id}/stream")
    async def preview_stream(
        preview_id: int, _: None = Depends(ctx.check_session)
    ) -> StreamingResponse:
        p = preview.stream_path(preview_id)
        # noqa 覆盖整语句：流式句柄非阻塞读
        return StreamingResponse(
            p.open("rb"),
            media_type="application/octet-stream",  # noqa: ASYNC230, ASYNC240
        )
