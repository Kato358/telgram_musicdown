"""TelegramManager：UserClient/BotClient 生命周期与依赖注入代理（SDD §1.1）。

- 未登录（无会话/未连接）时 user/download 代理返回 not_connected 领域异常。
- 登录成功（sign_in/会话有效）后 start() 创建客户端并替换代理指向。
- Bot 可选：bot_token 为空则不启动。
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

from app.config import SecretConfig
from app.errors import AppError, SessionLockedError
from app.telegram.bot_client import BotClient
from app.telegram.user_client import UserClient

logger = logging.getLogger(__name__)


class _UserProxy:
    """SourceService/SearchService 所需协议面；未连接时报错。"""

    def __init__(self, mgr: TelegramManager) -> None:
        self._mgr = mgr

    def _client(self) -> UserClient:
        c = self._mgr.user
        if c is None:
            raise AppError("not_connected", "telegram 未登录：请先在 Web 完成初始化登录")
        return c

    def search_messages(
        self, chat_id: int, query: str, limit: int, offset: int
    ) -> list[dict[str, Any]]:
        return self._client().search_messages(chat_id, query, limit, offset)

    def get_chat(self, entity: str | int) -> dict[str, Any]:
        return self._client().get_chat(entity)

    def iter_messages(
        self, chat_id: int, reverse: bool, offset_id: int, limit: int
    ) -> list[dict[str, Any]]:
        return self._client().iter_messages(chat_id, reverse, offset_id, limit)


class _DownloadProxy:
    """DownloadService/PreviewService 所需协议面；未连接时报错。"""

    def __init__(self, mgr: TelegramManager) -> None:
        self._mgr = mgr

    def download_media(self, message_ref: dict[str, Any], file_name: str) -> Any:
        c = self._mgr.user
        if c is None:
            raise AppError("not_connected", "telegram 未登录：请先在 Web 完成初始化登录")
        return c.download_media(message_ref, file_name)


class TelegramManager:
    """持有 User/Bot Client；登录前为 None，登录后可重建。"""

    _downloads: Any = None
    _search: Any = None

    def __init__(self, secrets: SecretConfig, session_dir: Path) -> None:
        self.secrets = secrets
        self.session_dir = session_dir
        self.user: UserClient | None = None
        self.bot: BotClient | None = None
        self.user_client_proxy = _UserProxy(self)
        self.download_client_proxy = _DownloadProxy(self)

    async def start(self) -> None:
        """启动已配置的客户端：User 需已有会话；Bot 可选。"""
        # User client 仅在会话文件已存在时自动连接（登录由 Web 初始化完成）
        session_file = self.session_dir / "musicdown.session"
        if self.secrets.has_credentials and session_file.exists():
            await self.connect_user()
        if self.secrets.has_credentials and self.secrets.bot_token and self._downloads is not None:
            self.bot = BotClient(self.secrets, self.session_dir, self._downloads, self._search)


    def set_services(self, downloads: Any, search: Any) -> None:
        """注入 DownloadService/SearchService（Bot handlers 需要）。"""
        self._downloads = downloads
        self._search = search

    async def connect_user(self) -> dict[str, Any]:
        """连接/重建 UserClient；返回 me。会话无效抛出，由路由提示重登。"""
        await self.disconnect_user()
        self.user = UserClient(self.secrets, self.session_dir)
        try:
            await self.user.connect()
        except sqlite3.OperationalError as e:
            self.user = None
            if "locked" not in str(e).lower():
                raise
            raise SessionLockedError(
                f"会话文件被另一个运行实例占用（{self.session_dir}），请先停止旧进程再启动"
            ) from e
        return await self.user.get_me()

    async def disconnect_user(self) -> None:
        if self.user is not None:
            try:
                await self.user.disconnect()
            except Exception:  # noqa: BLE001  断连失败不影响重登
                logger.warning("user disconnect failed", exc_info=True)
            self.user = None

    async def start_bot(self) -> None:
        if self.bot is not None:
            try:
                await self.bot.start()
            except sqlite3.OperationalError as e:
                if "locked" not in str(e).lower():
                    raise
                raise SessionLockedError(
                    f"Bot 会话文件被另一个运行实例占用（{self.session_dir}），请先停止旧进程再启动"
                ) from e

    async def stop(self) -> None:
        if self.bot is not None:
            try:
                await self.bot.stop()
            except Exception:  # noqa: BLE001
                logger.warning("bot stop failed", exc_info=True)
            self.bot = None
        await self.disconnect_user()
