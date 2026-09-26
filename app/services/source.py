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

两种模式（FR-SEARCH-01，v0.14；``SearchSettings.mode``）：
- ``sources``（默认）：上面这套逐源链路，只搜「已添加且启用」的音乐源，结果按源分门别类；
- ``global``：一次 ``messages.searchGlobal`` 覆盖账号加入的全部对话——**不需要音乐源**，
  请求数从 N 降到 1，代价是结果按全局时间序（无法限定源集合，只能拿回按 ``chat_id`` 归源）、
  单次上游失败即整次失败（没有「逐源不可达」这一档）。缓存窗口与翻页前缀语义与逐源一致，
  缓存键用 ``GLOBAL_CACHE_SCOPE`` 这个保留 scope（``searchGlobal`` 的结果不依赖任何源行）。
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

from app.appsettings import SEARCH_MODE_GLOBAL, SEARCH_MODE_SOURCES, SearchSettings
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
from app.ports.music import MusicSourceIndexProto, MusicSourceProto
from app.registry import SourceRegistry
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


# searchGlobal 的缓存/快照 scope 标记（FR-SEARCH-01 global 模式）。
# 全局结果不依赖任何源行，但复用同一套「关键词 × 取数窗口」缓存与翻页前缀：
# sources.id 从 1 起，0 不会与任何源相撞，故拿它当保留 scope。
GLOBAL_CACHE_SCOPE = 0


