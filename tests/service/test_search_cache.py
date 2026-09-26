"""搜索二级缓存单测（SDD §2.6）：键规范化、L1 LRU、负缓存、L2 写直达与 TTL/容量淘汰。

纯缓存层行为，不经过搜索服务（服务侧链路见 tests/service/test_search.py）。
"""

from __future__ import annotations

import time
from pathlib import Path

from app.appsettings import SearchCacheSettings
from app.db.store import Store
from app.domain import SearchResultCard
from app.services.search_cache import (
    CachedSourceResult,
    QuerySnapshotCache,
    SearchCache,
    search_cache_key,
)


def _card(message_id: int, *, unique: str | None = None) -> SearchResultCard:
    return SearchResultCard(
        chat_id=-1001,
        message_id=message_id,
        title="晴天",
        artist="周杰伦",
        duration_sec=269,
        file_size=100,
        ext="mp3",
        mime="audio/mpeg",
        channel_title="频道",
        message_date="2026-01-01T00:00:00+00:00",
        caption=None,
        file_unique_id=unique if unique is not None else f"uniq_{message_id}",
    )


def _result(
    source_id: int, keyword: str, *, count: int = 1, covered: int | None = None
) -> CachedSourceResult:
    return CachedSourceResult(
        source_id=source_id,
        keyword=keyword,
        items=[_card(i) for i in range(count)],
        covered=covered if covered is not None else count,
        has_more=False,
        fetched_at=time.time(),
    )


def _cache(tmp_path: Path, **overrides: object) -> tuple[SearchCache, Store]:
    store = Store(tmp_path / "app.db")
    return SearchCache(store, SearchCacheSettings(**overrides)), store  # type: ignore[arg-type]


# ---- 键 ----


def test_key_is_stable_across_case_and_whitespace() -> None:
    assert search_cache_key(1, "Jay  Chou") == search_cache_key(1, " jay chou ")
    assert search_cache_key(1, "晴天") != search_cache_key(2, "晴天")


# ---- L1 / L2 ----


def test_put_writes_l1_and_l2_in_one_step(tmp_path: Path) -> None:
    cache, store = _cache(tmp_path)

    cache.put(_result(1, "晴天", count=2))

    assert store.search_cache_totals()[1] == 1  # 写直达：put 之后 L2 就有行
    hit = cache.get(1, "晴天")
    assert hit is not None and len(hit.items) == 2
    assert cache.hits == 1


def test_l2_backfills_l1_after_eviction(tmp_path: Path) -> None:
    cache, _ = _cache(tmp_path, max_entries=1)
    cache.put(_result(1, "晴天", count=3))
    cache.put(_result(2, "晴天", count=1))  # 把源 1 挤出 L1

    hit = cache.get(1, "晴天")  # 改从 L2 读，并回填 L1

    assert hit is not None and len(hit.items) == 3
    assert hit.covered == 3
    assert hit.from_cache is False  # 命中由消费方标注，缓存层不写这个观测位
    assert cache.hits == 1


def test_l2_is_readable_by_a_fresh_cache_instance(tmp_path: Path) -> None:
    cache, store = _cache(tmp_path)
    cache.put(_result(1, "晴天", count=3))

    fresh = SearchCache(store, SearchCacheSettings())
    hit = fresh.get(1, "晴天")

    assert hit is not None and len(hit.items) == 3


def test_put_upserts_same_key(tmp_path: Path) -> None:
    cache, store = _cache(tmp_path)
    cache.put(_result(1, "晴天", count=1))
    cache.put(_result(1, "晴天", count=5))

    assert store.search_cache_totals()[1] == 1  # 同键只一行，后写的赢
    hit = cache.get(1, "晴天")
    assert hit is not None and len(hit.items) == 5


def test_l1_evicts_oldest_beyond_max_entries(tmp_path: Path) -> None:
    cache, store = _cache(tmp_path, max_entries=2)
    for i in range(3):
        cache.put(_result(i, "晴天"))

    assert cache.get(0, "晴天") is not None  # L1 里没了，但 L2 会回填
    assert len(cache._l1) <= 2
    assert store.search_cache_totals()[1] == 3
    assert cache.get(1, "晴天") is not None


def test_recent_access_is_not_the_one_evicted(tmp_path: Path) -> None:
    cache, _ = _cache(tmp_path, max_entries=2)
    cache.put(_result(0, "晴天"))
    cache.put(_result(1, "晴天"))
    cache.get(0, "晴天")  # 触达 0，让它变成「最近用过的」
    cache.put(_result(2, "晴天"))

    assert search_cache_key(0, "晴天") in cache._l1
    assert search_cache_key(1, "晴天") not in cache._l1


def test_expired_l2_row_is_ignored(tmp_path: Path) -> None:
    cache, store = _cache(tmp_path, ttl_sec=-1)  # 写完即过期
    cache.put(_result(1, "晴天"))

    fresh = SearchCache(store, SearchCacheSettings())
    assert fresh.get(1, "晴天") is None


# ---- 负缓存 ----


def test_empty_result_is_negative_marked_in_l1_only(tmp_path: Path) -> None:
    cache, store = _cache(tmp_path)
    cache.put(_result(1, "查无此歌", count=0))

    assert cache.is_negative(1, "查无此歌") is True
    assert cache.get(1, "查无此歌") is None
    assert store.search_cache_totals() == (0, 0)  # 空结果不落 L2


