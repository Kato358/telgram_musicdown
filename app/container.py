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

from app.appsettings import AppSettings, load_app_settings, load_online_source_key
from app.config import SecretConfig, app_dirs
from app.events import EventBus
from app.ports import IStore
from app.ports.telegram import MediaClientProto, UserClientProto
from app.registry import SourceRegistry
from app.services.download import DownloadService
from app.services.local_library import LocalLibraryService
from app.services.preview import PreviewService
from app.services.search import SearchService
from app.services.search_cache import SearchCache
from app.services.source import SourceService
from app.telegram.unconnected import UnconnectedTelegramClient


@dataclass(slots=True)
class Overrides:
    """装配期覆盖项：测试注入假实现用；缺省是「未连接的占位客户端」而非 None。

    客户端缺省是 ``UnconnectedTelegramClient``（调用即报 ``not_connected``）：这样
    容器与各服务任何时刻都持有可调用的客户端，「某个链路悄悄拿 None 打上游」这类
    装配缺陷在类型上就不成立（它曾真实发生过：来源索引拿到的搜索客户端是 None）。
    """

    store: IStore | None = None
    user_client: UserClientProto = field(default_factory=UnconnectedTelegramClient)
    media_client: MediaClientProto = field(default_factory=UnconnectedTelegramClient)
    secrets: SecretConfig | None = None
    events: EventBus | None = None  # None = 新建


@dataclass(slots=True)
class Container:
    """服务容器：装配结果 + 统一关闭（供 web/tg 与测试共用）。"""

    base_dir: Path
    dirs: dict[str, Path]
    settings: AppSettings
    store: IStore
    events: EventBus
    #: 来源索引：搜索、下载、试听共用同一个实例（SDD §2.7）。设置保存时就地刷新它。
    registry: SourceRegistry
    downloads: DownloadService
    sources: SourceService
    search: SearchService
    preview: PreviewService
    library: LocalLibraryService

    def close(self) -> None:
        """统一释放：DB 连接等生命周期资源收口于此。"""
        close = getattr(self.store, "close", None)
        if callable(close):
            close()

    async def aclose(self) -> None:
        """异步资源释放（HTTP 连接池）；``close()`` 之后仍可调，用于完整收尾。

        在线源的连接池归来源索引持有（它按现读的 Key 建客户端），故这里转交给它。
        """
        await self.registry.aclose()
        self.close()


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
        events = EventBus()
    user_client = ov.user_client
    media_client = ov.media_client

    # 三个服务共用一份来源索引：同一个来源在搜索里叫 A、在下载里必须还是 A。
    # 在线源客户端由索引按**现读**的 Key 惰性建（见 SourceRegistry._online_client）：
    # 启用平台与 Key 都是运行期可变的，启动时建一次就会「开关开着、搜索里什么都没有」。
    registry = SourceRegistry(
        store,
        user_client,
        media_client,
        online_key=lambda: load_online_source_key(base_dir),
        chksz_providers=settings.chksz.providers,
        download_quality=settings.chksz.download_quality,
    )

    downloads = DownloadService(
        store,
        media_client,
        events,
        dirs["temp"],
        settings.template,
        max_concurrent=settings.download.max_concurrent,
        registry=registry,
        default_quality=settings.chksz.download_quality,
    )
    sources = SourceService(store, user_client)
    search = SearchService(
        store,
        user_client,
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
        preview_quality=settings.chksz.preview_quality,
    )
    library = LocalLibraryService(store, dirs["save_path"], downloads.tags, events)
    return Container(
        base_dir=base_dir,
        dirs=dirs,
        settings=settings,
        store=store,
        events=events,
        registry=registry,
        downloads=downloads,
        sources=sources,
        search=search,
        preview=preview,
        library=library,
    )
