"""试听缓存仓储（FR-PLAY-01~03）。"""

from __future__ import annotations

from app.db.models import PreviewCache, row_to
from app.db.store.base import _Base, utcnow


class PreviewStore(_Base):
    """``Store`` 的试听缓存聚合 mixin（FR-PLAY-01~03）。"""

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

    def delete_all_previews(self) -> None:
        """清空 preview_cache（设置页「清理缓存」，FR-PLAY-02）：文件由 PreviewService 删。"""
        with self._conn:
            self._conn.execute("DELETE FROM preview_cache")

    def preview_totals(self) -> tuple[int, int]:
        """返回 (总字节, 条数)，供 LRU 淘汰（SDD §2.4）。"""
        row = self._conn.execute(
            "SELECT COALESCE(SUM(file_size),0) AS total, COUNT(*) AS cnt FROM preview_cache"
        ).fetchone()
        return int(row["total"]), int(row["cnt"])
