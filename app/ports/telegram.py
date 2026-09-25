"""Telegram 客户端端口（DIP）：下载与试听所需的最小协议面。

真实实现在 telegram 层（user_client / manager 的 _DownloadProxy），
测试用 ``tests.fakes.FakeUserClient`` 实现同一协议（NFR-07）。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol


class TelegramClientProto(Protocol):
    """下载所需协议面（FakeUserClient 实现，NFR-07）。"""

    def download_media(
        self,
        message_ref: dict[str, Any],
        file_name: str,
        progress: Callable[[int, int], None] | None = None,
    ) -> Any: ...

    def get_messages(self, chat_id: int, message_ids: list[int]) -> Any: ...
