"""搜索二级缓存的 L2 仓储（SDD §2.6）。"""

from __future__ import annotations

from app.db.models import SearchCacheEntry, row_to
from app.db.store.base import _Base, utcnow


class SearchCacheStore(_Base):
    """``Store`` 的搜索缓存聚合 mixin（SDD §2.6）。"""

    def get_search_cache(self, cache_key: str) -> SearchCacheEntry | None:
        row = self._conn.execute(
            "SELECT * FROM search_cache WHERE cache_key=?", (cache_key,)
        ).fetchone()
        return row_to(SearchCacheEntry, row) if row else None

    def put_search_cache_many(self, entries: list[SearchCacheEntry]) -> None:
        """一条事务写入一批（延迟写队列一次 flush 落一批，不做 N 次 fsync）。"""
        if not entries:
            return
        with self._conn:
            self._conn.executemany(
                "INSERT INTO search_cache (cache_key, source_id, keyword, payload_json,"
                " item_count, byte_size, fetched_at, expires_at, last_access_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(cache_key) DO UPDATE SET"
                " payload_json=excluded.payload_json, item_count=excluded.item_count,"
                " byte_size=excluded.byte_size, fetched_at=excluded.fetched_at,"
                " expires_at=excluded.expires_at, last_access_at=excluded.last_access_at",
                [
                    (
                        e.cache_key,
                        e.source_id,
                        e.keyword,
                        e.payload_json,
                        e.item_count,
                        e.byte_size,
                        e.fetched_at,
                        e.expires_at,
                        e.last_access_at,
                    )
                    for e in entries
                ],
            )

    def touch_search_cache(self, cache_key: str) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE search_cache SET last_access_at=? WHERE cache_key=?",
                (utcnow(), cache_key),
            )

    def delete_search_cache(self, cache_key: str) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM search_cache WHERE cache_key=?", (cache_key,))

    def delete_search_cache_by_source(self, source_id: int) -> int:
        """删掉某个源的全部条目（源被移除/停用时用；缓存里留着它的结果只会有害）。"""
        with self._conn:
            cur = self._conn.execute("DELETE FROM search_cache WHERE source_id=?", (source_id,))
            return cur.rowcount

    def purge_search_cache(self, now: str) -> int:
        """删过期条目（TTL），返回删除行数。"""
        with self._conn:
            cur = self._conn.execute("DELETE FROM search_cache WHERE expires_at<=?", (now,))
            return cur.rowcount

    def list_search_cache_by_access(self, limit: int = 200) -> list[SearchCacheEntry]:
        """最久未访问的在前（LRU 淘汰候选）；limit 是每次取的最旧条数上限。"""
        return [
            row_to(SearchCacheEntry, r)
            for r in self._conn.execute(
                "SELECT * FROM search_cache ORDER BY last_access_at LIMIT ?", (limit,)
            )
        ]

    def search_cache_totals(self) -> tuple[int, int]:
        """返回 (总字节, 条数)：容量上限的淘汰依据（SDD §2.6）。"""
        row = self._conn.execute(
            "SELECT COALESCE(SUM(byte_size),0) AS total, COUNT(*) AS cnt FROM search_cache"
        ).fetchone()
        return int(row["total"]), int(row["cnt"])

    def delete_all_search_cache(self) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM search_cache")
