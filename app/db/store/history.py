"""下载历史仓储（FR-DL-01/05）。"""

from __future__ import annotations

from typing import Any

from app.db.models import History, row_to
from app.db.store.base import _Base, utcnow


class HistoryStore(_Base):
    """``Store`` 的下载历史聚合 mixin（FR-DL-01/05）。"""

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
                    " file_id, title, artist, album, duration_sec, file_size, bitrate, mime, ext,"
                    " caption, message_date, save_path, status, error, created_at,"
                    " finished_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                    " ON CONFLICT(chat_id, message_id) DO UPDATE SET"
                    " source_id=excluded.source_id, file_unique_id=excluded.file_unique_id,"
                    " file_id=excluded.file_id, title=excluded.title, artist=excluded.artist,"
                    " album=excluded.album, duration_sec=excluded.duration_sec,"
                    " file_size=excluded.file_size, bitrate=excluded.bitrate, mime=excluded.mime,"
                    " ext=excluded.ext,"
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
                        h.bitrate,
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
                " artist=?, album=?, duration_sec=?, file_size=?, bitrate=?, mime=?, ext=?,"
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
                    h.bitrate,
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

    def find_history_cover_source(self, title: str) -> History | None:
        """全局封面「本地优先」：按标题精确匹配最近一条已落盘的成功记录。

        封面是装饰，同名多行取最新（id 最大）即可；只挑 save_path 非空的 success 行
        （文件真的在磁盘上，才读得到内嵌封面）。
        """
        row = self._conn.execute(
            "SELECT * FROM history"
            " WHERE title=? AND status='success' AND save_path IS NOT NULL AND save_path!=''"
            " ORDER BY id DESC LIMIT 1",
            (title,),
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

    def delete_history(self, history_id: int) -> bool:
        """删除一条历史记录（下载页行删除）；返回是否删到了。磁盘文件不在这一层管。"""
        with self._conn:
            cur = self._conn.execute("DELETE FROM history WHERE id=?", (history_id,))
            return cur.rowcount > 0

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

    def set_history_media(
        self,
        history_id: int,
        duration_sec: int | None,
        file_size: int | None,
        bitrate: int | None,
        ext: str | None = None,
    ) -> None:
        """回填时长 / 大小 / 码率 / 容器（链接入队时未知，落盘实测后才拿得到）。

        只认有真值的更新：传 NULL 的字段保持原样，不把已有读数冲掉。``ext`` 是
        在线源解析之后才知道的容器（母带歌多半只能拿到无损），不记下来下载页显示的
        格式就永远停在入队时的空白。
        """
        with self._conn:
            self._conn.execute(
                "UPDATE history SET"
                " duration_sec=COALESCE(?, duration_sec),"
                " file_size=COALESCE(?, file_size),"
                " bitrate=COALESCE(?, bitrate),"
                " ext=COALESCE(?, ext)"
                " WHERE id=?",
                (duration_sec, file_size, bitrate, ext, history_id),
            )

    def list_history_missing_media(self, limit: int = 500) -> list[History]:
        """已落盘但缺时长/大小/码率的行（启动回填用），旧的在前。"""
        return [
            row_to(History, r)
            for r in self._conn.execute(
                "SELECT * FROM history WHERE status='success' AND save_path IS NOT NULL"
                " AND (duration_sec IS NULL OR file_size IS NULL OR bitrate IS NULL)"
                " ORDER BY id LIMIT ?",
                (limit,),
            )
        ]

    def history_paths_by_id(self) -> dict[str, int]:
        """save_path → history id（本地曲库扫描时把台账行挂回下载记录）。"""
        return {
            str(row["save_path"]): int(row["id"])
            for row in self._conn.execute(
                "SELECT id, save_path FROM history WHERE save_path IS NOT NULL AND save_path!=''"
            )
        }
