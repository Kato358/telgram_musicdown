"""源同步与历史回溯（FR-SRC-04）：扫源消息 → 音频入队下载。

- 执行器由下载 Worker 池消费（``tasks.type='sync'``），扫描不阻塞 Web 请求；
  崩溃/超时由 Worker 统一落库（NFR-08），本模块只负责扫描与入队。
- 添加源后的一次性初始导入（向导第 3 步）与「回溯历史」共用本执行器，
  差别只在 payload 的 direction / limit / 锚点。
- 每批检查任务状态：用户取消后立即停止，不再扩大入队。
- FloodWait 由 telegram 层 ``with_flood_retry`` 处理（NFR-09），本层不感知。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.db.models import Source
from app.db.store import Store
from app.domain import (
    SourceFilters,
    card_to_meta,
    message_to_card,
    scope_allows,
)
from app.services.download import DownloadRequest

logger = logging.getLogger(__name__)

BATCH = 100  # 每批拉取条数：批次之间可取消、可让出 loop
INITIAL_IMPORT_LIMIT = 200  # 添加源时的初始导入条数（向导第 3 步的承诺）
DEFAULT_SCOPE = ("audio", "audio_document")


@dataclass(slots=True)
class SyncOutcome:
    """一次同步的结果计数（进日志与任务事件）。"""

    scanned: int = 0
    queued: int = 0
    skipped: int = 0


class SyncClientProto(Protocol):
    """同步所需协议面（FakeUserClient 实现，NFR-07）。"""

    async def iter_messages(
        self, chat_id: int, reverse: bool, offset_id: int, limit: int
    ) -> list[dict[str, Any]]: ...


class DownloadQueueProto(Protocol):
    """入队协议面（DownloadService）。"""

    async def enqueue(self, req: DownloadRequest) -> int | None: ...


@dataclass(slots=True)
class _Scan:
    """一次扫描的游标与终点（FR-SRC-04）：方向、上限、锚点。"""

    reverse: bool
    offset_id: int
    limit: int | None = None
    to_message_id: int | None = None
    to_date: str | None = None
    end: bool = field(default=False)

    def batch_size(self, scanned: int) -> int:
        """本批请求条数；剩余为 0 则返回 0 表示扫够上限。"""
        if self.limit is None:
            return BATCH
        return min(BATCH, self.limit - scanned)

    def reached(self, message_id: int, message_date: str | None) -> bool:
        """本条是否已越过锚点（含边界那条，不再入队）。"""
        if self.to_message_id is not None and _past(message_id, self.to_message_id, self.reverse):
            return True
        return bool(
            self.to_date and message_date and _past(message_date, self.to_date, self.reverse)
        )

    def advance(self, batch: list[dict[str, Any]], requested: int) -> None:
        """下一页锚点 = 本批最后一条 id；本批不足请求数即已扫到底。"""
        self.offset_id = int(batch[-1]["message_id"])
        if len(batch) < requested:
            self.end = True


class SyncRunner:
    """扫源入队（FR-SRC-04）。"""

    def __init__(self, store: Store, client: SyncClientProto, queue: DownloadQueueProto) -> None:
        self.store = store
        self.client = client
        self.queue = queue

    async def run(self, task_id: int, payload: dict[str, Any]) -> SyncOutcome:
        """执行一次同步；payload: source_id, direction, limit?, to_message_id?, to_date?"""
        source = self.store.get_source(int(payload["source_id"]))
        if source is None:
            logger.warning(
                "sync task %s: source %s no longer exists", task_id, payload.get("source_id")
            )
            return SyncOutcome()
        scope = _parse_scope(source.media_scope)
        scan = _Scan(
            reverse=str(payload.get("direction") or "backward") == "forward",
            offset_id=int(payload.get("offset_id") or source.last_message_id or 0),
            limit=payload.get("limit"),
            to_message_id=payload.get("to_message_id"),
            to_date=payload.get("to_date"),
        )
        outcome = SyncOutcome()
        newest = scan.offset_id

        while not scan.end:
            size = scan.batch_size(outcome.scanned)
            if size <= 0:
                break
            batch = await self.client.iter_messages(
                source.telegram_chat_id, reverse=scan.reverse, offset_id=scan.offset_id, limit=size
            )
            if not batch:
                break
            for msg in batch:
                message_id = int(msg["message_id"])
                newest = max(newest, message_id)
                outcome.scanned += 1
                if scan.reached(message_id, msg.get("message_date")):
                    scan.end = True
                    break
                await self._enqueue(msg, source, scope, outcome)
            if scan.end:
                break
            scan.advance(batch, size)
            if self._cancelled(task_id):
                logger.info("sync task %s cancelled after %d scanned", task_id, outcome.scanned)
                return outcome

        if newest > (source.last_message_id or 0):
            self.store.set_source_cursor(int(source.id or 0), newest)  # FR-SRC-04 游标
        logger.info(
            "sync task %s done: scanned=%d queued=%d skipped=%d",
            task_id,
            outcome.scanned,
            outcome.queued,
            outcome.skipped,
        )
        return outcome

    async def _enqueue(
        self, msg: dict[str, Any], source: Source, scope: tuple[str, ...], outcome: SyncOutcome
    ) -> None:
        """一条消息：媒体范围 → 源级过滤 → 入队（去重命中计入 skipped）。"""
        if not scope_allows(list(scope), msg):
            return
        card = message_to_card(msg, source.title)
        if not SourceFilters.matches(source.filters_json, card):
            return
        task_id = await self.queue.enqueue(
            DownloadRequest(meta=card_to_meta(card), source_id=source.id)
        )
        if task_id is None:
            outcome.skipped += 1
        else:
            outcome.queued += 1

    def _cancelled(self, task_id: int) -> bool:
        task = self.store.get_task(task_id)
        return task is None or task.status == "cancelled"


def _past(value: Any, edge: Any, reverse: bool) -> bool:
    """锚点比较：向更新（reverse）看 >= 边界，向更早看 <= 边界。"""
    return bool(value >= edge) if reverse else bool(value <= edge)


def _parse_scope(media_scope: str) -> tuple[str, ...]:
    """sources.media_scope（JSON 数组）→ 元组；坏值退回落定媒体范围。"""
    try:
        value = json.loads(media_scope)
    except (TypeError, json.JSONDecodeError):
        return DEFAULT_SCOPE
    if not isinstance(value, list):
        return DEFAULT_SCOPE
    return tuple(str(v) for v in value)
