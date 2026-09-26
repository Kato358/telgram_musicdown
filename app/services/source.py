"""音乐源管理（FR-SRC-01~05）：添加 / 列出 / 启停 / 删除，以及候选源发现。

services 层不 import Kurigram（导入名 pyrogram；编码规范 §2.4 反模式表）；
Telegram 交互经 ``DialogClientProto`` 协议面（FakeUserClient 可替换，NFR-07）。
搜索链路见 ``services/search.py``，本模块只管「源」这个资源本身。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.db.models import Source
from app.domain import source_tags
from app.errors import AppError, SourceUnreachableError
from app.ports.repository import SourceRepo
from app.ports.telegram import DialogClientProto
from app.utils.linkparse import ParsedLink

logger = logging.getLogger(__name__)

DISCOVER_LIMIT = 60  # 候选源上限：列表本身是「挑几个加入」，不做全量呈现（FR-SRC-05）

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

    def __init__(self, store: SourceRepo, client: DialogClientProto) -> None:
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
        except AppError:
            # 客户端自己报的领域错误（not_connected 等）原样透出：改写成「没加入」会让
            # 界面给出错的修复方向（编码规范 §2.4：不吞、不改写底层给出的原因）。
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
