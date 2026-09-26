"""Windows Proactor loop 关闭连接时的 ConnectionResetError 噪音抑制（NFR-10 可观测性）。

现象：浏览器切换页面/断开 keep-alive 或 SSE 连接时，对端已发 RST，Proactor 传输
``_call_connection_lost`` 清理 socket 调 ``shutdown(SHUT_RDWR)`` 会再抛
``ConnectionResetError(10054)``，由 asyncio 默认异常处理器打出 ERROR 级
"Exception in callback" 日志（CPython gh-90476，3.13 仍存在）。

属无害噪音，不涉及业务状态；这里仅吞该路径上的连接类异常，其余异常照常抛出。
非 win32 平台为 no-op，可安全地在启动阶段统一调用（幂等）。
"""

from __future__ import annotations

import contextlib
import functools
import sys
from asyncio.proactor_events import _ProactorBasePipeTransport


def silence_proactor_connection_reset() -> None:
    """吞掉 Proactor 连接关闭时对已重置 socket ``shutdown`` 的报错（幂等）。"""
    if sys.platform != "win32" or getattr(silence_proactor_connection_reset, "_applied", False):
        return
    original = _ProactorBasePipeTransport._call_connection_lost  # type: ignore[attr-defined]

    @functools.wraps(original)
    def call_connection_lost(self: _ProactorBasePipeTransport, exc: BaseException | None) -> None:
        with contextlib.suppress(ConnectionResetError, ConnectionAbortedError):
            original(self, exc)

    _ProactorBasePipeTransport._call_connection_lost = call_connection_lost  # type: ignore[attr-defined]
    silence_proactor_connection_reset._applied = True  # type: ignore[attr-defined]
