"""搜索二级缓存（SDD §2.6）：形态参照 pansou `util/cache`，落到本项目的单进程/SQLite 上。

- **L1**：进程内 LRU（``OrderedDict``）——命中免外呼，也免一次 SQLite 读。
- **L2**：SQLite ``search_cache`` 表（``app/db/migrations/005_search_cache.sql``）——跨重启存活。
- 读：L1 → L2（命中回填 L1）→ 上游（上游由 ``SearchService`` 负责）。
- 写：L1 与 L2 同步写直达（一条 upsert）。

**为什么没有 pansou 的 DelayedBatchWriteManager**：pansou 的延迟合批是为了摊薄「分片磁盘文件
多次写 + fsync」的代价；这里是本地 SQLite 单行 upsert（WAL，微秒级），批处理的收益接近零，
却要换来一个**绑定事件循环的后台任务**——而请求可能在不同 loop 上跑（Starlette TestClient
每个请求一个新 loop），那样合批队列会随 loop 一起被丢弃，L2 从此不再落盘。
少一层生命周期，比省一次微秒级写更值。

缓存粒度是「**关键词 × 源**的一次取数窗口」（``CachedSourceResult``），不是整次查询：
不同源各自命中、加删源不互相失效、后台补齐只补超窗的那一个源。

负结果按 pansou「跳过空结果缓存」的做法**不落 L2**，只在 L1 留短 TTL 标记：
挡同词突发重打，又不至于让「刚发布的歌」在 TTL 内查不到。

并发说明：store 是本地 SQLite，点操作与淘汰都是短操作，故与 ``PreviewService`` 的
服务层惯例一致——直接在 loop 线程调用（不经 ``to_thread``：``Store`` 的连接跨线程共用
需要 ``check_same_thread=False``，把服务层的调用散到线程池只会引入无谓的跨线程竞争）。
方法本身也不做成 ``async``：内部没有任何 await，做成协程只是给调用方添一次假挂起点。
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime

from app.appsettings import SearchCacheSettings
from app.db.models import SearchCacheEntry
from app.domain import (
    SearchResultCard,
    card_from_dict,
    card_key,
    card_to_dict,
    normalize_keyword,
)
from app.ports.repository import SearchCacheRepo

logger = logging.getLogger(__name__)

CACHE_KEY_VERSION = "v1"  # 键格式版本：载荷结构变了一次性作废旧键，不必写迁移
L2_EVICT_BATCH = 64  # 容量淘汰时每轮取最旧的条数（一次取一条会退化成 N 次查询）


def search_cache_key(source_id: int, keyword: str) -> str:
    """缓存键 = 源 + 规范化关键词（digest 封口，避免关键词里的分隔符拼出歧义键）。"""
    digest = hashlib.sha1(  # noqa: S324  缓存键去歧义，不用于安全
        normalize_keyword(keyword).encode("utf-8"), usedforsecurity=False
    ).hexdigest()
    return f"{CACHE_KEY_VERSION}:{source_id}:{digest}"


def _iso(epoch: float) -> str:
    """与 ``app.db.store.utcnow()`` 同格式的 UTC ISO-8601 文本（TTL 比较按文本序即时间序）。"""
    return datetime.fromtimestamp(epoch, UTC).isoformat(timespec="seconds")


def _encode(result: CachedSourceResult) -> str:
    return json.dumps(
        {
            "items": [card_to_dict(c) for c in result.items],
            "covered": result.covered,
            "has_more": result.has_more,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _decode(raw: str) -> tuple[list[SearchResultCard], int, bool]:
    """payload → (items, covered, has_more)；坏载荷按「没缓存」处理，不让一条烂行崩掉搜索。"""
    try:
        data = json.loads(raw)
        items = [card_from_dict(d) for d in data["items"]]
        return items, int(data["covered"]), bool(data["has_more"])
    except (ValueError, TypeError, KeyError) as e:
        logger.warning("search cache payload ignored: %s", e)
        return [], 0, False


@dataclass(slots=True)
class CachedSourceResult:
    """一个源在一个关键词上的取数窗口。

    ``covered`` 是已消费的上游 offset（原始结果条数，不是音频卡片数）——
    续取从它起步；``has_more`` 是「上游还有更深的结果」。

    ``from_cache`` **由消费方标注**（``SearchService`` 用 ``dataclasses.replace`` 置 True）：
    缓存里存的是取数窗口本身，不该把「谁读过它」这件事写进缓存对象，
    否则同一条 L1 记录被下一个调用方读到时会互相污染这个观测位。
    """

    source_id: int
    keyword: str
    items: list[SearchResultCard]
    covered: int
    has_more: bool
    fetched_at: float
    from_cache: bool = False


@dataclass(slots=True, frozen=True)
class SearchCacheStats:
    """搜索缓存占用与命中观测（设置页 + 排障）。"""

    entries: int
    bytes: int
    max_entries: int
    max_bytes: int
    hits: int
    misses: int


class SearchCache:
    """二级缓存门面：L1 内存 LRU + L2 SQLite（写直达）。"""

    def __init__(self, store: SearchCacheRepo, settings: SearchCacheSettings) -> None:
        self.store = store
        self.settings = settings
        self.hits = 0
        self.misses = 0
        self._l1: OrderedDict[str, CachedSourceResult] = OrderedDict()
        self._negative: OrderedDict[str, float] = OrderedDict()

    # ---- 读 ----

    def get(self, source_id: int, keyword: str) -> CachedSourceResult | None:
        """L1 → L2；L2 命中回填 L1（下一次同词连 SQLite 都不读）。"""
        key = search_cache_key(source_id, keyword)
        hit = self._l1.get(key)
        if hit is not None:
            self._l1.move_to_end(key)
            self.hits += 1
            return hit
        entry = self.store.get_search_cache(key)
        if entry is None or entry.expires_at <= _iso(time.time()):
            if entry is not None:  # 过期的 L2 行顺手清掉（不等 purge 周期）
                self.store.delete_search_cache(key)
            self.misses += 1
            return None
        items, covered, has_more = _decode(entry.payload_json)
        result = CachedSourceResult(
            source_id=source_id,
            keyword=normalize_keyword(keyword),
            items=items,
            covered=covered,
            has_more=has_more,
            fetched_at=_epoch(entry.fetched_at),
        )
        self._put_l1(key, result)
        self.store.touch_search_cache(key)
        self.hits += 1
        return result

    def is_negative(self, source_id: int, keyword: str) -> bool:
        """该源在该词上刚查到「没有歌」：短时间内不必再打一次上游。"""
        key = search_cache_key(source_id, keyword)
        marked = self._negative.get(key)
        if marked is None:
            return False
        if time.monotonic() - marked >= self.settings.negative_ttl_sec:
            del self._negative[key]
            return False
        return True

    # ---- 写 ----

    def put(self, result: CachedSourceResult) -> None:
        """写：L1 立即、L2 同一步落盘；空结果只留 L1 负标记（不落 L2）。"""
        key = search_cache_key(result.source_id, result.keyword)
        self._negative.pop(key, None)
        if not result.items:
            # 刚查到「这个源在这个词上没有结果」：留着的老 L2 行是过期事实，清掉；
            # 新事实只以 L1 短负缓存表达（不落 L2，免得空结果把缓存占满）。
            self._negative[key] = time.monotonic()
            self._negative.move_to_end(key)
            while len(self._negative) > self.settings.max_entries:
                self._negative.popitem(last=False)  # 负标记同样按条数封顶，别让它无限长
            self.store.delete_search_cache(key)
            return
        self._put_l1(key, result)
        self.store.put_search_cache_many([_entry_from(key, result, self.settings.ttl_sec)])

    # ---- 维护 ----

    def purge(self) -> int:
        """删过期 + 按条数/字节上限 LRU 淘汰（启动装配后调用一次）。返回删除条数。"""
        removed = self.store.purge_search_cache(_iso(time.time()))
        total_bytes, count = self.store.search_cache_totals()
        while count > self.settings.max_entries or total_bytes > self.settings.max_bytes:
            oldest = self.store.list_search_cache_by_access(limit=L2_EVICT_BATCH)
            if not oldest:
                break
            for entry in oldest:
                if count <= self.settings.max_entries and total_bytes <= self.settings.max_bytes:
                    break  # 已经回到上限内：这一批剩下的不动（按下限删，不多删）
                self.store.delete_search_cache(entry.cache_key)
                total_bytes -= entry.byte_size
                count -= 1
                removed += 1
        return removed

    def invalidate_source(self, source_id: int) -> int:
        """源被移除时清掉它的条目（L1 前缀 + L2 按 source_id）；返回清的 L2 行数。"""
        prefix = f"{CACHE_KEY_VERSION}:{source_id}:"
        for cache in (self._l1, self._negative):
            for key in [k for k in cache if k.startswith(prefix)]:
                del cache[key]
        return self.store.delete_search_cache_by_source(source_id)

    def clear(self) -> None:
        """清空两级缓存（设置页「清理缓存」）：都能重查，删了只是下次慢一点。"""
        self._l1.clear()
        self._negative.clear()
        self.store.delete_all_search_cache()

    def stats(self) -> SearchCacheStats:
        total_bytes, count = self.store.search_cache_totals()
        return SearchCacheStats(
            entries=count,
            bytes=total_bytes,
            max_entries=self.settings.max_entries,
            max_bytes=self.settings.max_bytes,
            hits=self.hits,
            misses=self.misses,
        )

    # ---- 内部 ----

    def _put_l1(self, key: str, result: CachedSourceResult) -> None:
        self._l1[key] = result
        self._l1.move_to_end(key)
        while len(self._l1) > self.settings.max_entries:
            self._l1.popitem(last=False)


@dataclass(slots=True)
class _QuerySnapshot:
    """一次查询的**定序前缀**（L1 专用）：已发出的卡片次序不再变动。"""

    cards: list[SearchResultCard]
    keys: set[str]
    touched_at: float


class QuerySnapshotCache:
    """定序前缀的进程内缓存（TTL + 条数上限，只服务翻页稳定性）。

    为什么需要它：每个源的窗口是按**上游自己的顺序**取回的，而我们的排序键（日期/相关度）
    与上游顺序并不一致——窗口加深后，同一批卡片的整体次序会变。若每页都按 offset 直接切
    「当前窗口」，翻页就会出现**重复行**（次序变了）或**丢行**（某条本该在前一页的卡片，
    加深后落进了已发过的区间）。实测：`q=周杰伦, page_size=5` 的第 0 页与第 1 页都出现了
    message_id 3573。

    故：首次查询定序一份前缀，之后翻页只在这份前缀上切片；窗口加深发现的**新**卡片按新次序
    过滤掉已收录的之后**追加在尾部**。于是「不重复、不丢行」是构造保证的，代价是深页的
    次序是尽力而为（新发现的更优项出现在后面，而不是插队）。它只存在于进程内：重启/过期后
    从头搜索即可，不需要跨进程一致。
    """

    def __init__(self, ttl_sec: int, max_entries: int = 32) -> None:
        self.ttl_sec = ttl_sec
        self.max_entries = max_entries
        self._snapshots: OrderedDict[str, _QuerySnapshot] = OrderedDict()

    def resolve(self, key: str, ordered: list[SearchResultCard]) -> list[SearchResultCard]:
        """把「本次按当前窗口算出的次序」并入该查询的前缀，返回可切页的完整次序。"""
        now = time.time()
        snapshot = self._snapshots.get(key)
        if snapshot is None or now - snapshot.touched_at >= self.ttl_sec:
            snapshot = _QuerySnapshot(
                cards=list(ordered), keys={card_key(c) for c in ordered}, touched_at=now
            )
            self._snapshots[key] = snapshot
            self._snapshots.move_to_end(key)
            self._trim()
            return snapshot.cards
        for card in ordered:
            row = card_key(card)
            if row in snapshot.keys:
                continue
            snapshot.keys.add(row)
            snapshot.cards.append(card)
        snapshot.touched_at = now  # 滑窗 TTL：翻页期间前缀不被过期清掉
        self._snapshots.move_to_end(key)
        return snapshot.cards

    def drop(self, key: str) -> None:
        self._snapshots.pop(key, None)

    def clear(self) -> None:
        self._snapshots.clear()

    def _trim(self) -> None:
        while len(self._snapshots) > self.max_entries:
            self._snapshots.popitem(last=False)


def _entry_from(key: str, result: CachedSourceResult, ttl_sec: int) -> SearchCacheEntry:
    now = time.time()
    payload = _encode(result)
    return SearchCacheEntry(
        cache_key=key,
        source_id=result.source_id,
        keyword=normalize_keyword(result.keyword),
        payload_json=payload,
        item_count=len(result.items),
        byte_size=len(payload.encode("utf-8")),
        fetched_at=_iso(now),
        expires_at=_iso(now + ttl_sec),
        last_access_at=_iso(now),
    )


def _epoch(iso: str) -> float:
    """iso → epoch；解不出按 0（当作很旧，只有观测用途，不影响正确性）。"""
    try:
        return datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return 0.0
