-- 001: 初始 schema（SDD §3.2）
CREATE TABLE IF NOT EXISTS sources (
    id                    INTEGER PRIMARY KEY,
    telegram_chat_id      INTEGER NOT NULL UNIQUE,
    username              TEXT,
    title                 TEXT NOT NULL,
    type                  TEXT NOT NULL,
    enabled               INTEGER NOT NULL DEFAULT 1,
    auto_sync             INTEGER NOT NULL DEFAULT 0,
    sync_interval_sec     INTEGER NOT NULL DEFAULT 120,
    last_message_id       INTEGER,
    media_scope           TEXT NOT NULL DEFAULT '["audio","audio_document"]',
    filters_json          TEXT,
    save_path_override    TEXT,
    dir_template_override TEXT,
    file_template_override TEXT,
    note                  TEXT,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS history (
    id             INTEGER PRIMARY KEY,
    source_id      INTEGER REFERENCES sources(id) ON DELETE SET NULL,
    chat_id        INTEGER NOT NULL,
    message_id     INTEGER NOT NULL,
    file_unique_id TEXT,
    file_id        TEXT,
    title          TEXT, artist TEXT, album TEXT,
    duration_sec   INTEGER, file_size INTEGER,
    mime           TEXT, ext TEXT, caption TEXT,
    message_date   TEXT,
    save_path      TEXT,
    status         TEXT NOT NULL,
    error          TEXT,
    created_at     TEXT NOT NULL,
    finished_at    TEXT,
    UNIQUE(chat_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_history_unique ON history(file_unique_id);
CREATE INDEX IF NOT EXISTS idx_history_status ON history(status, created_at);

CREATE TABLE IF NOT EXISTS tasks (
    id             INTEGER PRIMARY KEY,
    type           TEXT NOT NULL,
    payload_json   TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'queued',
    progress_bytes INTEGER DEFAULT 0,
    total_bytes    INTEGER,
    speed          REAL,
    retry_count    INTEGER DEFAULT 0,
    next_retry_at  TEXT,
    history_id     INTEGER REFERENCES history(id),
    error          TEXT,
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, next_retry_at);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS preview_cache (
    id              INTEGER PRIMARY KEY,
    chat_id         INTEGER NOT NULL,
    message_id      INTEGER NOT NULL,
    file_path       TEXT NOT NULL,
    file_size       INTEGER NOT NULL,
    last_access_at  TEXT NOT NULL,
    UNIQUE(chat_id, message_id)
);