def _snapshot_key(
    keyword: str,
    scope: str,
    sort: str,
    *,
    page_size: int,
    fields: list[str] | None,
    filters: dict[str, Any] | None,
) -> str:
    """翻页前缀的身份：同关键词 + 同取数范围 + 同排序/字段/筛选 + 同页大小，才算同一次翻页。

    ``scope`` 是取数范围的字符串标识：逐源模式 = 逗号分隔的源 id 集合，全局模式 = ``"global"``。
    """
    return "|".join(
        (
            normalize_keyword(keyword),
            scope,
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

    async def search_global(self, query: str, limit: int) -> list[dict[str, Any]]: ...

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
    """关键词搜索（FR-SEARCH-01~04）：二级缓存 + 并发扇出 + 同步窗口。

    扇出的对象是 ``MusicSourceProto``——音乐源频道与在线源平台在同一层，合并、
    筛选、排序、去重、切页对两者一视同仁。``client`` 仍留在本类里，但只服务
    searchGlobal 与全局模式的分页，那是 Telegram 独有能力（SDD §2.7）。
    """

    def __init__(
        self,
        store: IStore,
        client: SourceClientProto,
        cache: SearchCache | None = None,
        settings: SearchSettings | None = None,
        registry: MusicSourceIndexProto | None = None,
    ) -> None:
        self.store = store
        self.client = client
        # registry 缺省就地拼一个「只有音乐源」的：测试与一次性脚本不必为在线源
        # 操心，而装配路径（容器）显式注入带在线源的那个。
        self.registry: MusicSourceIndexProto = registry or SourceRegistry(store, client, client)
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
        """搜索（FR-SEARCH-01~03）：按 ``settings.mode`` 走逐源扇出或 searchGlobal。

        逐源模式：每个源先备齐 ``(page+1)*page_size`` 张卡片再合并（k 路归并的取数下界），
        并在**同一份定序前缀**上切页（见 ``QuerySnapshotCache``）：旧实现把同一页的
        offset 在上游与本地各减一次，单源时 ``page>0`` 恒空；而按当前窗口直接切页，
        又会因窗口加深导致重复行与丢行。全局模式见 ``_search_global``。
        """
        keyword = q.strip()
        size = min(max(1, page_size or self.settings.page_size), self.settings.max_page_size)
        page = max(0, page)
        if self.settings.global_mode:
            return await self._search_global(
                keyword,
                source_ids,
                page,
                size,
                fields=fields,
                filters=filters,
                sort=sort,
                refresh=refresh,
            )
        targets = [
            target
            for target in self.registry.targets()
            if source_ids is None or target.scope_id in source_ids
        ]
        if not targets:
            return SearchResponse(
                results=[], meta={"reason": "no_enabled_sources", "mode": SEARCH_MODE_SOURCES}
            )
        need = (page + 1) * size
        snapshot_key = _snapshot_key(
            keyword,
            ",".join(str(target.scope_id) for target in targets),
            sort,
            page_size=size,
            fields=fields,
            filters=filters,
        )
        if refresh:
            # 强制重取：定序前缀也作废，否则会拿旧次序套新结果
            self._snapshots.drop(snapshot_key)

        tasks = {
            target.scope_id: asyncio.create_task(
                self._source_result(target, keyword, need, refresh=refresh)
            )
            for target in targets
        }
        done, _ = await asyncio.wait(set(tasks.values()), timeout=self.settings.sync_window_sec)

        per_source: list[CachedSourceResult] = []
        unreachable: list[dict[str, Any]] = []
        pending_sources: list[int] = []
        for target in targets:
            task = tasks[target.scope_id]
            if task not in done:
                # 超窗：不取消、不等待——它继续跑并自己写缓存，下次同词直接命中完整结果。
                pending_sources.append(target.scope_id)
                self._track_background(task)
                continue
            try:
                per_source.append(task.result())
            except SourceUnreachableError as e:
                unreachable.append({"source_id": target.scope_id, "reason": e.reason})
            except Exception:  # noqa: BLE001  单源崩掉只该让它缺席，不该废掉整次搜索
                logger.exception("search failed source_id=%s", target.scope_id)
                unreachable.append({"source_id": target.scope_id, "reason": "error"})

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
                "mode": SEARCH_MODE_SOURCES,
            },
        )

    # ---- 全局模式（FR-SEARCH-01 global，v0.14）----

    async def _search_global(
        self,
        keyword: str,
        source_ids: list[int] | None,
        page: int,
        size: int,
        *,
        fields: list[str] | None,
        filters: dict[str, Any] | None,
        sort: str,
        refresh: bool,
    ) -> SearchResponse:
        """searchGlobal 链路：一次上游调用覆盖账号全部对话，本地归源/筛选/排序/切页。

        与逐源模式的三点语义差异（都是 searchGlobal 的固有限制，不是实现取舍）：
        - **不需要音乐源**：取数范围 = 账号加入的对话，故「没有启用源」不是错误；
        - **无法限定源集合**：结果按全局时间序，故 ``source_ids`` 只能在取回后按
          ``chat_id`` 过滤（界面在全局模式下不提供源选择，这是给 API 调用方的入口）；
        - **单次失败即整次失败**：没有「逐源不可达」这一档，FloodWait 直接以错误透出。
        """
        need = (page + 1) * size
        snapshot_key = _snapshot_key(
            keyword, "global", sort, page_size=size, fields=fields, filters=filters
        )
        if refresh:
            self._snapshots.drop(snapshot_key)
        try:
            window = await self._global_result(keyword, need, refresh=refresh)
        except SourceUnreachableError as e:
            if e.reason == "flood_wait":
                raise SourceUnreachableError(
                    "flood_wait",
                    "Telegram 限流：稍等几秒再搜（全局搜索一次请求覆盖全部对话，等几秒即可）",
                ) from e
            raise

        tokens = keyword_tokens(keyword)
        parsed = SearchFilters.parse(filters)
        cards = [
            card
            for card in window.items
            if parsed.matches(card) and matches_fields(card, tokens, fields)
        ]
        if source_ids is not None:
            wanted = {
                s.telegram_chat_id for s in self.store.list_sources() if s.id in set(source_ids)
            }
            cards = [card for card in cards if card.chat_id in wanted]
        ordered = dedupe_cards(sort_cards(cards, sort, keyword))
        ordered = self._snapshots.resolve(snapshot_key, ordered)
        start = page * size
        return SearchResponse(
            results=ordered[start : start + size],
            meta={
                "unreachable": [],
                "partial": False,
                "pending_sources": [],
                "page": page,
                "page_size": size,
                # 上游还有更深的结果：全局搜索的「还有更多」只能靠窗口是否取满来判断
                "has_more": start + size < len(ordered) or window.has_more,
                "sort": sort if sort in SEARCH_SORTS else "relevance",
                "filters": parsed.active,
                "cache": {"hits": int(window.from_cache), "misses": int(not window.from_cache)},
                "mode": SEARCH_MODE_GLOBAL,
            },
        )

    async def _global_result(self, keyword: str, need: int, *, refresh: bool) -> CachedSourceResult:
        """全局取数窗口：缓存够深就直接用，否则合并一次上游调用并写回缓存。

        与逐源的差别在「续取」不存在：searchGlobal 没有 offset 参数，只能「从头要 N 条」，
        故窗口就是「从最新往前的 N 条」，深翻页靠把 ``need`` 调大重取（上游约 10k 条上限）。
        """
        base: CachedSourceResult | None = None
        if not refresh:
            base = self.cache.get(GLOBAL_CACHE_SCOPE, keyword)
            if base is not None and (len(base.items) >= need or not base.has_more):
                return replace(base, from_cache=True)
            if base is None and self.cache.is_negative(GLOBAL_CACHE_SCOPE, keyword):
                return CachedSourceResult(
                    source_id=GLOBAL_CACHE_SCOPE,
                    keyword=keyword,
                    items=[],
                    covered=0,
                    has_more=False,
                    fetched_at=time.time(),
                    from_cache=True,
                )
        key = f"{search_cache_key(GLOBAL_CACHE_SCOPE, keyword)}#{need}"
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.create_task(self._fetch_global(keyword, need))
            self._inflight[key] = task
            task.add_done_callback(partial(self._release_inflight, key))
        return await task

    async def _fetch_global(self, keyword: str, need: int) -> CachedSourceResult:
        """一次 searchGlobal 取回 ``need`` 条（上游已按 Audio 过滤），按消息自带的频道名标来源。"""
        async with self._sem:
            raw = await self.client.search_global(keyword, limit=need)
        items = [
            message_to_card(m, m.get("chat_title")) for m in raw if is_audio_message(m)
        ]
        result = CachedSourceResult(
            source_id=GLOBAL_CACHE_SCOPE,
            keyword=keyword,
            items=items,
            covered=len(raw),
            has_more=len(raw) >= need,
            fetched_at=time.time(),
        )
        self.cache.put(result)
        return result

    def apply_settings(self, settings: SearchSettings) -> None:
        """设置页保存后即时刷新（FR-CFG-03）：模式/扇出/窗口/分页与缓存 TTL 现读现生效。

        扇出上限重建信号量（在飞的调用持旧信号量跑完，不影响正确性）；定序前缀的 TTL
        跟着更新——它只决定「翻页前缀活多久」，不参与结果正确性。
        """
        self.settings = settings
        self.cache.settings = settings.cache
        self._snapshots.ttl_sec = settings.cache.ttl_sec
        self._sem = asyncio.Semaphore(settings.fanout)

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
        target: MusicSourceProto,
        keyword: str,
        need: int,
        *,
        refresh: bool,
    ) -> CachedSourceResult:
        """一个来源的取数窗口：缓存够用就直接回，否则合并一次上游调用并写回缓存。"""
        source_id = target.scope_id
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
            task = asyncio.create_task(self._fetch_extend(target, keyword, need, base))
            self._inflight[key] = task
            task.add_done_callback(partial(self._release_inflight, key))
        return await task

    async def _fetch_extend(
        self,
        target: MusicSourceProto,
        keyword: str,
        need: int,
        base: CachedSourceResult | None,
    ) -> CachedSourceResult:
        """从 ``base.covered`` 续取到凑够 ``need`` 张卡片。

        「续取几轮才够」是各来源自己的事：Telegram 上游混着非音频消息，要多翻；
        在线源一轮就是纯音乐。协议把这件事收在 ``MusicSourceProto.search`` 里，
        本层只管把窗口接到缓存上。
        """
        items = list(base.items) if base else []
        async with self._sem:
            window = await target.search(keyword, need - len(items), base.covered if base else 0)
        items.extend(window.items)
        result = CachedSourceResult(
            source_id=target.scope_id,
            keyword=keyword,
            items=items,
            covered=window.consumed,
            has_more=window.has_more,
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
