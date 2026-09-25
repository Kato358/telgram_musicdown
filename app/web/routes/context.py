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

from app.ports import IStore
from app.services.download import DownloadService
from app.services.preview import PreviewService
from app.services.source import SearchService, SourceService
from app.web import auth as web_auth


@dataclass(slots=True)
class RouteContext:
    """路由共享上下文：服务句柄 + 认证依赖 + 应用级配置。"""

    store: IStore
    events: Any  # EventBus（避免 web 层 import services 之外的实现细节）
    downloads: DownloadService
    sources: SourceService
    search: SearchService
    preview: PreviewService
    tg: Any  # TelegramManager（协议面：路由只用其生命周期方法）
    base_dir: Path
    web_host: str
    web_login_secret: str
    static_dir: Path | None = None
    started_at: float = field(default_factory=time.monotonic)

    def check_session(self, request: Request) -> None:
        """认证依赖（FR-WEB-01/02）：豁免判定 + 会话 cookie 校验。"""
        if not web_auth.auth_required(self.web_host, self.web_login_secret):
            return
        token = request.cookies.get(web_auth.SESSION_COOKIE, "")
        if not web_auth.verify_session_token(token, self.web_login_secret):
            raise HTTPException(status_code=401, detail="unauthorized")


def register_all(app: FastAPI, ctx: RouteContext) -> None:  # noqa: PLC0415  模块注册函数
    """按资源注册全部路由模块（每个模块一个 register 函数，SRP）。"""
    from app.web.routes import (  # noqa: PLC0415
        auth,
        downloads,
        history,
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
