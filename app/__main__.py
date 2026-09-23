"""入口：装配、启动 FastAPI + TG 客户端（SDD §1.1，FR-OPS-01）。

单进程：uvicorn 与 Pyrogram 共享同一 asyncio loop，避免双 loop（SDD §1.3）。
启动顺序：目录 → 迁移 → 恢复（NFR-05）→ 服务装配 → web/tg 启动。
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.config import SecretConfig, app_dirs, load_secrets, web_dist_dir
from app.db.store import Store
from app.domain import TemplateConfig
from app.errors import SessionLockedError, WebAuthConfigError
from app.events import EventBus
from app.services.download import DownloadService
from app.services.preview import PreviewService
from app.services.source import SearchService, SourceService
from app.services.sync import SyncRunner
from app.telegram.manager import TelegramManager
from app.web import auth as web_auth
from app.web.routes import create_app


def setup_logging(logs_dir: Path, level: int = logging.INFO) -> None:
    """统一 logging；logs/ 滚动 10MB × 5（编码规范 §2.5）。"""
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    root = logging.getLogger()
    root.setLevel(level)
    handler = RotatingFileHandler(
        logs_dir / "app.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    handler.setFormatter(fmt)
    root.addHandler(handler)
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(fmt)
    root.addHandler(console)


@dataclass(slots=True)
class AppServices:
    """装配后的服务容器（供 web/tg 与测试共用）。"""

    dirs: dict[str, Path]
    secrets: SecretConfig
    store: Store
    events: EventBus
    template: TemplateConfig
    sources: SourceService
    search: SearchService
    downloads: DownloadService
    preview: PreviewService
    tg: TelegramManager


def build_services(base_dir: Path) -> AppServices:
    """装配全部服务；返回依赖容器（供 web/tg 与测试共用）。"""
    dirs = app_dirs(base_dir)
    secrets = load_secrets(base_dir)
    web_auth.check_auth_config(secrets.web_host, secrets.web_login_secret)
    store = Store(dirs["data"] / "app.db")
    events = EventBus()
    template = TemplateConfig(
        dir_template=store.get_setting("dir_template", "{artist}/{album}") or "{artist}/{album}",
        file_template=store.get_setting("file_template", "{track:02d} {title}")
        or "{track:02d} {title}",
        date_format=store.get_setting("date_format", "%Y-%m") or "%Y-%m",
        save_path=dirs["save_path"],
    )
    tg = TelegramManager(secrets, dirs["sessions"])
    sources = SourceService(store, tg.user_client_proxy)
    search = SearchService(store, tg.user_client_proxy)
    # 依赖注入边界：user_client 未连接时占位 proxy 报 not_connected
    downloads = DownloadService(
        store,
        tg.download_client_proxy,
        events,
        dirs["temp"],
        template,
        max_concurrent=int(store.get_setting("max_download_task", "3") or 3),
    )
    # 源同步/回溯（FR-SRC-04）由下载 Worker 池执行：扫描 → 逐条入队
    downloads.set_sync_runner(SyncRunner(store, tg.user_client_proxy, downloads))
    preview = PreviewService(
        store,
        tg.download_client_proxy,
        events,
        dirs["preview"],
        max_bytes=int(store.get_setting("preview_cache_max_bytes", str(512 * 1024 * 1024)) or 0),
    )
    return AppServices(
        dirs=dirs,
        secrets=secrets,
        store=store,
        events=events,
        template=template,
        sources=sources,
        search=search,
        downloads=downloads,
        preview=preview,
        tg=tg,
    )


async def run(base_dir: Path) -> None:
    """单 asyncio loop：uvicorn + worker 池（SDD §1.3）。"""
    import uvicorn  # noqa: PLC0415  延迟导入保持模块加载轻

    svc = build_services(base_dir)
    store = svc.store
    recovered = store.recover_interrupted()
    if recovered:
        logging.getLogger(__name__).info("recovered %d interrupted tasks", recovered)

    app = create_app(
        store=svc.store,
        events=svc.events,
        downloads=svc.downloads,
        sources=svc.sources,
        search=svc.search,
        preview=svc.preview,
        tg=svc.tg,
        base_dir=base_dir,
        web_host=svc.secrets.web_host,
        web_login_secret=svc.secrets.web_login_secret,
        static_dir=web_dist_dir(),
    )
    svc.tg.set_services(svc.downloads, svc.search)
    await svc.downloads.start_workers()
    await svc.tg.start()  # User（有会话时）与 Bot（有 token 时）一起装配并启动

    config = uvicorn.Config(
        app,
        host=svc.secrets.web_host,
        port=svc.secrets.web_port,
        log_level="info",
        access_log=False,
    )
    server = uvicorn.Server(config)
    try:
        await server.serve()
    finally:
        await svc.tg.stop()
        await svc.downloads.stop_workers()
        store.close()


def main() -> None:
    # Docker：TGM_BASE_DIR 指向挂载卷（/data）；默认源码根
    base_dir = Path(os.environ.get("TGM_BASE_DIR") or Path(__file__).resolve().parent.parent)
    dirs = app_dirs(base_dir)
    setup_logging(dirs["logs"])
    try:
        asyncio.run(run(base_dir))
    except WebAuthConfigError as e:
        logging.getLogger(__name__).error("startup refused: %s", e.message)
        sys.exit(1)
    except SessionLockedError as e:
        logging.getLogger(__name__).error("startup refused: %s", e.message)
        sys.exit(1)


if __name__ == "__main__":
    main()
