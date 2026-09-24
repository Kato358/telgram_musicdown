"""仓储层：全部 SQL 收口于此（编码规范 §2.6）。

- 参数化查询（``?`` 占位），禁止 f-string/``%`` 拼 SQL。
- 事务边界在 store 方法内；写 history 与 tasks 关联更新同事务。
- 迁移：db/migrations/NNN_*.sql 顺序执行并记 schema_version。
- 时间戳统一 UTC ISO-8601 文本，展示层转本地。
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.db.models import History, PreviewCache, Source, Task, row_to

logger = logging.getLogger(__name__)


def utcnow() -> str:
    """UTC ISO-8601 文本（store 约定）。"""
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store:
    """SQLite 仓储。同步 sqlite3，async 侧经 to_thread 调用（§2.3）。"""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    def _migrate(self) -> None:
        self._conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
        row = self._conn.execute("SELECT version FROM schema_version").fetchone()
        current = row["version"] if row else 0
        mig_dir = Path(__file__).parent / "migrations"
        for sql_file in sorted(mig_dir.glob("*.sql")):
            version = int(sql_file.name.split("_", 1)[0])
            if version <= current:
                continue
            logger.info("applying migration %s", sql_file.name)
            self._conn.executescript(sql_file.read_text(encoding="utf-8"))
            self._conn.execute("DELETE FROM schema_version")
            self._conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ---- sources ----

    def upsert_source(self, src: Source) -> int:
        now = utcnow()
        with self._conn:
            if src.id is None:
                cur = self._conn.execute(
                    "INSERT INTO sources (telegram_chat_id, username, title, type, enabled,"
                    " auto_sync, sync_interval_sec, last_message_id, media_scope,"
                    " filters_json, save_path_override, dir_template_override,"
                    " file_template_override, note, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        src.telegram_chat_id,
                        src.username,
                        src.title,
                        src.type,
                        src.enabled,
                        src.auto_sync,
                        src.sync_interval_sec,
                        src.last_message_id,
                        src.media_scope,
                        src.filters_json,
                        src.save_path_override,
                        src.dir_template_override,
                        src.file_template_override,
                        src.note,
                        now,
                        now,
                    ),
                )
                return int(cur.lastrowid)  # type: ignore[arg-type]  # sqlite3 lastrowid 运行时必为 int
            self._conn.execute(
                "UPDATE sources SET telegram_chat_id=?, username=?, title=?, type=?,"
                " enabled=?, auto_sync=?, sync_interval_sec=?, last_message_id=?,"
                " media_scope=?, filters_json=?, save_path_override=?,"
                " dir_template_override=?, file_template_override=?, note=?, updated_at=?"
                " WHERE id=?",
                (
                    src.telegram_chat_id,
                    src.username,
                    src.title,
                    src.type,
                    src.enabled,
                    src.auto_sync,
                    src.sync_interval_sec,
                    src.last_message_id,
                    src.media_scope,
                    src.filters_json,
                    src.save_path_override,
                    src.dir_template_override,
                    src.file_template_override,
                    src.note,
                    now,
                    src.id,
                ),
            )
            return src.id

    def get_source(self, source_id: int) -> Source | None:
        row = self._conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        return row_to(Source, row) if row else None

    def get_source_by_chat(self, chat_id: int) -> Source | None:
        row = self._conn.execute(
            "SELECT * FROM sources WHERE telegram_chat_id=?", (chat_id,)
        ).fetchone()
        return row_to(Source, row) if row else None

    def list_sources(self, enabled_only: bool = False) -> list[Source]:
        sql = "SELECT * FROM sources"
        if enabled_only:
            sql += " WHERE enabled=1"
        sql += " ORDER BY id"
        return [row_to(Source, r) for r in self._conn.execute(sql)]

    def delete_source(self, source_id: int, with_history: bool = False) -> None:
        """删除源；with_history 同时删该源下载记录（不删磁盘文件，FR-SRC-03）。"""
        with self._conn:
            if with_history:
                self._conn.execute("DELETE FROM history WHERE source_id=?", (source_id,))
            self._conn.execute("DELETE FROM sources WHERE id=?", (source_id,))

    def set_source_cursor(self, source_id: int, last_message_id: int) -> None:
        """游标写入 sources.last_message_id（FR-SRC-04）。"""
        with self._conn:
            self._conn.execute(
                "UPDATE sources SET last_message_id=?, updated_at=? WHERE id=?",
                (last_message_id, utcnow(), source_id),
            )

    # ---- history ----

    def upsert_history(self, h: History) -> int:
        """写 history；同一 (chat_id, message_id) 再次入队时收敛到同一行并重置为本次状态。

        行在入队时就存在（status=queued），下载结束再改状态；重试同一消息不该新增行
        （tasks.history_id 与历史页都指向它）。
        """
        now = utcnow()
        with self._conn:
            if h.id is None:
                cur = self._conn.execute(
                    "INSERT INTO history (source_id, chat_id, message_id, file_unique_id,"
                    " file_id, title, artist, album, duration_sec, file_size, mime, ext,"
                    " caption, message_date, save_path, status, error, created_at,"
                    " finished_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                    " ON CONFLICT(chat_id, message_id) DO UPDATE SET"
                    " source_id=excluded.source_id, file_unique_id=excluded.file_unique_id,"
                    " file_id=excluded.file_id, title=excluded.title, artist=excluded.artist,"
                    " album=excluded.album, duration_sec=excluded.duration_sec,"
                    " file_size=excluded.file_size, mime=excluded.mime, ext=excluded.ext,"
                    " caption=excluded.caption, message_date=excluded.message_date,"
                    " status=excluded.status, error=NULL, finished_at=NULL"
                    " RETURNING id",
                    (
                        h.source_id,
                        h.chat_id,
                        h.message_id,
                        h.file_unique_id,
                        h.file_id,
                        h.title,
                        h.artist,
                        h.album,
                        h.duration_sec,
                        h.file_size,
                        h.mime,
                        h.ext,
                        h.caption,
                        h.message_date,
                        h.save_path,
                        h.status,
                        h.error,
                        now,
                        h.finished_at,
                    ),
                )
                row = cur.fetchone()
                return int(row["id"])
            self._conn.execute(
                "UPDATE history SET source_id=?, file_unique_id=?, file_id=?, title=?,"
                " artist=?, album=?, duration_sec=?, file_size=?, mime=?, ext=?,"
                " caption=?, message_date=?, save_path=?, status=?, error=?,"
                " finished_at=? WHERE id=?",
                (
                    h.source_id,
                    h.file_unique_id,
                    h.file_id,
                    h.title,
                    h.artist,
                    h.album,
                    h.duration_sec,
                    h.file_size,
                    h.mime,
                    h.ext,
                    h.caption,
                    h.message_date,
                    h.save_path,
                    h.status,
                    h.error,
                    h.finished_at,
                    h.id,
                ),
            )
            return h.id

    def get_history(self, history_id: int) -> History | None:
        row = self._conn.execute("SELECT * FROM history WHERE id=?", (history_id,)).fetchone()
        return row_to(History, row) if row else None

    def find_history_success(
        self, chat_id: int, message_id: int, file_unique_id: str | None = None
    ) -> History | None:
        """去重规则 1/2（FR-DL-05）：(chat_id, message_id) 或 file_unique_id 已成功。"""
        if file_unique_id:
            row = self._conn.execute(
                "SELECT * FROM history WHERE file_unique_id=? AND status='success'",
                (file_unique_id,),
            ).fetchone()
            if row:
                return row_to(History, row)
        row = self._conn.execute(
            "SELECT * FROM history WHERE chat_id=? AND message_id=? AND status='success'",
            (chat_id, message_id),
        ).fetchone()
        return row_to(History, row) if row else None

    def list_history(
        self,
        status: str | None = None,
        source_id: int | None = None,
        q: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[History]:
        """按状态（可多值）/ 源 / 关键词分页取历史行，新的在前。

        `status` 允许逗号分隔的一组状态（下载页的「进行中」页签 = 等待/下载中/已暂停）：
        值仍全部走 ``?`` 参数，SQL 文本里只有问号，拼接的只是占位符个数。
        """
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            values = [value for value in status.split(",") if value]
            if len(values) == 1:
                clauses.append("status=?")
                params += values
            elif values:
                clauses.append("status IN (" + ",".join(["?"] * len(values)) + ")")
                params += values
        if source_id is not None:
            clauses.append("source_id=?")
            params.append(source_id)
        if q:
            clauses.append("(title LIKE ? OR artist LIKE ? OR caption LIKE ?)")
            like = f"%{q}%"
            params += [like, like, like]
        sql = "SELECT * FROM history"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
        params += [limit, offset]
        return [row_to(History, r) for r in self._conn.execute(sql, params)]

    def latest_task_ids(self, history_ids: list[int]) -> dict[int, int]:
        """每行历史记录当前挂着的任务 id（同一行取 id 最大者）。

        历史行是持久记录、任务是它的执行态：重试复用同一条任务、重新下载新开一条，
        所以「最新」就是「这条记录现在在跑谁」；台账被删过的行不出现在结果里。
        """
        if not history_ids:
            return {}
        marks = ",".join("?" * len(history_ids))
        # 占位符按长度生成、值全部参数化，没有用户输入进来
        sql = (
            f"SELECT history_id, MAX(id) AS task_id FROM tasks WHERE history_id IN ({marks})"  # noqa: S608
            " GROUP BY history_id"
        )
        rows = self._conn.execute(sql, history_ids)
        return {int(r["history_id"]): int(r["task_id"]) for r in rows}

    def mark_history_status(
        self,
        history_id: int,
        status: str,
        error: str | None = None,
        save_path: str | None = None,
        finished: bool = False,
    ) -> None:
        """任务失败必须把 error 写入 history 再抛/返回（编码规范 §2.4）。"""
        with self._conn:
            self._conn.execute(
                "UPDATE history SET status=?, error=?, save_path=COALESCE(?, save_path),"
                " finished_at=COALESCE(?, finished_at) WHERE id=?",
                (status, error, save_path, utcnow() if finished else None, history_id),
            )

    def reset_history_for_retry(self, history_id: int) -> None:
        """重试前清除 history 的终态字段，保留已有文件索引。"""
        with self._conn:
            self._conn.execute(
                "UPDATE history SET status='queued', error=NULL, finished_at=NULL WHERE id=?",
                (history_id,),
            )

    def set_history_display_tags(
        self, history_id: int, title: str | None, artist: str | None, album: str | None
    ) -> None:
        """FR-TAG-02：标签写成功后同步 DB 展示字段。"""
        with self._conn:
            self._conn.execute(
                "UPDATE history SET title=?, artist=?, album=? WHERE id=?",
                (title, artist, album, history_id),
            )

    # ---- tasks ----

    def create_task(self, t: Task) -> int:
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO tasks (type, payload_json, status, total_bytes, history_id,"
                " created_at) VALUES (?,?,?,?,?,?)",
                (t.type, t.payload_json, t.status, t.total_bytes, t.history_id, utcnow()),
            )
            return int(cur.lastrowid)  # type: ignore[arg-type]  # sqlite3 lastrowid 运行时必为 int

    def get_task(self, task_id: int) -> Task | None:
        row = self._conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return row_to(Task, row) if row else None

    def list_tasks(self, status: str | None = None, limit: int = 50) -> list[Task]:
        sql = "SELECT * FROM tasks"
        params: list[Any] = []
        if status:
            sql += " WHERE status=?"
            params.append(status)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        return [row_to(Task, r) for r in self._conn.execute(sql, params)]

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
    ) -> None:
        sets: list[str] = []
        params: list[Any] = []
        if status is not None:
            sets.append("status=?")
            params.append(status)
        if progress_bytes is not None:
            sets.append("progress_bytes=?")
            params.append(progress_bytes)
        if total_bytes is not None:
            sets.append("total_bytes=?")
            params.append(total_bytes)
        if speed is not None:
            sets.append("speed=?")
            params.append(speed)
        if retry_count is not None:
            sets.append("retry_count=?")
            params.append(retry_count)
        if next_retry_at is not None:
            sets.append("next_retry_at=?")
            params.append(next_retry_at)
        if history_id is not None:
            sets.append("history_id=?")
            params.append(history_id)
        if error is not None:
            sets.append("error=?")
            params.append(error)
        if not sets:
            return
        params.append(task_id)
        with self._conn:
            # sets 只含固定列名片段（"status=?" 等），无用户输入；params 全参数化
            sql = "UPDATE tasks SET " + ", ".join(sets) + " WHERE id=?"  # noqa: S608
            self._conn.execute(sql, params)

    def reset_task_for_retry(self, task_id: int) -> None:
        """把可重试任务复位，清掉旧错误、进度和退避时间。"""
        with self._conn:
            self._conn.execute(
                "UPDATE tasks SET status='queued', progress_bytes=0, total_bytes=NULL,"
                " speed=NULL, next_retry_at=NULL, error=NULL WHERE id=?",
                (task_id,),
            )

    def delete_task(self, task_id: int) -> bool:
        """删除任务记录；history 与已落盘文件由服务层决定是否保留。"""
        with self._conn:
            cur = self._conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
            return cur.rowcount > 0

    def recover_interrupted(self) -> int:
        """NFR-05 恢复：queued 保持、downloading 标 failed（retryable），其余不动。

        下载页只认 history 的状态，所以这里一并把历史行结算掉：任务被标 failed 的那些，
        以及**没有任何在跑任务**却仍停在非终态的行（任务台账被删过的旧数据）——
        否则它们会在列表里永远显示「等待」，而队列里根本没有它们。
        """
        n = 0
        with self._conn:
            cur = self._conn.execute(
                "UPDATE tasks SET status='failed', error='interrupted, retryable'"
                " WHERE status='downloading'"
            )
            n += cur.rowcount
            self._conn.execute(
                "UPDATE history SET status='failed', error='interrupted, retryable',"
                " finished_at=?"
                " WHERE status IN ('queued','downloading','paused')"
                " AND NOT EXISTS (SELECT 1 FROM tasks t WHERE t.history_id=history.id"
                " AND t.status IN ('queued','downloading','paused'))",
                (utcnow(),),
            )
        return n

    # ---- stats ----

    def counts(self) -> dict[str, int]:
        """各表计数（控制台统计卡）：一次取全，免掉前端为一个数字去拉整页列表。

        「已入库」的判据是 `save_path IS NOT NULL`（入库即已写盘），与播放可用性同一判据。
        曲库占用**不在这里算**：`history.file_size` 在链接/转发入队时常常拿不到（Telegram 没给），
        拿它求和会让「占用」长期显示 0——真实占用得问磁盘，见 `library_paths()`。
        """
        tasks = {
            row["status"]: int(row["cnt"])
            for row in self._conn.execute(
                "SELECT status, COUNT(*) AS cnt FROM tasks GROUP BY status"
            )
        }
        history = self._conn.execute(
            "SELECT"
            " COALESCE(SUM(save_path IS NOT NULL), 0) AS tracks,"
            " COALESCE(SUM(status='failed'), 0) AS failed"
            " FROM history"
        ).fetchone()
        sources = self._conn.execute(
            "SELECT COUNT(*) AS total, COALESCE(SUM(enabled=1), 0) AS enabled FROM sources"
        ).fetchone()
        return {
            "tasks_queued": tasks.get("queued", 0),
            "tasks_downloading": tasks.get("downloading", 0),
            "tasks_paused": tasks.get("paused", 0),
            "tasks_failed": tasks.get("failed", 0),
            "tasks_success": tasks.get("success", 0),
            "library_tracks": int(history["tracks"]),
            "library_failed": int(history["failed"]),
            "sources_total": int(sources["total"]),
            "sources_enabled": int(sources["enabled"]),
        }

    def library_paths(self) -> list[str]:
        """已入库行的落盘路径（save_path 非空），供调用方去磁盘上问真实占用。"""
        return [
            str(row["save_path"])
            for row in self._conn.execute(
                "SELECT save_path FROM history WHERE save_path IS NOT NULL"
            )
        ]

    # ---- settings ----

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self._conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO settings (key, value) VALUES (?,?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def all_settings(self) -> dict[str, str]:
        return {r["key"]: r["value"] for r in self._conn.execute("SELECT * FROM settings")}

    # ---- preview cache ----

    def get_preview(self, chat_id: int, message_id: int) -> PreviewCache | None:
        row = self._conn.execute(
            "SELECT * FROM preview_cache WHERE chat_id=? AND message_id=?",
            (chat_id, message_id),
        ).fetchone()
        return row_to(PreviewCache, row) if row else None

    def put_preview(self, p: PreviewCache) -> int:
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO preview_cache (chat_id, message_id, file_path, file_size,"
                " last_access_at) VALUES (?,?,?,?,?)"
                " ON CONFLICT(chat_id, message_id) DO UPDATE SET"
                " file_path=excluded.file_path, file_size=excluded.file_size,"
                " last_access_at=excluded.last_access_at",
                (p.chat_id, p.message_id, p.file_path, p.file_size, p.last_access_at),
            )
            return int(cur.lastrowid)  # type: ignore[arg-type]  # sqlite3 lastrowid 运行时必为 int

    def touch_preview(self, preview_id: int) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE preview_cache SET last_access_at=? WHERE id=?", (utcnow(), preview_id)
            )

    def list_previews_by_access(self) -> list[PreviewCache]:
        return [
            row_to(PreviewCache, r)
            for r in self._conn.execute("SELECT * FROM preview_cache ORDER BY last_access_at")
        ]

    def delete_preview(self, preview_id: int) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM preview_cache WHERE id=?", (preview_id,))

    def preview_totals(self) -> tuple[int, int]:
        """返回 (总字节, 条数)，供 LRU 淘汰（SDD §2.4）。"""
        row = self._conn.execute(
            "SELECT COALESCE(SUM(file_size),0) AS total, COUNT(*) AS cnt FROM preview_cache"
        ).fetchone()
        return int(row["total"]), int(row["cnt"])
