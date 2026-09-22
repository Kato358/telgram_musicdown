"""FloodWait 统一退避包装（NFR-09，SDD §1.3）。

Pyrogram 调用必须经 ``with_flood_retry()`` 包装，禁止裸调后自行 try FloodWait
（编码规范 §2.3）。按服务端给的 ``value`` 秒挂起该 worker 后重试，其余 worker 不受影响。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

from pyrogram.errors import FloodWait

MAX_FLOOD_RETRIES = 5

T = TypeVar("T")


class FloodWaitExhaustedError(Exception):
    """FloodWait 重试耗尽（MAX_FLOOD_RETRIES 次后）。"""

    def __init__(self, label: str) -> None:
        super().__init__(f"floodwait retries exhausted for {label}")
        self.label = label


logger = logging.getLogger(__name__)


async def with_flood_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    label: str = "call",
    max_retries: int = MAX_FLOOD_RETRIES,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    """执行 fn，FloodWait 按服务端秒数等待后重试（NFR-09）。

    WARNING 级日志记录等待（编码规范 §2.5：可恢复异常）。
    """
    for attempt in range(max_retries):
        try:
            return await fn()
        except FloodWait as e:
            wait_sec = float(e.value)
            logger.warning(
                "floodwait on %s (attempt %d): sleeping %.0fs", label, attempt + 1, wait_sec
            )
            await sleep(wait_sec)
    raise FloodWaitExhaustedError(label)  # pragma: no cover - 循环耗尽
