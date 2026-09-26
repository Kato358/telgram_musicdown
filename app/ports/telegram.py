"""Telegram 客户端端口（DIP）：服务层与适配器之间的协议面。

三种能力分开声明（ISP）——**谁用哪一份就只声明那一份**：

============  ==========================================
协议           能力（调用方）
============  ==========================================
``DialogClientProto`` 解析对话 / 列候选（``SourceService``）
``SearchClientProto``  对话内搜索与全账号搜索（``SearchService``、音乐源适配器）
``MediaClientProto``   下载音频 / 取消息（``DownloadService``、音乐源适配器、试听）
============  ==========================================

真实实现在 telegram 层：``UserClient`` 同时提供前两种（``UserClientProto`` 就是
这两者的合成），启动期代理 ``manager._UserProxy`` / ``_DownloadProxy`` 实现同一批协议；
未登录时由 ``telegram/unconnected.py`` 的占位实现顶上。测试用
``tests.fakes.FakeUserClient`` 实现同一批协议（NFR-07）。
"""

from __future__ import annotations

from typing import Any, Protocol

from app.ports.music import ProgressCb


class DialogClientProto(Protocol):
    """音乐源管理所需协议面：解析对话与列出候选。"""

    async def get_chat(self, entity: str | int) -> dict[str, Any]: ...

    async def list_dialogs(self, limit: int) -> list[dict[str, Any]]: ...


class SearchClientProto(Protocol):
    """搜索所需协议面：对话内搜索与全账号搜索。"""

    async def search_messages(
        self, chat_id: int, query: str, limit: int, offset: int
    ) -> list[dict[str, Any]]: ...

    async def search_global(self, query: str, limit: int) -> list[dict[str, Any]]: ...


class MediaClientProto(Protocol):
    """取音频所需协议面：下载与按 id 取消息（补全元数据用）。"""

    async def download_media(
        self,
        message_ref: dict[str, Any],
        file_name: str,
        progress: ProgressCb | None = None,
    ) -> Any: ...

    async def get_messages(self, chat_id: int, message_ids: list[int]) -> Any: ...


class UserClientProto(SearchClientProto, DialogClientProto, Protocol):
    """User Client 的完整协议面（搜索 + 源管理）：组合根注入的就是这一个对象。"""
