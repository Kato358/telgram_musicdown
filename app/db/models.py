"""SQLite 表定义（SDD §3.2）。

行以 dataclass 表示；字段名与 SDD §3.2 一致。全部 SQL 收口 db/store.py（§2.6）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeVar

HistoryT = TypeVar("HistoryT")


@dataclass(slots=True)
class Source:
    id: int | None
    telegram_chat_id: int
    title: str
    type: str  # channel|group|user
    username: str | None = None
    enabled: int = 1
    media_scope: str = '["audio","audio_document"]'
    filters_json: str | None = None
    save_path_override: str | None = None
    dir_template_override: str | None = None
    file_template_override: str | None = None
    note: str | None = None
    created_at: str = ""
    updated_at: str = ""


@dataclass(slots=True)
class History:
    id: int | None
    chat_id: int
    message_id: int
    source_id: int | None = None
    file_unique_id: str | None = None
    file_id: str | None = None
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    duration_sec: int | None = None
    file_size: int | None = None
    bitrate: int | None = None
    mime: str | None = None
    ext: str | None = None
    caption: str | None = None
    message_date: str | None = None
    save_path: str | None = None
    status: str = "queued"
    error: str | None = None
    created_at: str = ""
    finished_at: str | None = None


@dataclass(slots=True)
class Task:
    id: int | None
    type: str  # search|link|bot|sync|preview
    payload_json: str
    status: str = "queued"  # queued|downloading|paused|success|failed|skipped|cancelled
    progress_bytes: int = 0
    total_bytes: int | None = None
    speed: float | None = None
    retry_count: int = 0
    next_retry_at: str | None = None
    history_id: int | None = None
    error: str | None = None
    created_at: str = ""


@dataclass(slots=True)
class PreviewCache:
    id: int | None
    chat_id: int
    message_id: int
    file_path: str
    file_size: int
    last_access_at: str


@dataclass(slots=True)
class LocalTrack:
    """本地曲库台账行（FR-LIB）：save_path 下音频文件的扫描索引。

    rel_path 相对曲库根目录（posix 分隔符），部署目录整体搬家记录仍有效；
    missing=1 表示文件已不在磁盘——记录保留，曲库可见（可重新下载）。
    """

    id: int | None
    rel_path: str
    file_name: str
    ext: str | None = None
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    duration_sec: int | None = None
    file_size: int | None = None
    bitrate: int | None = None
    mtime: float | None = None
    missing: int = 0
    history_id: int | None = None
    first_seen_at: str = ""
    scanned_at: str = ""


def row_to(cls: type[HistoryT], row: Any) -> HistoryT:
    """sqlite3.Row → dataclass 转换（编码规范 §2.2）。"""
    return cls(**{f: row[f] for f in row.keys()})  # noqa: SIM118  sqlite3.Row
