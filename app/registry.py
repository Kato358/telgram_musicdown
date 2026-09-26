"""来源索引：把两类适配器拉平成一份「当前可搜的源」清单（组合根的配套件）。

**为什么它不是 service**：SDD §1.2 定了「services 之间禁止互相 import」。
``SearchService`` / ``DownloadService`` / ``PreviewService`` 都要用这份清单，若把
它做成 service 就等于让三个服务依赖第四个服务。做成适配器层的具体实现、由容器
注入进各服务（服务侧只认 ``MusicSourceIndexProto``），依赖方向仍然是
services → ports ← adapters。

清单随库里的启用源实时重建，不缓存：源是低频变更的资源，而一次搜索的代价是一次
网络往返，多构造几个小对象换来的是「删了源立刻生效」这个不必再推理的性质。

scope 分配全局唯一，三段互不相撞（``domain.PROVIDER_SCOPES`` 是它的唯一事实源）：

============  ==========================================
scope         来源
============  ==========================================
``>= 1``      ``sources`` 表里启用的音乐源频道（``sources.id``）
``0``         searchGlobal 的保留 scope（不在这份清单里）
``-1/-2/-3``  网易云 / QQ 音乐 / 酷狗（``PROVIDER_SCOPES``）
============  ==========================================
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from app.appsettings import OnlineSourceKey
from app.chksz.client import ChkszClient
from app.chksz.source import ChkszSource
from app.domain import PROVIDER_SCOPES, PROVIDER_TELEGRAM
from app.ports.music import MusicSourceProto
from app.ports.repository import SourceRepo
from app.ports.telegram import MediaClientProto, SearchClientProto
from app.telegram.music_source import TelegramSource

logger = logging.getLogger(__name__)


class SourceRegistry:
    """``MusicSourceIndexProto`` 的具体实现：音乐源频道 + 在线源，一份清单。"""

    def __init__(
        self,
        sources: SourceRepo,
        search_client: SearchClientProto,
        media_client: MediaClientProto,
        *,
        online_key: Callable[[], OnlineSourceKey] | None = None,
        chksz_client: ChkszClient | None = None,
        chksz_enabled: bool = False,
        download_quality: str = "flac",
    ) -> None:
        self._sources = sources
        self._search = search_client
        self._media = media_client
        self._online_key = online_key
        self._chksz_client = chksz_client
        self._chksz_enabled = chksz_enabled
        self._download_quality = download_quality
        #: 按 (Key, 地址) 缓存的在线源客户端；配置换了就换一个（旧的退场时统一关）。
        self._chksz_clients: dict[tuple[str, str], ChkszClient] = {}

    def apply_chksz(self, enabled: bool, download_quality: str) -> None:
        """设置页保存后即时开关在线源并换默认档（FR-CFG-03：不必重启）。

        只翻这两个标志——客户端不在这里建：它是按 **现读的** Key 惰性建的
        （见 :meth:`_online_client`），所以「先开开关、后补 Key」也能立刻用上。
        """
        self._chksz_enabled = enabled
        self._download_quality = download_quality

    def online_sources(self) -> list[MusicSourceProto]:
        """在线源适配器（按 ``PROVIDER_SCOPES`` 的顺序）；未启用或没 Key 时为空。"""
        client = self._online_client()
        if not self._chksz_enabled or client is None:
            return []
        return [
            ChkszSource(provider, client, default_quality=self._download_quality)
            for provider in PROVIDER_SCOPES
        ]

    def _online_client(self) -> ChkszClient | None:
        """在线源客户端：注入的优先（装配/测试），否则按**现读**的 Key 惰性建一个。

        判据是「开关打开**且**配了 Key」——两件事都可能在本进程存活期间变化（设置页保存、
        向导写 Key），而 Key 只在 config.yaml。启动时读一次就定死，等于「开关显示已开、
        搜索里什么都没有」；这里按 (Key, 地址) 记账，改了就用新的那个。
        """
        if self._chksz_client is not None:
            return self._chksz_client
        if self._online_key is None:
            return None
        key = self._online_key()
        if not key.api_key:
            return None
        client = self._chksz_clients.get((key.api_key, key.base_url))
        if client is None:
            client = ChkszClient(key.base_url, key.api_key)
            self._chksz_clients[(key.api_key, key.base_url)] = client
        return client

    async def aclose(self) -> None:
        """退场：关掉本对象建过的在线源连接池（注入进来的那个不归我们管）。"""
        for client in self._chksz_clients.values():
            await client.aclose()
        self._chksz_clients.clear()

    def targets(self) -> list[MusicSourceProto]:
        """当前可搜的来源：启用的音乐源频道在前，在线源在后。"""
        channels = [
            TelegramSource(row.id, row.telegram_chat_id, row.title, self._search, self._media)
            for row in self._sources.list_sources(enabled_only=True)
            if row.id is not None
        ]
        return [*channels, *self.online_sources()]

    def by_scope(self, scope_id: int) -> MusicSourceProto | None:
        """按 scope 找回来源；已停用/已移除返回 None，调用方据此拒绝执行。"""
        for target in self.targets():
            if target.scope_id == scope_id:
                return target
        return None

    def by_meta(self, provider: str, source_id: int | None) -> MusicSourceProto | None:
        """按记录归属找回「能取到它」的来源（取得到 ≠ 搜得到，见协议里的说明）。"""
        if provider == PROVIDER_TELEGRAM:
            if source_id is not None:
                hit = self.by_scope(source_id)
                if hit is not None:
                    return hit
            # 源行已停用或已删除也照样能下：音频的定位是 (chat_id, message_id)，
            # 不依赖 sources 行在不在。给一个不入清单的游离适配器。
            return TelegramSource.detached(self._search, self._media)
        scope = PROVIDER_SCOPES.get(provider)
        return self.by_scope(scope) if scope is not None else None
