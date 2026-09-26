"""仓储基础设施：连接、迁移与时间戳工具。"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger("app.db.store")


def utcnow() -> str:
    """UTC ISO-8601 文本（store 约定）。"""
    return datetime.now(UTC).isoformat(timespec="seconds")


class _Base:
    """连接、迁移与关闭：各聚合 mixin 的共同基类（不单独实例化）。"""

    _conn: sqlite3.Connection

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
        # 本文件在 app/db/store/ 下，迁移目录仍归 app/db/ 所有
        mig_dir = Path(__file__).resolve().parents[1] / "migrations"
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
