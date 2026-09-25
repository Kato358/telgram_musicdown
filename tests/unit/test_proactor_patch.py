"""Proactor 连接关闭噪音抑制单测：吞 ConnectionResetError、其余异常照常抛。"""

from __future__ import annotations

import sys
from asyncio.proactor_events import _ProactorBasePipeTransport
from typing import Any

import pytest

from app.utils.proactor_patch import silence_proactor_connection_reset


class _Sock:
    def fileno(self) -> int:
        return 5

    def shutdown(self, how: int) -> None:
        pass

    def close(self) -> None:
        pass


class _ResettingSock(_Sock):
    """shutdown 必抛 ConnectionResetError 的假 socket（模拟对端已 RST）。"""

    def shutdown(self, how: int) -> None:
        raise ConnectionResetError(10054, "远程主机强迫关闭了一个现有的连接")


class _BrokenSock(_ResettingSock):
    """shutdown 抛非连接类错误，应保持可观测、不被吞。"""

    def shutdown(self, how: int) -> None:
        raise OSError(9, "Bad file descriptor")


def _make_transport(sock: Any) -> _ProactorBasePipeTransport:
    t = _ProactorBasePipeTransport.__new__(_ProactorBasePipeTransport)
    t._called_connection_lost = False  # type: ignore[attr-defined]
    t._closing = False  # type: ignore[attr-defined]  # 避免 GC 时 __repr__ 触发 AttributeError
    t._protocol = type("P", (), {"connection_lost": lambda self, exc: None})()  # type: ignore[attr-defined]
    t._sock = sock  # type: ignore[attr-defined]
    t._server = None  # type: ignore[attr-defined]
    return t


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Proactor 专属路径")
def test_patched_swallows_connection_reset() -> None:
    """补丁后 _call_connection_lost 对已重置 socket 不再抛 10054。"""
    silence_proactor_connection_reset()

    # 已重置 socket：shutdown 抛 ConnectionResetError → 被吞
    _ProactorBasePipeTransport._call_connection_lost(_make_transport(_ResettingSock()), None)
    # 正常 socket：清理流程照常走完
    t_ok = _make_transport(_Sock())
    _ProactorBasePipeTransport._call_connection_lost(t_ok, None)
    assert t_ok._called_connection_lost is True


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Proactor 专属路径")
def test_patched_reraises_other_errors() -> None:
    """非连接类异常（如 OSError）不被吞，保持可观测。"""
    silence_proactor_connection_reset()
    t = _make_transport(_BrokenSock())
    with pytest.raises(OSError):
        _ProactorBasePipeTransport._call_connection_lost(t, None)


def test_silence_is_idempotent() -> None:
    """重复调用不重复包裹（全平台可跑）。"""
    silence_proactor_connection_reset()
    once = _ProactorBasePipeTransport._call_connection_lost
    silence_proactor_connection_reset()
    assert _ProactorBasePipeTransport._call_connection_lost is once
