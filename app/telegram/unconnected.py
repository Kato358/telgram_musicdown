"""未连接占位客户端：装配期还没有 Telegram 会话时的默认注入物。

组合根在 TG 管理器就绪前也要把服务装出来（Web 向导就是在「没有会话」这件事上工作的），
而服务与来源索引都要求一个非空客户端。过去这个位置放的是 ``None``，结果是
「某个链路悄悄拿 None 打上游」——报错是 ``AttributeError``，且要跑一次真实搜索才暴露。
本实现把这种装配态变成**显式的一等客户端**：六个方法一律抛 ``not_connected``
领域异常，与 ``manager`` 的代理在「已部署但未登录」时的行为完全一致。

它不是测试替身：测试的假客户端在 ``tests/fakes``，本类是生产装配的一部分。
"""

from __future__ import annotations

from typing import Any

from app.errors import AppError
from app.ports.music import ProgressCb

_MESSAGE = "telegram 未登录：请先在 Web 完成初始化登录"


class UnconnectedTelegramClient:
    """未登录时的 User/Media 协议面：全部调用即报 ``not_connected``。"""

    async def get_chat(self, entity: str | int) -> dict[str, Any]:
        raise AppError("not_connected", _MESSAGE)

    async def list_dialogs(self, limit: int) -> list[dict[str, Any]]:
        raise AppError("not_connected", _MESSAGE)

    async def search_messages(
        self, chat_id: int, query: str, limit: int, offset: int
    ) -> list[dict[str, Any]]:
        raise AppError("not_connected", _MESSAGE)

    async def search_global(self, query: str, limit: int) -> list[dict[str, Any]]:
        raise AppError("not_connected", _MESSAGE)

    async def download_media(
        self, message_ref: dict[str, Any], file_name: str, progress: ProgressCb | None = None
    ) -> Any:
        raise AppError("not_connected", _MESSAGE)

    async def get_messages(self, chat_id: int, message_ids: list[int]) -> Any:
        raise AppError("not_connected", _MESSAGE)
