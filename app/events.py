"""进程内事件总线（SDD §1.4）。

一个简单的 asyncio.Queue 广播器，供 SSE ``/api/events`` 使用。
事件类型：task.progress、task.status、log.error、preview.ready、session.invalid。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
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

    async def subscribe(self) -> asyncio.Queue[Event]:
        q: asyncio.Queue[Event] = asyncio.Queue(maxsize=self._maxsize)
        async with self._lock:
            self._subscribers.add(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue[Event]) -> None:
        async with self._lock:
            self._subscribers.discard(q)

    async def publish(self, event: Event) -> None:
        """投递事件到全部订阅者；满队列丢弃最旧（Web 至少 1s 刷新可降级轮询）。"""
        async with self._lock:
            targets = list(self._subscribers)
        for q in targets:
            if q.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    q.get_nowait()
            q.put_nowait(event)

    def publish_nowait(self, event: Event) -> None:
        """同步上下文便捷发布（worker 线程经 to_thread 回调不可用）。"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.debug("event %s dropped: no running loop", event.type)
            return
        loop.create_task(self.publish(event))
