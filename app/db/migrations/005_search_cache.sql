-- 搜索二级缓存（SDD §2.6）：L2 持久层，条目 = 「关键词 × 源」的一次取数窗口。
-- L1 是进程内 LRU（app/services/search_cache.py），本表让它跨重启存活。
-- cache_key 由 (source_id, 规范化关键词) 派生，故 keyword/source_id 冗余存一份便于排查与按源失效。
CREATE TABLE IF NOT EXISTS search_cache (
    cache_key      TEXT PRIMARY KEY,
    source_id      INTEGER NOT NULL,
    keyword        TEXT NOT NULL,
    payload_json   TEXT NOT NULL,
    item_count     INTEGER NOT NULL,
    byte_size      INTEGER NOT NULL,
    fetched_at     TEXT NOT NULL,
    expires_at     TEXT NOT NULL,
    last_access_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_search_cache_expires ON search_cache(expires_at);
CREATE INDEX IF NOT EXISTS idx_search_cache_access ON search_cache(last_access_at);
CREATE INDEX IF NOT EXISTS idx_search_cache_source ON search_cache(source_id);
