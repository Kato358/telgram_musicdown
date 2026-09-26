"""统计聚合仓储（`GET /api/stats`）。"""

from __future__ import annotations

from app.db.store.base import _Base


class StatsStore(_Base):
    """``Store`` 的统计聚合 mixin（`GET /api/stats`）。"""

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