def test_empty_result_drops_stale_l2_row(tmp_path: Path) -> None:
    cache, store = _cache(tmp_path)
    cache.put(_result(1, "晴天", count=2))
    assert store.search_cache_totals()[1] == 1

    cache.put(_result(1, "晴天", count=0))

    # 「刚查到这个源没有结果」是更新的事实，旧 L2 行必须清掉
    assert store.search_cache_totals() == (0, 0)


def test_expired_negative_marker_clears(tmp_path: Path) -> None:
    cache, _ = _cache(tmp_path, negative_ttl_sec=0)
    cache.put(_result(1, "查无此歌", count=0))

    assert cache.is_negative(1, "查无此歌") is False


def test_negative_markers_are_bounded(tmp_path: Path) -> None:
    # 负标记按关键词 × 源增长：不封顶就是一条内存泄漏路径
    cache, _ = _cache(tmp_path, max_entries=2)
    for source_id in range(4):
        cache.put(_result(source_id, "查无此歌", count=0))

    assert cache.is_negative(0, "查无此歌") is False
    assert cache.is_negative(3, "查无此歌") is True


def test_non_empty_result_clears_negative_marker(tmp_path: Path) -> None:
    cache, _ = _cache(tmp_path)
    cache.put(_result(1, "晴天", count=0))
    cache.put(_result(1, "晴天", count=1))

    assert cache.is_negative(1, "晴天") is False


# ---- 维护 ----


def test_purge_drops_expired_rows(tmp_path: Path) -> None:
    cache, store = _cache(tmp_path, ttl_sec=-1)
    cache.put(_result(1, "晴天"))

    assert cache.purge() == 1
    assert store.search_cache_totals() == (0, 0)


def test_purge_enforces_entry_capacity_by_lru(tmp_path: Path) -> None:
    cache, store = _cache(tmp_path, max_entries=2, ttl_sec=600)
    for i in range(4):
        cache.put(_result(i, "晴天"))

    removed = cache.purge()

    assert removed == 2  # 按下限删，不多删
    assert store.search_cache_totals()[1] == 2


def test_purge_enforces_byte_capacity(tmp_path: Path) -> None:
    cache, store = _cache(tmp_path, max_entries=1000, max_bytes=1, ttl_sec=600)
    cache.put(_result(1, "晴天", count=2))

    assert cache.purge() >= 1
    assert store.search_cache_totals() == (0, 0)


def test_invalidate_source_removes_only_that_source(tmp_path: Path) -> None:
    cache, store = _cache(tmp_path)
    cache.put(_result(1, "晴天"))
    cache.put(_result(2, "晴天"))

    removed = cache.invalidate_source(1)

    assert removed == 1
    assert store.get_search_cache(search_cache_key(1, "晴天")) is None
    assert store.get_search_cache(search_cache_key(2, "晴天")) is not None


def test_clear_empties_both_levels(tmp_path: Path) -> None:
    cache, store = _cache(tmp_path)
    cache.put(_result(1, "晴天"))

    cache.clear()

    assert store.search_cache_totals() == (0, 0)
    assert cache.get(1, "晴天") is None


def test_stats_reports_occupancy_and_hit_rate(tmp_path: Path) -> None:
    cache, _ = _cache(tmp_path)
    cache.put(_result(1, "晴天", count=2))
    cache.get(1, "晴天")
    cache.get(9, "晴天")

    stats = cache.stats()

    assert stats.entries == 1
    assert stats.bytes > 0
    assert (stats.hits, stats.misses) == (1, 1)


# ---- 翻页用的定序前缀 ----


def test_snapshot_keeps_prefix_and_appends_new_rows() -> None:
    snapshots = QuerySnapshotCache(ttl_sec=60)
    snapshots.resolve("q", [_card(1), _card(2)])

    # 窗口加深后整体次序变了（3 更优）：已发出的前缀不动，新行追加在后 —— 翻页因此不重不丢
    grown = snapshots.resolve("q", [_card(3), _card(1), _card(2)])

    assert [c.message_id for c in grown] == [1, 2, 3]


def test_snapshot_dedupes_by_row_key() -> None:
    snapshots = QuerySnapshotCache(ttl_sec=60)
    snapshots.resolve("q", [_card(1, unique="same")])

    grown = snapshots.resolve("q", [_card(1, unique="same"), _card(2, unique="other")])

    assert [c.message_id for c in grown] == [1, 2]


def test_snapshot_expires_and_is_rebuilt() -> None:
    snapshots = QuerySnapshotCache(ttl_sec=0)
    snapshots.resolve("q", [_card(1), _card(2)])

    rebuilt = snapshots.resolve("q", [_card(3), _card(1), _card(2)])

    assert [c.message_id for c in rebuilt] == [3, 1, 2]


def test_snapshot_is_bounded_and_drop_forgets() -> None:
    snapshots = QuerySnapshotCache(ttl_sec=60, max_entries=1)
    snapshots.resolve("a", [_card(1)])
    snapshots.resolve("b", [_card(2)])  # 把 a 挤出（上限 1）

    assert [c.message_id for c in snapshots.resolve("a", [_card(3)])] == [3]

    snapshots.drop("b")
    assert [c.message_id for c in snapshots.resolve("b", [_card(4)])] == [4]
