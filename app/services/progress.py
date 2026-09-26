"""下载进度上报与任务运行态（SRP）：从下载队列引擎拆出的两块独立职责。

- ``TaskRunState``：暂停/取消标记与在跑协程的登记处。原先这三份集合挂在
  ``DownloadService`` 私有字段上，进度回调要反向读服务的私有集合（跨类访问私有成员）；
  现在它是独立对象，Worker 池与进度回调共用同一份事实源。
- ``ProgressReporter``：把 Pyrogram 的同步进度回调转成节流的 SSE + DB 写入。
  它只依赖 ``IStore`` / ``EventBus`` / ``TaskRunState`` 三个协作面，不再持有整个服务。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from time import monotonic
from typing import Any

from app.events import Event, EventBus
from app.ports import IStore

logger = logging.getLogger(__name__)

PROGRESS_SSE_INTERVAL_SEC = 0.5
PROGRESS_DB_INTERVAL_SEC = 2.0

# 进度回调的节流阈值：距上次上报不足该字节数且未到时间间隔时不发帧
PROGRESS_SSE_MIN_DELTA_BYTES = 256 * 1024


@dataclass(slots=True)
class TaskRunState:
    """任务运行态（Worker 池与进度回调共用）：暂停/取消标记 + 在跑协程登记。"""

    paused: set[int] = field(default_factory=set)
    cancelled: set[int] = field(default_factory=set)
    running: dict[int, asyncio.Task[Any]] = field(default_factory=dict)

    def is_stopped(self, task_id: int) -> bool:
        """任务是否已被用户暂停或取消（进度回调据此停止上报）。"""
        return task_id in self.paused or task_id in self.cancelled

    def mark_paused(self, task_id: int) -> None:
        self.paused.add(task_id)
        self.cancelled.discard(task_id)

    def mark_cancelled(self, task_id: int) -> None:
        self.cancelled.add(task_id)
        self.paused.discard(task_id)

    def clear(self, task_id: int) -> None:
        """清除暂停/取消标记（恢复、重试、任务收尾）。"""
        self.paused.discard(task_id)
        self.cancelled.discard(task_id)

    def forget_cancelled(self, task_id: int) -> None:
        """任务收尾：只撤取消标记（暂停标记由用户的恢复动作负责）。"""
        self.cancelled.discard(task_id)

    def register_running(self, task_id: int, task: asyncio.Task[Any]) -> None:
        self.running[task_id] = task

    def unregister_running(self, task_id: int, task: asyncio.Task[Any]) -> bool:
        """仅当登记的就是这个协程时才移除（避免误删新一轮任务）。"""
        if self.running.get(task_id) is task:
            self.running.pop(task_id, None)
            return True
        return False

    def running_task(self, task_id: int) -> asyncio.Task[Any] | None:
        return self.running.get(task_id)


class ProgressReporter:
    """把 Pyrogram 同步进度回调转换成节流的 SSE 与 DB 更新。"""

    def __init__(
        self,
        store: IStore,
        events: EventBus,
        state: TaskRunState,
        task_id: int,
        total_bytes: int | None,
    ) -> None:
        self._store = store
        self._events = events
        self._state = state
        self._task_id = task_id
        self._total_bytes = total_bytes
        self._current_bytes = 0
        self._speed: float | None = None
        self._last_sample_at = monotonic()
        self._last_sample_bytes = 0
        self._last_emit_at = 0.0
        self._last_emit_bytes = 0
        self._last_db_at = 0.0
        self._loop = asyncio.get_running_loop()

    def __call__(self, current: int, total: int) -> None:
        if self._state.is_stopped(self._task_id):
            return
        try:
            current_bytes = max(0, int(current))
            total_bytes = int(total)
        except (TypeError, ValueError):
            return
        if total_bytes > 0:
            self._total_bytes = total_bytes
        if self._total_bytes is not None:
            current_bytes = min(current_bytes, self._total_bytes)

        now = monotonic()
        elapsed = now - self._last_sample_at
        if elapsed > 0:
            delta = current_bytes - self._last_sample_bytes
            if delta >= 0:
                self._speed = delta / elapsed
            else:
                self._speed = None
        self._last_sample_at = now
        self._last_sample_bytes = current_bytes
        self._current_bytes = current_bytes

        final = self._total_bytes is not None and current_bytes >= self._total_bytes
        first = self._last_emit_at == 0.0
        if (
            not first
            and not final
            and now - self._last_emit_at < PROGRESS_SSE_INTERVAL_SEC
            and current_bytes - self._last_emit_bytes < PROGRESS_SSE_MIN_DELTA_BYTES
        ):
            return
        self._last_emit_at = now
        self._last_emit_bytes = current_bytes

        # 最终帧在 async flush 中 await 发布，保证随后 task.status 的顺序。
        if not final:
            self._events.publish_nowait(Event("task.progress", self._payload()))

        if final or now - self._last_db_at >= PROGRESS_DB_INTERVAL_SEC:
            self._last_db_at = now
            self._schedule_persist()

    def _payload(self) -> dict[str, object]:
        eta: int | None = None
        if self._total_bytes is not None and self._speed and self._speed > 0:
            eta = max(0, int((self._total_bytes - self._current_bytes) / self._speed))
        return {
            "task_id": self._task_id,
            "progress_bytes": self._current_bytes,
            "total_bytes": self._total_bytes,
            "speed": self._speed,
            "eta": eta,
        }

    def _schedule_persist(self) -> None:
        def persist() -> None:
            self._store.update_task(
                self._task_id,
                progress_bytes=self._current_bytes,
                total_bytes=self._total_bytes,
                speed=self._speed or 0.0,
            )

        try:
            self._loop.call_soon_threadsafe(persist)
        except RuntimeError:
            logger.debug("task %s progress persistence skipped: loop closed", self._task_id)

    async def flush(self) -> None:
        """收尾：把最后读数落库，未取消则 await 发布最终进度帧。"""
        cancelled = self._state.is_stopped(self._task_id)
        self._store.update_task(
            self._task_id,
            progress_bytes=self._current_bytes,
            total_bytes=self._total_bytes,
            speed=self._speed or 0.0,
        )
        if not cancelled:
            await self._events.publish(Event("task.progress", self._payload()))
