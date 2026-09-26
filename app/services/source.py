"""源管理（FR-SRC-01~05）与搜索（FR-SEARCH-01~04）服务。

services 层不 import Kurigram（导入名 pyrogram；编码规范 §2.4 反模式表）；
Telegram 交互经 SourceClientProto 协议面（FakeUserClient 可替换，NFR-07）。
音频判定、卡片映射、源级过滤、搜索筛选/排序等跨服务共用的纯规则在 app/domain.py；
二级缓存（L1 内存 LRU + L2 SQLite）在 app/services/search_cache.py。

搜索链路（SDD §2.6，形态参照 pansou 的「并发取数 + 快回慢补 + 二级缓存」）：
1. 目标源**并发**扇出（``asyncio.Semaphore`` 封顶，防把账号打进 FloodWait）；
2. 每个源先查缓存（L1 → L2），够用就直接用，不回源；
3. 同键并发请求经 singleflight 合并成一次上游调用（缓存只挡「先后」，它挡「同时」）；
4. ``sync_window_sec`` 内回来的进响应，超窗的**转后台补齐**并写缓存——下次同词直接命中；
5. 合并 → 勾选字段过滤 → 二次筛选 → 排序 → 去重 → 切页（分页在完整结果集上做）。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from dataclasses import dataclass, replace
from functools import partial
from typing import Any, Protocol

from app.appsettings import SearchSettings
from app.db.models import Source
from app.domain import (
    SEARCH_SORTS,
    SearchFilters,
    SearchResultCard,
    dedupe_cards,
    is_audio_message,
    keyword_tokens,
    matches_fields,
    message_to_card,
    normalize_keyword,
    sort_cards,
    source_tags,
)
from app.errors import SourceUnreachableError
from app.ports import IStore
from app.services.search_cache import (
    CachedSourceResult,
    QuerySnapshotCache,
    SearchCache,
    SearchCacheStats,
    search_cache_key,
)
from app.utils.linkparse import ParsedLink

logger = logging.getLogger(__name__)

DISCOVER_LIMIT = 60  # 候选源上限：列表本身是「挑几个加入」，不做全量呈现（FR-SRC-05）

SEARCH_FETCH_ROUNDS = 5  # 单源为凑够一页卡片最多往返几次（上游已按 Audio 过滤，通常一次就够）


def _snapshot_key(
    keyword: str,
    sources: list[tuple[int, Source]],
    sort: str,
    *,
    page_size: int,
    fields: list[str] | None,
    filters: dict[str, Any] | None,
) -> str:
    """翻页前缀的身份：同关键词 + 同源集合 + 同排序/字段/筛选 + 同页大小，才算同一次翻页。"""
    return "|".join(
        (
            normalize_keyword(keyword),
            ",".join(str(source_id) for source_id, _ in sources),
            sort if sort in SEARCH_SORTS else "relevance",
            str(page_size),
            ",".join(sorted(fields or ())),
            json.dumps(filters, sort_keys=True, ensure_ascii=False, default=str),
        )
    )


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

    def __init__(self, store: IStore, client: SourceClientProto) -> None:
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
    """搜索响应：卡片数组 + meta（floodwait/不可达/缓存与补齐状态附在 meta，FR-SEARCH-01）。"""

    results: list[SearchResultCard]
    meta: dict[str, Any]


class SearchService:
    """已启用源内关键词搜索（FR-SEARCH-01~04）：二级缓存 + 并发扇出 + 同步窗口。"""

    def __init__(
        self,
        store: IStore,
        client: SourceClientProto,
        cache: SearchCache | None = None,
        settings: SearchSettings | None = None,
    ) -> None:
        self.store = store
        self.client = client
        self.settings = settings or SearchSettings()
        # 缓存是搜索的实现细节，装配路径（容器）显式传 settings 驱动的实例；
        # 其余构造点（测试、一次性脚本）拿缺省缓存即可，不必各自拼一个。
        self.cache = cache or SearchCache(store, self.settings.cache)
        # 翻页用的定序前缀（进程内，见 QuerySnapshotCache 的说明）
        self._snapshots = QuerySnapshotCache(self.settings.cache.ttl_sec)
        self._sem = asyncio.Semaphore(self.settings.fanout)
        # singleflight：同 (源, 关键词, 目标深度) 的并发请求共享一次上游调用
        self._inflight: dict[str, asyncio.Task[CachedSourceResult]] = {}
        # 超窗转后台补齐的任务：强引用集合，防止被 GC 掉
        self._bg: set[asyncio.Task[CachedSourceResult]] = set()

    # ---- 对外 ----

    async def search(
        self,
        q: str,
        source_ids: list[int] | None = None,
        page: int = 0,
        page_size: int | None = None,
        fields: list[str] | None = None,
        filters: dict[str, Any] | None = None,
        sort: str = "relevance",
        *,
        refresh: bool = False,
    ) -> SearchResponse:
        """搜索已启用源（FR-SEARCH-01~03）。

        每个源先备齐 ``(page+1)*page_size`` 张卡片再合并（k 路归并的取数下界），
        并在**同一份定序前缀**上切页（见 ``QuerySnapshotCache``）：旧实现把同一页的
        offset 在上游与本地各减一次，单源时 ``page>0`` 恒空；而按当前窗口直接切页，
        又会因窗口加深导致重复行与丢行。
        """
        sources = [
            (s.id, s)
            for s in self.store.list_sources(enabled_only=True)
            if s.id is not None and (source_ids is None or s.id in source_ids)
        ]
        if not sources:
            return SearchResponse(results=[], meta={"reason": "no_enabled_sources"})
        keyword = q.strip()
        size = min(max(1, page_size or self.settings.page_size), self.settings.max_page_size)
        page = max(0, page)
        need = (page + 1) * size
        snapshot_key = _snapshot_key(
            keyword, sources, sort, page_size=size, fields=fields, filters=filters
        )
        if refresh:
            # 强制重取：定序前缀也作废，否则会拿旧次序套新结果
            self._snapshots.drop(snapshot_key)

        tasks = {
            source_id: asyncio.create_task(
                self._source_result(source_id, src, keyword, need, refresh=refresh)
            )
            for source_id, src in sources
        }
        done, _ = await asyncio.wait(set(tasks.values()), timeout=self.settings.sync_window_sec)

        per_source: list[CachedSourceResult] = []
        unreachable: list[dict[str, Any]] = []
        pending_sources: list[int] = []
        for source_id, _src in sources:
            task = tasks[source_id]
            if task not in done:
                # 超窗：不取消、不等待——它继续跑并自己写缓存，下次同词直接命中完整结果。
                pending_sources.append(source_id)
                self._track_background(task)
                continue
            try:
                per_source.append(task.result())
            except SourceUnreachableError as e:
                unreachable.append({"source_id": source_id, "reason": e.reason})
            except Exception:  # noqa: BLE001  单源崩掉只该让它缺席，不该废掉整次搜索
                logger.exception("search failed source_id=%s", source_id)
                unreachable.append({"source_id": source_id, "reason": "error"})

        tokens = keyword_tokens(keyword)
        parsed = SearchFilters.parse(filters)
        cards = [
            card
            for result in per_source
            for card in result.items
            if parsed.matches(card) and matches_fields(card, tokens, fields)
        ]
        ordered = dedupe_cards(sort_cards(cards, sort, keyword))
        # 翻页必须在**同一份定序前缀**上切（窗口加深会让次序变动，直接切当前窗口会重复或丢行）
        ordered = self._snapshots.resolve(snapshot_key, ordered)
        start = page * size
        hits = sum(1 for result in per_source if result.from_cache)
        return SearchResponse(
            results=ordered[start : start + size],
            meta={
                "unreachable": unreachable,
                "partial": bool(pending_sources),
                "pending_sources": pending_sources,
                "page": page,
                "page_size": size,
                "has_more": start + size < len(ordered)
                or bool(pending_sources)
                or any(result.has_more for result in per_source),
                "sort": sort if sort in SEARCH_SORTS else "relevance",
                "filters": parsed.active,
                "cache": {"hits": hits, "misses": len(per_source) - hits},
            },
        )

    async def invalidate_source(self, source_id: int) -> int:
        """源被移除后清掉它的缓存条目（路由在删除源时调用）。"""
        return self.cache.invalidate_source(source_id)

    def cache_stats(self) -> SearchCacheStats:
        """搜索缓存占用与命中观测（设置页「缓存占用」）。"""
        return self.cache.stats()

    def purge_cache(self) -> int:
        """删过期 + 按上限淘汰（启动装配后调用一次，让上限不只在写时生效）。"""
        return self.cache.purge()

    def clear_cache(self) -> None:
        """清空搜索缓存（设置页「清理缓存」）：能重查，删了只是下次慢一点。"""
        self.cache.clear()
        self._snapshots.clear()

    async def aclose(self) -> None:
        """退场：停掉未完成的补齐/合并任务（缓存写是同步的，没有待落盘的队列）。"""
        outstanding = [*self._bg, *self._inflight.values()]
        for task in outstanding:
            task.cancel()
        if outstanding:
            with contextlib.suppress(Exception):
                await asyncio.gather(*outstanding, return_exceptions=True)
        self._bg.clear()
        self._inflight.clear()

    # ---- 单源取数 ----

    async def _source_result(
        self,
        source_id: int,
        src: Source,
        keyword: str,
        need: int,
        *,
        refresh: bool,
    ) -> CachedSourceResult:
        """一个源的取数窗口：缓存够用就直接回，否则合并一次上游调用并写回缓存。"""
        base: CachedSourceResult | None = None
        if not refresh:
            base = self.cache.get(source_id, keyword)
            if base is not None and (len(base.items) >= need or not base.has_more):
                return replace(base, from_cache=True)
            if base is None and self.cache.is_negative(source_id, keyword):
                # 刚查过「这个源在这个词上没有歌」：直接当作空结果，不重复打上游
                return CachedSourceResult(
                    source_id=source_id,
                    keyword=keyword,
                    items=[],
                    covered=0,
                    has_more=False,
                    fetched_at=time.time(),
                    from_cache=True,
                )
        key = f"{search_cache_key(source_id, keyword)}#{need}"
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.create_task(self._fetch_extend(source_id, src, keyword, need, base))
            self._inflight[key] = task
            task.add_done_callback(partial(self._release_inflight, key))
        return await task

    async def _fetch_extend(
        self,
        source_id: int,
        src: Source,
        keyword: str,
        need: int,
        base: CachedSourceResult | None,
    ) -> CachedSourceResult:
        """从 ``base.covered`` 续取到凑够 ``need`` 张卡片（上游已按 Audio 过滤，通常一轮即够）。"""
        items = list(base.items) if base else []
        covered = base.covered if base else 0
        has_more = True
        for _ in range(SEARCH_FETCH_ROUNDS):
            if len(items) >= need or not has_more:
                break
            limit = need - len(items)
            async with self._sem:
                raw = await self.client.search_messages(
                    src.telegram_chat_id, keyword, limit=limit, offset=covered
                )
            if not raw:
                has_more = False
                break
            covered += len(raw)
            has_more = len(raw) >= limit
            items.extend(
                message_to_card(m, src.title) for m in raw if is_audio_message(m)
            )
        result = CachedSourceResult(
            source_id=source_id,
            keyword=keyword,
            items=items,
            covered=covered,
            has_more=has_more,
            fetched_at=time.time(),
        )
        self.cache.put(result)
        return result

    def _release_inflight(self, key: str, task: asyncio.Task[CachedSourceResult]) -> None:
        """调用方可能先被取消：这里负责摘掉合并表项并消费异常，避免「未被取回」告警。"""
        self._inflight.pop(key, None)
        if not task.cancelled():
            task.exception()

    def _track_background(self, task: asyncio.Task[CachedSourceResult]) -> None:
        """超窗任务转后台：写缓存由 ``_fetch_extend`` 自己完成，这里只保引用、吞异常。"""
        if task in self._bg:
            return
        self._bg.add(task)

        def _done(t: asyncio.Task[CachedSourceResult]) -> None:
            self._bg.discard(t)
            if not t.cancelled() and t.exception() is not None:
                logger.info("background search fill failed: %s", t.exception())

        task.add_done_callback(_done)
