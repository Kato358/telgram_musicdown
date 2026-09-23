"""源管理（FR-SRC-01~05）与搜索（FR-SEARCH-01~04）服务。

services 层不 import pyrogram（编码规范 §2.4 反模式表）；
Telegram 交互经 SourceClientProto 协议面（FakeUserClient 可替换，NFR-07）。
音频判定、卡片映射、源级过滤等跨服务共用的纯规则在 app/domain.py。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

from app.db.models import Source
from app.db.store import Store
from app.domain import (
    SearchResultCard,
    is_audio_message,
    message_to_card,
    source_tags,
)
from app.errors import SourceUnreachableError
from app.utils.linkparse import ParsedLink

logger = logging.getLogger(__name__)

DISCOVER_LIMIT = 60  # 候选源上限：列表本身是「挑几个加入」，不做全量呈现（FR-SRC-05）


class SourceClientProto(Protocol):
    """源/搜索所需协议面（FakeUserClient 实现，NFR-07）。"""

    async def get_chat(self, entity: str | int) -> dict[str, Any]: ...

    async def search_messages(
        self, chat_id: int, query: str, limit: int, offset: int
    ) -> list[dict[str, Any]]: ...

    async def list_dialogs(self, limit: int) -> list[dict[str, Any]]: ...


@dataclass(slots=True)
class ChatInfo:
    """get_chat 解析结果（FR-SRC-01）。"""

    chat_id: int
    title: str
    type: str  # channel|group|user
    username: str | None = None
    can_view_history: bool = True


@dataclass(slots=True)
class DiscoverCandidate:
    """候选源（FR-SRC-05）：账号对话中命中音乐关键词的频道/群组/用户。"""

    chat_id: int
    title: str
    username: str | None
    type: str
    members: int | None
    tags: list[str]


class SourceService:
    """音乐源增删改查（FR-SRC-01~03）。"""

    def __init__(self, store: Store, client: SourceClientProto) -> None:
        self.store = store
        self.client = client

    async def add_source(self, link: ParsedLink) -> Source:
        """添加源：get_chat 解析 → 保存（FR-SRC-01）。"""
        entity: str | int = link.username or link.chat_id or ""
        if not entity:
            raise SourceUnreachableError("invalid_link")
        try:
            info_raw = await self.client.get_chat(entity)
        except SourceUnreachableError:
            raise
        except Exception as e:
            raise SourceUnreachableError("not_joined", f"cannot resolve chat: {e}") from e
        info = ChatInfo(
            chat_id=info_raw["chat_id"],
            title=info_raw["title"],
            type=info_raw["type"],
            username=info_raw.get("username"),
            can_view_history=info_raw.get("can_view_history", True),
        )
        if not info.can_view_history:
            raise SourceUnreachableError("banned", "history not accessible")
        now_src = Source(
            id=None,
            telegram_chat_id=info.chat_id,
            title=info.title,
            type=info.type,
            username=info.username,
        )
        if existing := self.store.get_source_by_chat(info.chat_id):
            now_src.id = existing.id
            now_src.created_at = existing.created_at
        source_id = self.store.upsert_source(now_src)
        saved = self.store.get_source(source_id)
        if saved is None:  # pragma: no cover - 刚 upsert 必在
            raise RuntimeError(f"source {source_id} vanished after upsert")
        return saved

    def list_sources(self, enabled_only: bool = False) -> list[Source]:
        return self.store.list_sources(enabled_only)

    def update_source(self, source: Source) -> Source:
        self.store.upsert_source(source)
        source_id = source.id
        if source_id is None:  # pragma: no cover - 调用契约要求已持久化
            raise RuntimeError("update_source requires persisted source")
        saved = self.store.get_source(source_id)
        if saved is None:  # pragma: no cover
            raise RuntimeError(f"source {source_id} vanished after upsert")
        return saved

    def delete_source(self, source_id: int, with_history: bool = False) -> None:
        self.store.delete_source(source_id, with_history)

    async def discover_candidates(self, limit: int = DISCOVER_LIMIT) -> list[DiscoverCandidate]:
        """候选源（FR-SRC-05）：扫账号对话，标题/用户名命中音乐关键词且尚未添加的。

        非自动全量加入：只列候选，加不加由用户在向导里逐个决定。
        """
        added = {s.telegram_chat_id for s in self.store.list_sources()}
        raw = await self.client.list_dialogs(limit)
        out: list[DiscoverCandidate] = []
        for d in raw:
            chat_id = int(d["chat_id"])
            if chat_id in added:
                continue
            tags = source_tags(str(d.get("title") or ""), d.get("username"))
            if not tags:
                continue
            members = d.get("members")
            out.append(
                DiscoverCandidate(
                    chat_id=chat_id,
                    title=str(d.get("title") or chat_id),
                    username=d.get("username"),
                    type=str(d.get("type") or "user"),
                    members=int(members) if isinstance(members, int) else None,
                    tags=tags,
                )
            )
        out.sort(key=lambda c: (c.members or 0, c.title.lower()), reverse=True)
        return out[:limit]


@dataclass(slots=True)
class SearchResponse:
    """搜索响应：卡片数组 + meta（floodwait/不可达附在 meta，FR-SEARCH-01）。"""

    results: list[SearchResultCard]
    meta: dict[str, Any]


class SearchService:
    """已启用源内关键词搜索（FR-SEARCH-01~03）。"""

    def __init__(self, store: Store, client: SourceClientProto) -> None:
        self.store = store
        self.client = client

    async def search(
        self,
        q: str,
        source_ids: list[int] | None = None,
        page: int = 0,
        page_size: int = 20,
    ) -> SearchResponse:
        """搜索已启用源（FR-SEARCH-01）；结果按日期排序，二次筛选拉全量后过滤。"""
        sources = [
            s
            for s in self.store.list_sources(enabled_only=True)
            if source_ids is None or s.id in source_ids
        ]
        if not sources:
            return SearchResponse(results=[], meta={"reason": "no_enabled_sources"})
        results: list[SearchResultCard] = []
        unreachable: list[dict[str, Any]] = []
        for src in sources:
            try:
                raw = await self.client.search_messages(
                    src.telegram_chat_id, q, limit=page_size, offset=page * page_size
                )
            except SourceUnreachableError as e:
                unreachable.append({"source_id": src.id, "reason": e.reason})
                continue
            for m in raw:
                if not is_audio_message(m):
                    continue
                results.append(message_to_card(m, src.title))
        results.sort(key=lambda c: c.message_date or "", reverse=True)
        start = page * page_size
        return SearchResponse(
            results=results[start : start + page_size],
            meta={"unreachable": unreachable},
        )
