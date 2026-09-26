"""设置路由（FR-CFG-03）：settings 读写 / 模板预览 / 模板字段定义。

预览与字段文档共用同一首真实歌曲示例（SAMPLE_TRACK）：字段越全，
预览越接近实际落盘效果。示例字段说明的翻译在 Web 前端 i18n 里，后端只发字段名与示例值。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI

from app.appsettings import load_template_config, preview_max_bytes
from app.domain import TemplateConfig, TrackMeta, meta_from_dict
from app.services.path_builder import render_path, resolve_field
from app.services.preview import CacheStats
from app.web.routes import schemas
from app.web.routes.context import RouteContext

# 预览与模板字段文档共用的示例：一首真实歌曲的完整元数据。
# caption 故意带「艺术家 - 标题」首行与 Duration 行，能演示 FR-NAME-05 的回退正则。
SAMPLE_TRACK = TrackMeta(
    chat_id=-1001234567890,
    message_id=520,
    title="晴天",
    artist="周杰伦",
    album="叶惠美",
    track=3,
    duration_sec=269,
    file_size=10_757_120,
    bitrate=320,
    file_name="周杰伦 - 晴天.mp3",
    mime="audio/mpeg",
    caption="周杰伦 - 晴天\n2003 | 无损转制 | Duration: 04:29",
    message_date="2024-05-17T18:30:00+00:00",
    channel_title="华语经典音乐馆",
    ext="mp3",
    unique_id="AgACAgIAAxkCAAI",
)

# 模板字段文档顺序：常用在前，覆盖渲染器支持的全部字段（FR-NAME-02）。
TEMPLATE_FIELDS: tuple[str, ...] = (
    "title",
    "artist",
    "album",
    "track",
    "ext",
    "duration",
    "bitrate",
    "size",
    "date",
    "year",
    "channel",
    "channel_id",
    "message_id",
    "caption",
    "file_name",
    "unique_id",
)


def _one_line(text: str | None) -> str:
    """表格单元格用：压平空白，避免多行值（如 caption）撑爆布局。"""
    return " ".join(text.split()) if text else ""


def _stored_save_path(ctx: RouteContext) -> Path:
    """settings 表里的 save_path；缺省回退 base_dir/downloads（同 appsettings 约定）。"""
    raw = ctx.store.get_setting("save_path")
    return Path(raw) if raw else ctx.base_dir / "downloads"


def _cache_response(stats: CacheStats) -> schemas.CacheStatsResponse:
    """服务层 CacheStats → 响应契约（字段名不靠约定对齐，映射写在一处）。"""
    return schemas.CacheStatsResponse(
        total_bytes=stats.total_bytes,
        max_bytes=stats.max_bytes,
        preview_bytes=stats.preview_bytes,
        preview_count=stats.preview_count,
        cover_bytes=stats.cover_bytes,
        cover_count=stats.cover_count,
    )


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
        # 保存即生效（FR-CFG-03）：模板/落盘根与试听缓存现读现刷，不等重启；
        # 下载并发数绑定 worker 池规模，仍在下次启动生效。
        ctx.downloads.apply_template(load_template_config(store, ctx.base_dir))
        ctx.preview.max_bytes = preview_max_bytes(store)
        return store.all_settings()

    @app.get("/api/settings/cache")
    async def cache_usage(_: None = Depends(ctx.check_session)) -> schemas.CacheStatsResponse:
        """缓存占用（FR-PLAY-02）：试听按访问时间 LRU、封面按 mtime，共用一个字节上限。"""
        return _cache_response(await ctx.preview.cache_stats())

    @app.post("/api/settings/cache/clear")
    async def cache_clear(_: None = Depends(ctx.check_session)) -> schemas.CacheStatsResponse:
        """清空试听与封面缓存（FR-PLAY-02）：试听可重下、封面可重抓，返回清理后的占用。"""
        return _cache_response(await ctx.preview.clear_cache())

    @app.get("/api/settings/template-fields")
    async def template_fields(_: None = Depends(ctx.check_session)) -> dict[str, Any]:
        """模板字段文档：字段名 + 真实示例值（取自 SAMPLE_TRACK，含回退链效果）。"""
        cfg = TemplateConfig()
        fields = [
            {"field": name, "example": _one_line(resolve_field(SAMPLE_TRACK, name, cfg))}
            for name in TEMPLATE_FIELDS
        ]
        return {
            "fields": fields,
            "sample": {"artist": SAMPLE_TRACK.artist, "title": SAMPLE_TRACK.title},
        }

    @app.post("/api/settings/preview-path")
    async def preview_path(
        req: schemas.PreviewPathRequest, _: None = Depends(ctx.check_session)
    ) -> dict[str, str]:
        # 未传 sample 就用内置的真实歌曲示例；传了则按调用方字段渲染。
        meta = meta_from_dict(req.sample) if req.sample else SAMPLE_TRACK
        cfg = TemplateConfig(
            dir_template=req.dir_template,
            file_template=req.file_template,
            save_path=_stored_save_path(ctx),
        )
        path = render_path(meta, cfg)
        try:
            relative = path.relative_to(cfg.save_path).as_posix()
        except ValueError:  # 截断等极端情形拿不到相对路径，前端退化为整段显示
            relative = path.name
        return {"path": str(path), "root": str(cfg.save_path), "relative": relative}
