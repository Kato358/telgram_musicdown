"""仓储层：全部 SQL 收口于此（编码规范 §2.6，DIP 适配器）。

- ``Store`` 实现 ``app.ports.repository.IStore`` 协议：服务层依赖端口，不依赖本类。
- 参数化查询（``?`` 占位），禁止 f-string/``%`` 拼 SQL。
- 事务边界在 store 方法内；写 history 与 tasks 关联更新同事务。
- 迁移：db/migrations/NNN_*.sql 顺序执行并记 schema_version。
- 时间戳统一 UTC ISO-8601 文本，展示层转本地。
"""

from __future__ import annotations

from app.db.store.base import utcnow
from app.db.store.history import HistoryStore
from app.db.store.library import LibraryStore
from app.db.store.preview import PreviewStore
from app.db.store.search_cache import SearchCacheStore
from app.db.store.settings import SettingsStore
from app.db.store.sources import SourceStore
from app.db.store.stats import StatsStore
from app.db.store.tasks import TaskStore

__all__ = ["Store", "utcnow"]


class Store(
    SourceStore,
    HistoryStore,
    TaskStore,
    LibraryStore,
    SettingsStore,
    PreviewStore,
    SearchCacheStore,
    StatsStore,
):
    """SQLite 仓储。同步 sqlite3，async 侧经 to_thread 调用（§2.3）。"""
