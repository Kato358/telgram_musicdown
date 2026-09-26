"""轻量依赖注入容器（SRP）：装配与生命周期收口。

- ``Container``：一次性解析配置 → 按依赖顺序构造仓储与各服务；
  服务之间只引用接口/协议面（DIP），容器是唯一知道具体实现的地方。
- ``Container.close()``：统一释放资源（DB 连接）。

测试可用 ``Container.override`` 预置假实现（FakeUserClient / 假仓储），
其余组件照常装配，不 mock 被测对象（编码规范 §6）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.appsettings import AppSettings, load_app_settings
from app.config import SecretConfig, app_dirs
from app.ports import IStore
from app.ports.telegram import TelegramClientProto
from app.registry import SourceRegistry
from app.services.download import DownloadService
from app.services.local_library import LocalLibraryService
from app.services.preview import PreviewService
from app.services.search_cache import SearchCache
from app.services.source import SearchService, SourceService


@dataclass(slots=True)
class Overrides:
    """装配期覆盖项：测试注入假实现用（None = 用真实实现）。"""

    store: IStore | None = None
    download_client: TelegramClientProto | None = None
    source_client: Any = None
    secrets: SecretConfig | None = None
    events: Any = None  # EventBus 覆盖（None = 新建）


@dataclass(slots=True)
class Container:
    """服务容器：装配结果 + 统一关闭（供 web/tg 与测试共用）。"""

    base_dir: Path
    dirs: dict[str, Path]
    settings: AppSettings
    store: IStore
    #: 来源索引：搜索、下载、试听共用同一个实例（SDD §2.7）。设置保存时就地刷新它。
    registry: SourceRegistry
    downloads: DownloadService
    sources: SourceService
    search: SearchService
    preview: PreviewService
    library: LocalLibraryService
    extras: dict[str, Any] = field(default_factory=dict)

    def close(self) -> None:
        """统一释放：DB 连接等生命周期资源收口于此。"""
        close = getattr(self.store, "close", None)
        if callable(close):
            close()


def build_container(base_dir: Path, overrides: Overrides | None = None) -> Container:
    """装配全部服务（DIP 组合根）：配置 → 仓储 → 服务，按依赖顺序构造。"""
    ov = overrides or Overrides()
    dirs = app_dirs(base_dir)
    store = ov.store
    if store is None:
        from app.db.store import Store  # noqa: PLC0415  具体实现只在组合根引用

        store = Store(dirs["data"] / "app.db")
    settings = load_app_settings(base_dir, store, secrets=ov.secrets)
    events = ov.events
    if events is None:
        from app.events import EventBus  # noqa: PLC0415

        events = EventBus()
    download_client = ov.download_client
    source_client = ov.source_client

    # 三个服务共用一份来源索引：同一个来源在搜索里叫 A、在下载里必须还是 A。
    registry = SourceRegistry(
        store,
        source_client,
        download_client,  # type: ignore[arg-type]  装配后必非空
    )

    downloads = DownloadService(
        store,
        download_client,
        events,
        dirs["temp"],
        settings.template,
        max_concurrent=settings.download.max_concurrent,
        registry=registry,
    )
    sources = SourceService(store, source_client)
    search = SearchService(
        store,
        source_client,
        SearchCache(store, settings.search.cache),
        settings.search,
        registry=registry,
    )
    preview = PreviewService(
        store,
        registry,
        events,
        dirs["preview"],
        max_bytes=settings.preview.max_bytes,
    )
    library = LocalLibraryService(store, dirs["save_path"], downloads.tags, events)
    return Container(
        base_dir=base_dir,
        dirs=dirs,
        settings=settings,
        store=store,
        registry=registry,
        downloads=downloads,
        sources=sources,
        search=search,
        preview=preview,
        library=library,
        extras={"events": events},
    )
