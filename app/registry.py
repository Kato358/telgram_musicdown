"""来源索引：把音乐源频道拉平成一份「当前可搜的源」清单（组合根的配套件）。

**为什么它不是 service**：SDD §1.2 定了「services 之间禁止互相 import」。
``SearchService`` / ``DownloadService`` / ``PreviewService`` 都要用这份清单，若把
它做成 service 就等于让三个服务依赖第四个服务。做成适配器层的具体实现、由容器
注入进各服务（服务侧只认 ``MusicSourceIndexProto``），依赖方向仍然是
services → ports ← adapters。

清单随库里的启用源实时重建，不缓存：源是低频变更的资源，而一次搜索的代价是一次
网络往返，多构造几个小对象换来的是「删了源立刻生效」这个不必再推理的性质。

scope 分配全局唯一（``domain.PROVIDER_SCOPES`` 是它的唯一事实源）：

============  ==========================================
scope         来源
============  ==========================================
``>= 1``      ``sources`` 表里启用的音乐源频道（``sources.id``）
``0``         searchGlobal 的保留 scope（不在这份清单里）
``-1/-2/-3``  预留给在线源平台（网易云 / QQ 音乐 / 酷狗）
============  ==========================================
"""

from __future__ import annotations

import logging

from app.domain import PROVIDER_SCOPES, PROVIDER_TELEGRAM
from app.ports.music import MusicSourceProto
from app.ports.repository import SourceRepo
from app.telegram.music_source import MediaClientProto, SearchClientProto, TelegramSource

logger = logging.getLogger(__name__)


class SourceRegistry:
    """``MusicSourceIndexProto`` 的具体实现：音乐源频道的索引。"""

    def __init__(
        self,
        sources: SourceRepo,
        search_client: SearchClientProto,
        media_client: MediaClientProto,
    ) -> None:
        self._sources = sources
        self._search = search_client
        self._media = media_client

    def targets(self) -> list[MusicSourceProto]:
        """当前可搜的来源：启用的音乐源频道。"""
        return [
            TelegramSource(row.id, row.telegram_chat_id, row.title, self._search, self._media)
            for row in self._sources.list_sources(enabled_only=True)
            if row.id is not None
        ]

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
