"""路由模块共享上下文（SRP）：路由工厂只传一个 Context。

路由模块之间互不引用；它们只依赖本 Context 与服务接口（DIP）。
``presenters.py`` 是纯序列化函数（dict 组装），无副作用、可单测。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from app.events import EventBus
from app.ports import IStore
from app.services.download import DownloadService
from app.services.preview import PreviewService
from app.services.source import SearchService, SourceService
from app.services.tags import TagService
from app.telegram.manager import TelegramManager
from app.web import auth as web_auth


@dataclass(slots=True)
class RouteContext:
    """路由共享上下文：服务句柄 + 认证依赖 + 应用级配置。"""

    #: 来源索引（SDD §2.7）：搜索/下载/试听共用；设置保存时就地刷新在线源开关与档位。
    registry: Any
    store: IStore
    events: EventBus
    downloads: DownloadService
    sources: SourceService
    search: SearchService
    preview: PreviewService
    library: Any  # LocalLibraryService（None = 未装配，曲库路由退 503）
    tg: TelegramManager
    base_dir: Path
    web_host: str
    web_login_secret: str
    # 控制台登录总开关（FR-WEB-02）：False = 全放行（含 check_session）
    web_login_enabled: bool = True
    # 音频标签读写：封面/曲库路由直接用，不再穿到 downloads.tags 拿服务内部件
    tags: TagService = field(default_factory=TagService)
    static_dir: Path | None = None
    started_at: float = field(default_factory=time.monotonic)

    def check_session(self, request: Request) -> None:
        """认证依赖（FR-WEB-01/02）：豁免判定 + 会话 cookie 校验。"""
        if not web_auth.auth_required(self.web_host, self.web_login_secret, self.web_login_enabled):
            return
        token = request.cookies.get(web_auth.SESSION_COOKIE, "")
        if not web_auth.verify_session_token(token, self.web_login_secret):
            raise HTTPException(status_code=401, detail="unauthorized")


def register_all(app: FastAPI, ctx: RouteContext) -> None:  # noqa: PLC0415  模块注册函数
    """按资源注册全部路由模块（每个模块一个 register 函数，SRP）。"""
    from app.web.routes import (  # noqa: PLC0415
        auth,
        cover,
        downloads,
        history,
        library,
        logs,
        lyrics,
        preview,
        search,
        settings,
        setup,
        sources,
        stats,
    )

    auth.register(app, ctx)
    setup.register(app, ctx)
    stats.register(app, ctx)
    sources.register(app, ctx)
    downloads.register(app, ctx)
    history.register(app, ctx)
    preview.register(app, ctx)
    settings.register(app, ctx)
    search.register(app, ctx)
    logs.register(app, ctx)
    lyrics.register(app, ctx)
    cover.register(app, ctx)
    library.register(app, ctx)
