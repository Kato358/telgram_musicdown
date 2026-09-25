"""进程内事件总线（SDD §1.4）。

一个简单的 asyncio.Queue 广播器，供 SSE ``/api/events`` 使用。
事件类型：task.progress、task.status、log.error、preview.ready、session.invalid。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import traceback
from dataclasses import dataclass, field
from time import time

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class Event:
    """总线事件：type + 载荷（SDD §1.4 表）。"""

    type: str
    payload: dict[str, object] = field(default_factory=dict)
    ts: float = field(default_factory=time)

    def to_sse(self) -> str:
        """SSE 帧：``data: {"type": ..., "payload": ..., "ts": ...}\\n\\n``。"""
        return (
            "data: "
            + json.dumps(
                {"type": self.type, "payload": self.payload, "ts": self.ts},
                ensure_ascii=False,
            )
            + "\n\n"
        )


class EventBus:
    """asyncio.Queue 广播器；订阅者各自 queue，发布投递到全部。"""

    def __init__(self, maxsize: int = 1000) -> None:
        self._subscribers: set[asyncio.Queue[Event]] = set()
        self._maxsize = maxsize
        self._lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    async def subscribe(self) -> asyncio.Queue[Event]:
        q: asyncio.Queue[Event] = asyncio.Queue(maxsize=self._maxsize)
        async with self._lock:
            self._subscribers.add(q)
        self._loop = asyncio.get_running_loop()
        return q

    async def unsubscribe(self, q: asyncio.Queue[Event]) -> None:
        async with self._lock:
            self._subscribers.discard(q)

    async def publish(self, event: Event) -> None:
        """投递事件到全部订阅者；满队列丢弃最旧（Web 至少 1s 刷新可降级轮询）。"""
        self._loop = asyncio.get_running_loop()
        async with self._lock:
            targets = list(self._subscribers)
        self._publish_to_targets(event, targets)

    def publish_nowait(self, event: Event) -> None:
        """从同步回调线程投递事件；同一事件循环内则立即入队。"""
        loop: asyncio.AbstractEventLoop | None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = self._loop
        else:
            self._loop = loop
            self._publish_to_targets(event, list(self._subscribers))
            return
        if loop is None or loop.is_closed():
            logger.debug("event %s dropped: no running loop", event.type)
            return
        try:
            loop.call_soon_threadsafe(self._publish_on_loop, event)
        except RuntimeError:
            logger.debug("event %s dropped: loop closed", event.type)

    def _publish_on_loop(self, event: Event) -> None:
        """把跨线程事件转成事件循环内的同步入队。"""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        self._publish_to_targets(event, list(self._subscribers))

    @staticmethod
    def _publish_to_targets(event: Event, targets: list[asyncio.Queue[Event]]) -> None:
        for q in targets:
            if q.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    q.get_nowait()
            q.put_nowait(event)


class EventBusLogHandler(logging.Handler):
    """logging → 事件总线桥：ERROR+ 记录发布为 ``log.error``（日志页「实时错误」）。

    历史事实源仍是 ``logs/app.log`` 文件，这里只补「连接期间发生了什么」的实时提示。
    ``logging.Handler`` 可能在任意线程被调（worker 池、Pyrogram loop），所以发布走
    ``publish_nowait``（跨线程安全）；发布路径自身不再记日志，不会自激成环。
    """

    def __init__(self, bus: EventBus, level: int = logging.ERROR) -> None:
        super().__init__(level=level)
        self._bus = bus

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            if record.exc_info:
                message += "\n" + "".join(traceback.format_exception(*record.exc_info)).rstrip()
        except Exception:  # noqa: BLE001  emit 内异常按 logging 约定交 handleError，不外抛
            self.handleError(record)
            return
        self._bus.publish_nowait(
            Event("log.error", {"message": message, "logger": record.name, "ts": record.created})
        )


def attach_event_log_bridge(bus: EventBus, root: logging.Logger | None = None) -> None:
    """把桥接 handler 挂到根 logger（幂等）；在组合根（run）调用一次。"""
    root = root or logging.getLogger()
    if any(isinstance(h, EventBusLogHandler) for h in root.handlers):
        return
    root.addHandler(EventBusLogHandler(bus))
