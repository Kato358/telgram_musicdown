"""设置路由（FR-CFG-03）：settings 读写 / 模板预览。"""

from __future__ import annotations

import json

from fastapi import Depends, FastAPI

from app.domain import TemplateConfig, TrackMeta
from app.services.path_builder import render_path
from app.web.routes import schemas
from app.web.routes.context import RouteContext


def register(app: FastAPI, ctx: RouteContext) -> None:
    """注册设置路由。"""
    store = ctx.store

    @app.get("/api/settings")
    async def get_settings(_: None = Depends(ctx.check_session)) -> dict[str, str]:
        return store.all_settings()

    @app.put("/api/settings")
    async def put_settings(
        req: schemas.SettingsUpdateRequest, _: None = Depends(ctx.check_session)
    ) -> dict[str, str]:
        for k, v in req.values.items():
            store.set_setting(k, json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v)
        return store.all_settings()

    @app.post("/api/settings/preview-path")
    async def preview_path(
        req: schemas.PreviewPathRequest, _: None = Depends(ctx.check_session)
    ) -> dict[str, str]:
        sample = req.sample or {}
        meta = TrackMeta(
            chat_id=int(sample.get("chat_id", -100)),
            message_id=int(sample.get("message_id", 1)),
            title=sample.get("title", "Sample Title"),
            artist=sample.get("artist", "Sample Artist"),
            album=sample.get("album"),
            track=sample.get("track"),
            ext=sample.get("ext", "mp3"),
        )
        cfg = TemplateConfig(dir_template=req.dir_template, file_template=req.file_template)
        return {"path": str(render_path(meta, cfg))}
