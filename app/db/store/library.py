"""本地曲库台账仓储（FR-LIB）：扫描索引的读写与筛选聚合。"""

from __future__ import annotations

from typing import Any

from app.db.models import LocalTrack, row_to
from app.db.store.base import _Base, utcnow


class LibraryStore(_Base):
    """``Store`` 的本地曲库聚合 mixin（FR-LIB）。"""

    _LOCAL_SORTS = {
        "title": "title COLLATE NOCASE",
        "artist": "artist COLLATE NOCASE",
        "album": "album COLLATE NOCASE",
        "duration": "duration_sec",
        "size": "file_size",
        "bitrate": "bitrate",
        "created": "first_seen_at",
    }

    def upsert_local_track(self, t: LocalTrack) -> int:
        """写扫描索引；同 rel_path 收敛到一行（文件回来/标签变了就地更新）。"""
        now = utcnow()
        with self._conn:
            if t.id is None:
                cur = self._conn.execute(
                    "INSERT INTO local_tracks (rel_path, file_name, ext, title, artist, album,"
                    " duration_sec, file_size, bitrate, mtime, missing, history_id,"
                    " first_seen_at, scanned_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                    " ON CONFLICT(rel_path) DO UPDATE SET"
                    " file_name=excluded.file_name, ext=excluded.ext, title=excluded.title,"
                    " artist=excluded.artist, album=excluded.album,"
                    " duration_sec=excluded.duration_sec, file_size=excluded.file_size,"
                    " bitrate=excluded.bitrate, mtime=excluded.mtime,"
                    " missing=excluded.missing, history_id=excluded.history_id,"
                    " scanned_at=excluded.scanned_at"
                    " RETURNING id",
                    (
                        t.rel_path,
                        t.file_name,
                        t.ext,
                        t.title,
                        t.artist,
                        t.album,
                        t.duration_sec,
                        t.file_size,
                        t.bitrate,
                        t.mtime,
                        t.missing,
                        t.history_id,
                        t.first_seen_at or now,
                        now,
                    ),
                )
                row = cur.fetchone()
                return int(row["id"])
            self._conn.execute(
                "UPDATE local_tracks SET rel_path=?, file_name=?, ext=?, title=?, artist=?,"
                " album=?, duration_sec=?, file_size=?, bitrate=?, mtime=?, missing=?,"
                " history_id=?, first_seen_at=?, scanned_at=? WHERE id=?",
                (
                    t.rel_path,
                    t.file_name,
                    t.ext,
                    t.title,
                    t.artist,
                    t.album,
                    t.duration_sec,
                    t.file_size,
                    t.bitrate,
                    t.mtime,
                    t.missing,
                    t.history_id,
                    t.first_seen_at,
                    now,
                    t.id,
                ),
            )
            return t.id

    def get_local_track(self, track_id: int) -> LocalTrack | None:
        row = self._conn.execute("SELECT * FROM local_tracks WHERE id=?", (track_id,)).fetchone()
        return row_to(LocalTrack, row) if row else None

    def local_track_paths(self) -> dict[str, LocalTrack]:
        """rel_path → 行（扫描时与磁盘清单对账用）。"""
        return {
            str(row["rel_path"]): row_to(LocalTrack, row)
            for row in self._conn.execute("SELECT * FROM local_tracks")
        }

    @staticmethod
    def _local_track_clauses(
        q: str | None, artist: str | None, missing: bool | None
    ) -> tuple[list[str], list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if q:
            clauses.append("(title LIKE ? OR artist LIKE ? OR album LIKE ? OR file_name LIKE ?)")
            like = f"%{q}%"
            params += [like, like, like, like]
        if artist is not None:
            clauses.append("artist=?")
            params.append(artist)
        if missing is not None:
            clauses.append("missing=?")
            params.append(1 if missing else 0)
        return clauses, params

    def list_local_tracks(
        self,
        q: str | None = None,
        artist: str | None = None,
        missing: bool | None = None,
        sort: str = "created",
        order: str = "desc",
        limit: int = 50,
        offset: int = 0,
    ) -> list[LocalTrack]:
        """按关键词/歌手/在库状态筛选 + 白名单排序分页取行（曲库懒加载的数据源）。"""
        clauses, params = self._local_track_clauses(q, artist, missing)
        sql = "SELECT * FROM local_tracks"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        # 排序列与方向都来自白名单字典，不进用户输入
        column = self._LOCAL_SORTS.get(sort, self._LOCAL_SORTS["created"])
        direction = "ASC" if order == "asc" else "DESC"
        sql += f" ORDER BY {column} {direction}, id {direction} LIMIT ? OFFSET ?"  # noqa: S608
        params += [limit, offset]
        return [row_to(LocalTrack, r) for r in self._conn.execute(sql, params)]

    def count_local_tracks(self, q: str | None = None, artist: str | None = None) -> dict[str, int]:
        """当前筛选下的在库/已删除/合计计数（页签计数，不数当前页）。"""
        clauses, params = self._local_track_clauses(q, artist, None)
        sql = "SELECT COALESCE(SUM(missing=0),0) AS present, COALESCE(SUM(missing=1),0) AS gone"
        if clauses:
            sql += " FROM local_tracks WHERE " + " AND ".join(clauses)
        else:
            sql += " FROM local_tracks"
        row = self._conn.execute(sql, params).fetchone()
        present, gone = int(row["present"]), int(row["gone"])
        return {"present": present, "missing": gone, "all": present + gone}

    def local_track_bytes(self) -> int:
        """在库文件总字节：file_size 是扫描时实测的，比 history.file_size 可靠。"""
        row = self._conn.execute(
            "SELECT COALESCE(SUM(file_size),0) AS total FROM local_tracks WHERE missing=0"
        ).fetchone()
        return int(row["total"])

    def local_track_artists(self) -> list[tuple[str, int]]:
        """非空歌手聚合（筛选下拉）：标签缺失时回退目录名的值也在这里。"""
        return [
            (str(row["artist"]), int(row["cnt"]))
            for row in self._conn.execute(
                "SELECT artist, COUNT(*) AS cnt FROM local_tracks"
                " WHERE missing=0 AND artist IS NOT NULL AND artist!=''"
                " GROUP BY artist ORDER BY artist COLLATE NOCASE"
            )
        ]

    def set_local_track_missing(self, track_ids: list[int], missing: bool) -> None:
        """批量翻转在库状态（扫描对账结果；文件回来/消失都走这里）。"""
        if not track_ids:
            return
        with self._conn:
            marks = ",".join("?" * len(track_ids))
            self._conn.execute(
                f"UPDATE local_tracks SET missing=?, scanned_at=? WHERE id IN ({marks})",  # noqa: S608
                (1 if missing else 0, utcnow(), *track_ids),
            )

    def delete_local_track(self, track_id: int) -> bool:
        """删除一条曲库记录；磁盘文件不在这一层管。"""
        with self._conn:
            cur = self._conn.execute("DELETE FROM local_tracks WHERE id=?", (track_id,))
            return cur.rowcount > 0
