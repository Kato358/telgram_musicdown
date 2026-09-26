"""任务台账仓储（FR-DL-01~04、NFR-05）。"""

from __future__ import annotations

from typing import Any

from app.db.models import Task, row_to
from app.db.store.base import _Base, utcnow


class TaskStore(_Base):
    """``Store`` 的任务台账聚合 mixin（FR-DL-01~04、NFR-05）。"""

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

    def next_queued_task(self) -> dict[str, Any] | None:
        """取最早一条可执行任务并标 downloading（Worker 池消费，FR-DL-01）。

        「可执行」= status='queued' 且 next_retry_at 已到；取到即置 downloading，
        使同一任务不会被第二个 Worker 再次取走。
        """
        row = self._conn.execute(
            "SELECT * FROM tasks WHERE status='queued'"
            " AND (next_retry_at IS NULL OR next_retry_at <= ?)"
            " ORDER BY id LIMIT 1",
            (utcnow(),),
        ).fetchone()
        if row is None:
            return None
        task = {k: row[k] for k in row.keys()}  # noqa: SIM118  sqlite3.Row
        self.update_task(task["id"], status="downloading")
        return task

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
