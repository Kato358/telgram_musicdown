"""Telegram 音乐源频道适配器：把一个频道包装成 ``MusicSourceProto``。

与 ``app/chksz/source.py`` 平级，是同一个协议的另一个实现。服务层因此不必知道
「结果是从频道搜来的还是从在线源搜来的」——搜索、下载、试听三条链路只认协议。

Telegram 侧特有的两件事留在本文件，不外泄：

- **搜索会混进非音频消息**：上游按 Audio 过滤器过滤了，但一轮拿到的条目未必全是
  音频，所以要按「消费了多少上游条目」续取，不能按卡片数续（SDD §2.6）。
- **限流不挂起**：``search_messages`` 把 FloodWait 翻成 ``reason="flood_wait"``
  直接透出（FR-SEARCH-01「限流要可见」），本适配器原样往上传。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Protocol

from app.domain import PROVIDER_TELEGRAM, SearchResultCard, is_audio_message, message_to_card
from app.ports.music import FetchRef, FetchResult, ProgressCb, SearchWindow

# 游离适配器的 scope：它不进任何缓存键，也不进搜索清单，只用来「按 chat/message
# 把音频取回来」——源行已停用或已删除时的兜底。取一个与所有保留 scope 都不撞的值。
DETACHED_SCOPE = -999

# 单次搜索为凑够一页卡片最多往返几次（上游已按 Audio 过滤，通常一次就够）。
FETCH_ROUNDS = 5


def _size_of(path: Path) -> int:
    """落盘字节数（文件不存在算 0，由调用方判定该失败）。"""
    return path.stat().st_size if path.exists() else 0


class SearchClientProto(Protocol):
    """对话内搜索所需协议面（``UserClient`` 与测试假客户端都实现它）。"""

    async def search_messages(
        self, chat_id: int, query: str, limit: int, offset: int
    ) -> list[dict[str, Any]]: ...


class MediaClientProto(Protocol):
    """取音频所需协议面。"""

    def download_media(
        self,
        message_ref: dict[str, Any],
        file_name: str,
        progress: ProgressCb | None = None,
    ) -> Any: ...


class TelegramSource:
    """一个已启用的音乐源频道。"""

    provider = PROVIDER_TELEGRAM

    def __init__(
        self,
        scope_id: int,
        chat_id: int,
        title: str,
        search_client: SearchClientProto,
        media_client: MediaClientProto,
    ) -> None:
        self.scope_id = scope_id
        self.label = title
        self.chat_id = chat_id
        self._search = search_client
        self._media = media_client

    @classmethod
    def detached(
        cls, search_client: SearchClientProto, media_client: MediaClientProto
    ) -> TelegramSource:
        """不带源行的适配器：只能 ``fetch``（音频定位在 meta 里），不能 ``search``。"""
        return cls(DETACHED_SCOPE, 0, "Telegram", search_client, media_client)

    async def search(self, keyword: str, need: int, offset: int = 0) -> SearchWindow:
        """续取到凑够 ``need`` 张卡片。

        ``consumed`` 记的是**上游条目数**不是卡片数：混着非音频消息时两者不等，
        拿卡片数去续会重复翻同一段、把后面的歌整段漏掉。
        """
        if not self.chat_id:
            raise ValueError("游离 Telegram 适配器不能搜索：它没有源对话")
        items: list[SearchResultCard] = []
        consumed = offset
        has_more = True
        for _ in range(FETCH_ROUNDS):
            if len(items) >= need or not has_more:
                break
            limit = need - len(items)
            raw = await self._search.search_messages(
                self.chat_id, keyword, limit=limit, offset=consumed
            )
            if not raw:
                has_more = False
                break
            consumed += len(raw)
            has_more = len(raw) >= limit
            items.extend(message_to_card(m, self.label) for m in raw if is_audio_message(m))
        return SearchWindow(items=items, consumed=consumed, has_more=has_more)

    async def fetch(
        self, ref: FetchRef, dest: Path, progress: ProgressCb | None = None
    ) -> FetchResult:
        """按 chat_id/message_id 取音频到 ``dest``。

        频道里的文件就是它本身，没有「音质」这回事（``FetchRef.quality`` 忽略）。
        完整性由下载侧的 ``meta.file_size`` 校验兜底——只有 Telegram 的卡片带得上
        这个数，在线源没有，所以那一档校验在各自适配器里做。
        """
        if ref.chat_id is None or ref.message_id is None:
            raise ValueError("Telegram 源取音频需要 chat_id 与 message_id")
        await self._media.download_media(
            {"chat_id": ref.chat_id, "message_id": ref.message_id},
            str(dest),
            progress=progress,
        )
        return FetchResult(
            file_size=await asyncio.to_thread(_size_of, dest)
        )
