-- 003: 本地曲库索引（FR-LIB）：扫描 save_path 下音频文件的独立台账。
-- 文件被删除后记录保留（missing=1），曲库里仍可见、可重新下载；
-- history_id 指回下载记录（该记录被删则置 NULL，曲库行不受影响）。
CREATE TABLE IF NOT EXISTS local_tracks (
    id            INTEGER PRIMARY KEY,
    rel_path      TEXT NOT NULL UNIQUE,
    file_name     TEXT NOT NULL,
    ext           TEXT,
    title         TEXT,
    artist        TEXT,
    album         TEXT,
    duration_sec  INTEGER,
    file_size     INTEGER,
    bitrate       INTEGER,
    mtime         REAL,
    missing       INTEGER NOT NULL DEFAULT 0,
    history_id    INTEGER REFERENCES history(id) ON DELETE SET NULL,
    first_seen_at TEXT NOT NULL,
    scanned_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_local_tracks_missing ON local_tracks(missing, first_seen_at);
CREATE INDEX IF NOT EXISTS idx_local_tracks_artist ON local_tracks(artist);
