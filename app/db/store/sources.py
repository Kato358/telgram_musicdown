"""音乐源仓储（FR-SRC-01~04）。"""

from __future__ import annotations

from app.db.models import Source, row_to
from app.db.store.base import _Base, utcnow


class SourceStore(_Base):
    """``Store`` 的音乐源聚合 mixin（FR-SRC-01~04）。"""

    def upsert_source(self, src: Source) -> int:
        now = utcnow()
        with self._conn:
            if src.id is None:
                cur = self._conn.execute(
                    "INSERT INTO sources (telegram_chat_id, username, title, type, enabled,"
                    " created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                    (
                        src.telegram_chat_id,
                        src.username,
                        src.title,
                        src.type,
                        src.enabled,
                        now,
                        now,
                    ),
                )
                return int(cur.lastrowid)  # type: ignore[arg-type]  # sqlite3 lastrowid 运行时必为 int
            self._conn.execute(
                "UPDATE sources SET telegram_chat_id=?, username=?, title=?, type=?,"
                " enabled=?, updated_at=? WHERE id=?",
                (
                    src.telegram_chat_id,
                    src.username,
                    src.title,
                    src.type,
                    src.enabled,
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
