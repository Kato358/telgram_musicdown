"""键值设置仓储（FR-CFG-03）。"""

from __future__ import annotations

from app.db.store.base import _Base


class SettingsStore(_Base):
    """``Store`` 的键值设置聚合 mixin（FR-CFG-03）。"""

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
