"""事件总线日志桥测试：ERROR+ 记录发布为 log.error 事件（FR-WEB-06 日志页）。"""

from __future__ import annotations

import asyncio
import logging

import pytest

from app.events import Event, EventBus, EventBusLogHandler, attach_event_log_bridge

BRIDGE_LOGGER = "bridge-test"


@pytest.fixture()
def bridge_logger() -> logging.Logger:
    lg = logging.getLogger(BRIDGE_LOGGER)
    lg.setLevel(logging.DEBUG)
    lg.handlers.clear()
    lg.propagate = False
    yield lg
    lg.handlers.clear()


async def test_error_record_publishes_log_error_event(bridge_logger: logging.Logger) -> None:
    bus = EventBus()
    attach_event_log_bridge(bus, bridge_logger)
    q = await bus.subscribe()
    bridge_logger.error("boom %s", "x")
    ev = await asyncio.wait_for(q.get(), timeout=1.0)
    assert isinstance(ev, Event)
    assert ev.type == "log.error"
    assert ev.payload["message"] == "boom x"
    assert ev.payload["logger"] == BRIDGE_LOGGER


async def test_warning_record_is_not_published(bridge_logger: logging.Logger) -> None:
    bus = EventBus()
    attach_event_log_bridge(bus, bridge_logger)
    q = await bus.subscribe()
    bridge_logger.warning("recoverable")
    await asyncio.sleep(0.05)
    with pytest.raises(asyncio.QueueEmpty):
        q.get_nowait()


async def test_exception_info_appends_traceback(bridge_logger: logging.Logger) -> None:
    bus = EventBus()
    attach_event_log_bridge(bus, bridge_logger)
    q = await bus.subscribe()
    try:
        raise ValueError("bad")
    except ValueError:
        bridge_logger.exception("task failed")
    ev = await asyncio.wait_for(q.get(), timeout=1.0)
    message = str(ev.payload["message"])
    assert message.startswith("task failed")
    assert "ValueError: bad" in message


def test_attach_is_idempotent(bridge_logger: logging.Logger) -> None:
    bus = EventBus()
    attach_event_log_bridge(bus, bridge_logger)
    attach_event_log_bridge(bus, bridge_logger)
    assert sum(isinstance(h, EventBusLogHandler) for h in bridge_logger.handlers) == 1
