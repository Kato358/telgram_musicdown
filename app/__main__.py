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
from typing import Any

from app.config import SecretConfig, load_secrets, web_dist_dir
from app.container import Container, build_container
from app.errors import SessionLockedError, WebAuthConfigError
from app.events import EventBus, attach_event_log_bridge
from app.services.download import DownloadService
from app.services.local_library import LocalLibraryService
from app.services.preview import PreviewService
from app.services.source import SearchService, SourceService
from app.services.sync import SyncRunner
from app.telegram.manager import TelegramManager
from app.utils.proactor_patch import silence_proactor_connection_reset
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
    """装配后的服务容器（供 web/tg 与测试共用）。

    只放**启动后仍被读**的句柄；``dirs``/``template`` 是装配期中间量（``dirs`` 已用在
    建 TG 客户端与 save_path，``template`` 已交给 DownloadService），不在此重复持有。
    """

    secrets: SecretConfig
    store: Any
    events: EventBus
    sources: SourceService
    search: SearchService
    downloads: DownloadService
    preview: PreviewService
    library: LocalLibraryService
    tg: TelegramManager
    # 组合根持有的容器：关停时经它统一释放（Container.close 是生命周期收口点）
    container: Container


def build_services(base_dir: Path) -> AppServices:
    """装配全部服务；返回依赖容器（供 web/tg 与测试共用）。

    组合根：配置与仓储装配走 ``build_container``（DI），TG 生命周期在此收口。
    """
    container = build_container(base_dir)
    dirs = container.dirs
    store = container.store
    secrets = load_secrets(base_dir)
    web_auth.check_auth_config(secrets.web_host, secrets.web_login_secret)
    events = container.extras["events"]
    tg = TelegramManager(secrets, dirs["sessions"])
    downloads = container.downloads
    # 依赖注入边界：user_client 未连接时占位 proxy 报 not_connected
    downloads.client = tg.download_client_proxy
    container.sources.client = tg.user_client_proxy
    container.search.client = tg.user_client_proxy
    # 源同步/回溯（FR-SRC-04）由下载 Worker 池执行：扫描 → 逐条入队
    downloads.set_sync_runner(SyncRunner(store, tg.user_client_proxy, downloads))
    container.preview.client = tg.download_client_proxy
    return AppServices(
        secrets=secrets,
        store=store,
        events=events,
        sources=container.sources,
        search=container.search,
        downloads=downloads,
        preview=container.preview,
        library=container.library,
        tg=tg,
        container=container,
    )


async def run(base_dir: Path) -> None:
    """单 asyncio loop：uvicorn + worker 池（SDD §1.3）。"""
    import uvicorn  # noqa: PLC0415  延迟导入保持模块加载轻

    svc = build_services(base_dir)
    store = svc.store
    # ERROR+ 日志桥到 SSE：日志页的「实时错误」靠它（事件总线见 app/events.py）
    attach_event_log_bridge(svc.events)
    recovered = store.recover_interrupted()
    if recovered:
        logging.getLogger(__name__).info("recovered %d interrupted tasks", recovered)
    # 旧记录缺时长/大小/码率（链接入队时拿不到）：启动时按落盘文件补一次
    backfilled = await asyncio.to_thread(svc.downloads.backfill_history_media)
    if backfilled:
        logging.getLogger(__name__).info("backfilled media facts for %d history rows", backfilled)

    # 本地曲库台账随启动后台扫一次（不阻塞 web/tg 启动；扫描结果经 SSE 推给前端）
    async def _startup_library_scan() -> None:
        try:
            await svc.library.rescan()
        except Exception:  # noqa: BLE001  曲库扫描失败不影响主服务
            logging.getLogger(__name__).exception("startup library scan failed")

    library_scan_task = asyncio.create_task(_startup_library_scan())

    app = create_app(
        store=svc.store,
        events=svc.events,
        downloads=svc.downloads,
        sources=svc.sources,
        search=svc.search,
        preview=svc.preview,
        tg=svc.tg,
        library=svc.library,
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
        library_scan_task.cancel()
        svc.container.close()  # 生命周期收口：不再绕过容器直接 store.close()


def main() -> None:
    # Docker：TGM_BASE_DIR 指向挂载卷（/data）；默认源码根
    from app.config import app_dirs  # noqa: PLC0415  仅 main 需要

    # Windows Proactor 关连接时对已 RST 的 socket shutdown 报 10054，属 asyncio 已知噪音
    silence_proactor_connection_reset()
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
