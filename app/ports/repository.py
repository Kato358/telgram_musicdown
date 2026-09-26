"""数据访问端口（DIP 的抽象侧）：服务层依赖的唯一仓储协议。

协议按聚合拆分（ISP）：
- ``SourceRepo``：音乐源 CRUD 与游标；
- ``HistoryRepo``：下载历史（入队建行、去重、状态结算）；
- ``TaskRepo``：任务台账（创建、状态推进、重试复位、恢复）;
- ``SettingsRepo``：键值设置；
- ``PreviewRepo``：试听缓存（LRU 所需的读写）；
- ``StatsRepo``：统计聚合。

``Store``（db/store.py）是唯一 SQLite 适配器，实现全部协议；
服务层与 Web 层的签名一律写 ``IStore``（或对应子协议），不写 ``Store``。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from app.db.models import History, LocalTrack, PreviewCache, Source, Task


@runtime_checkable
class SourceRepo(Protocol):
    """音乐源仓储协议（FR-SRC-01~04）。"""

    def upsert_source(self, src: Source) -> int: ...
    def get_source(self, source_id: int) -> Source | None: ...
    def get_source_by_chat(self, chat_id: int) -> Source | None: ...
    def list_sources(self, enabled_only: bool = False) -> list[Source]: ...
    def delete_source(self, source_id: int, with_history: bool = False) -> None: ...
    def set_source_cursor(self, source_id: int, last_message_id: int) -> None: ...


@runtime_checkable
class HistoryRepo(Protocol):
    """下载历史仓储协议（FR-DL-01/05）。"""

    def upsert_history(self, h: History) -> int: ...
    def get_history(self, history_id: int) -> History | None: ...
    def find_history_success(
        self, chat_id: int, message_id: int, file_unique_id: str | None = None
    ) -> History | None: ...
    def find_history_cover_source(self, title: str) -> History | None:
        """全局封面「本地优先」：按标题取最近一条已落盘的成功记录。"""
        ...
    def list_history(
        self,
        status: str | None = None,
        source_id: int | None = None,
        q: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[History]: ...
    def delete_history(self, history_id: int) -> bool: ...
    def latest_task_ids(self, history_ids: list[int]) -> dict[int, int]: ...
    def history_paths_by_id(self) -> dict[str, int]: ...
    def mark_history_status(
        self,
        history_id: int,
        status: str,
        error: str | None = None,
        save_path: str | None = None,
        finished: bool = False,
    ) -> None: ...
    def reset_history_for_retry(self, history_id: int) -> None: ...
    def set_history_display_tags(
        self, history_id: int, title: str | None, artist: str | None, album: str | None
    ) -> None: ...
    def set_history_media(
        self,
        history_id: int,
        duration_sec: int | None,
        file_size: int | None,
        bitrate: int | None,
    ) -> None: ...
    def list_history_missing_media(self, limit: int = 500) -> list[History]: ...


@runtime_checkable
class TaskRepo(Protocol):
    """任务台账仓储协议（FR-DL-01~04、NFR-05）。"""

    def create_task(self, t: Task) -> int: ...
    def get_task(self, task_id: int) -> Task | None: ...
    def list_tasks(self, status: str | None = None, limit: int = 50) -> list[Task]: ...
    def update_task(
        self,
        task_id: int,
        status: str | None = None,
        progress_bytes: int | None = None,
        total_bytes: int | None = None,
        speed: float | None = None,
        retry_count: int | None = None,
        next_retry_at: str | None = None,
        history_id: int | None = None,
        error: str | None = None,
    ) -> None: ...
    def reset_task_for_retry(self, task_id: int) -> None: ...
    def delete_task(self, task_id: int) -> bool: ...
    def recover_interrupted(self) -> int: ...
    def next_queued_task(self) -> dict[str, Any] | None: ...


@runtime_checkable
class SettingsRepo(Protocol):
    """键值设置仓储协议（FR-CFG-03）。"""

    def get_setting(self, key: str, default: str | None = None) -> str | None: ...
    def set_setting(self, key: str, value: str) -> None: ...
    def all_settings(self) -> dict[str, str]: ...


@runtime_checkable
class PreviewRepo(Protocol):
    """试听缓存仓储协议（FR-PLAY-01~03）。"""

    def get_preview(self, chat_id: int, message_id: int) -> PreviewCache | None: ...
    def put_preview(self, p: PreviewCache) -> int: ...
    def touch_preview(self, preview_id: int) -> None: ...
    def list_previews_by_access(self) -> list[PreviewCache]: ...
    def delete_preview(self, preview_id: int) -> None: ...
    def preview_totals(self) -> tuple[int, int]: ...


@runtime_checkable
class StatsRepo(Protocol):
    """统计聚合仓储协议（`GET /api/stats`）。"""

    def counts(self) -> dict[str, int]: ...
    def library_paths(self) -> list[str]: ...


@runtime_checkable
class LocalLibraryRepo(Protocol):
    """本地曲库台账协议（FR-LIB）：扫描索引的读写与筛选聚合。"""

    def upsert_local_track(self, t: LocalTrack) -> int: ...
    def get_local_track(self, track_id: int) -> LocalTrack | None: ...
    def local_track_paths(self) -> dict[str, LocalTrack]:
        """rel_path → 行（扫描时与磁盘清单对账用）。"""
        ...
    def list_local_tracks(
        self,
        q: str | None = None,
        artist: str | None = None,
        missing: bool | None = None,
        sort: str = "created",
        order: str = "desc",
        limit: int = 50,
        offset: int = 0,
    ) -> list[LocalTrack]: ...
    def count_local_tracks(
        self, q: str | None = None, artist: str | None = None
    ) -> dict[str, int]:
        """当前关键词/歌手筛选下的 present/missing 计数（页签计数用）。"""
        ...
    def local_track_bytes(self) -> int:
        """在库文件的总字节（SUM(file_size) WHERE missing=0）。"""
        ...
    def local_track_artists(self) -> list[tuple[str, int]]:
        """非空歌手聚合（artist, count），按名称排序（筛选下拉用）。"""
        ...
    def set_local_track_missing(self, track_ids: list[int], missing: bool) -> None: ...
    def delete_local_track(self, track_id: int) -> bool: ...


class IStore(
    SourceRepo,
    HistoryRepo,
    TaskRepo,
    SettingsRepo,
    PreviewRepo,
    StatsRepo,
    LocalLibraryRepo,
    Protocol,
):
    """统一仓储端口：全部子协议的聚合（服务层依赖它，不依赖 ``Store``）。"""
