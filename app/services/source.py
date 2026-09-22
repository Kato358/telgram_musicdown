"""源管理（FR-SRC-01~05）与搜索（FR-SEARCH-01~04）服务。

services 层不 import pyrogram（编码规范 §2.4 反模式表）；
Telegram 交互经 TelegramClientProto 协议面（FakeUserClient 可替换，NFR-07）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from app.db.models import Source
from app.db.store import Store
from app.errors import SourceUnreachableError
from app.utils.linkparse import ParsedLink

logger = logging.getLogger(__name__)


class SourceClientProto(Protocol):
    """源/搜索所需协议面（FakeUserClient 实现，NFR-07）。"""

    async def get_chat(self, entity: str | int) -> dict[str, Any]: ...

    async def search_messages(
        self, chat_id: int, query: str, limit: int, offset: int
    ) -> list[dict[str, Any]]: ...


@dataclass(slots=True)
class ChatInfo:
    """get_chat 解析结果（FR-SRC-01）。"""

    chat_id: int
    title: str
    type: str  # channel|group|user
    username: str | None = None
    can_view_history: bool = True


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


@dataclass(slots=True)
class SearchResultCard:
    """搜索结果卡片（FR-SEARCH-02）。"""

    chat_id: int
    message_id: int
    title: str | None
    artist: str | None
    duration_sec: int | None
    file_size: int | None
    ext: str | None
    mime: str | None
    channel_title: str | None
    message_date: str | None
    caption: str | None
    file_unique_id: str | None = None


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
                if not self._is_audio(m):
                    continue
                results.append(self._to_card(m, src.title))
        results.sort(key=lambda c: c.message_date or "", reverse=True)
        start = page * page_size
        return SearchResponse(
            results=results[start : start + page_size],
            meta={"unreachable": unreachable},
        )

    @staticmethod
    def _is_audio(m: dict[str, Any]) -> bool:
        """音频判定（SDD §2.1）：msg.audio 或 MIME audio/* document；voice 一律排除。"""
        if m.get("voice"):
            return False
        if m.get("audio"):
            return True
        doc = m.get("document")
        return bool(doc and str(doc.get("mime_type", "")).startswith("audio/"))

    @staticmethod
    def _to_card(m: dict[str, Any], channel_title: str) -> SearchResultCard:
        audio = m.get("audio") or {}
        doc = m.get("document") or {}
        file_name = audio.get("file_name") or doc.get("file_name")
        ext = file_name.rsplit(".", 1)[-1] if file_name and "." in file_name else None
        return SearchResultCard(
            chat_id=m["chat_id"],
            message_id=m["message_id"],
            title=audio.get("title") or file_name,
            artist=audio.get("performer"),
            duration_sec=audio.get("duration") or doc.get("duration"),
            file_size=audio.get("file_size") or doc.get("file_size"),
            ext=ext,
            mime=audio.get("mime_type") or doc.get("mime_type"),
            channel_title=channel_title,
            message_date=m.get("message_date"),
            caption=m.get("caption"),
            file_unique_id=audio.get("file_unique_id") or doc.get("file_unique_id"),
        )


class SourceFilters:
    """源级过滤（FR-SRC-02）：filters_json 的结构化形式。"""

    @staticmethod
    def parse(filters_json: str | None) -> dict[str, Any]:
        if not filters_json:
            return {}
        try:
            data = json.loads(filters_json)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def _out_of_duration(f: dict[str, Any], dur: int | None) -> bool:
        min_d, max_d = f.get("min_duration"), f.get("max_duration")
        if dur is None:
            return min_d is not None or max_d is not None
        return (min_d is not None and dur < min_d) or (max_d is not None and dur > max_d)

    @staticmethod
    def _caption_blocked(f: dict[str, Any], caption: str | None) -> bool:
        cap = caption or ""
        contains = f.get("caption_contains") or []
        excludes = f.get("caption_exclude") or []
        has_all = all(k in cap for k in contains) if contains else True
        has_none = not any(k in cap for k in excludes)
        return not (has_all and has_none)

    @staticmethod
    def matches(filters_json: str | None, card: SearchResultCard) -> bool:
        f = SourceFilters.parse(filters_json)
        if not f:
            return True
        if SourceFilters._out_of_duration(f, card.duration_sec):
            return False
        if (exts := f.get("ext_whitelist")) and card.ext not in exts:
            return False
        return not SourceFilters._caption_blocked(f, card.caption)


def card_to_dict(card: SearchResultCard) -> dict[str, Any]:
    return asdict(card)
