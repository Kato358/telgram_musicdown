"""TelegramManager：UserClient/BotClient 生命周期与依赖注入代理（SDD §1.1）。

- 未登录（无会话/未连接）时 user/download 代理返回 not_connected 领域异常。
- 登录成功（sign_in/会话有效）后 start() 创建客户端并替换代理指向。
- Bot 可选：bot_token 为空则不启动。
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.config import SecretConfig
from app.errors import AppError, SessionLockedError
from app.services.download import DownloadQueueServiceProto
from app.telegram.bot_client import SESSION_NAME as BOT_SESSION_NAME
from app.telegram.bot_client import BotClient
from app.telegram.user_client import SESSION_NAME as USER_SESSION_NAME
from app.telegram.user_client import UserClient

logger = logging.getLogger(__name__)


class _UserProxy:
    """SourceService/SearchService 所需协议面；未登录时报错。"""

    def __init__(self, mgr: TelegramManager) -> None:
        self._mgr = mgr

    def _client(self) -> UserClient:
        c = self._mgr.authorized_client()
        if c is None:
            raise AppError("not_connected", "telegram 未登录：请先在 Web 完成初始化登录")
        return c

    async def search_messages(
        self, chat_id: int, query: str, limit: int, offset: int
    ) -> list[dict[str, Any]]:
        return await self._client().search_messages(chat_id, query, limit, offset)

    async def search_global(self, query: str, limit: int) -> list[dict[str, Any]]:
        return await self._client().search_global(query, limit)

    async def get_chat(self, entity: str | int) -> dict[str, Any]:
        return await self._client().get_chat(entity)

    async def list_dialogs(self, limit: int) -> list[dict[str, Any]]:
        return await self._client().list_dialogs(limit)


class _DownloadProxy:
    """DownloadService/PreviewService 所需协议面；未登录时报错。"""

    def __init__(self, mgr: TelegramManager) -> None:
        self._mgr = mgr

    def download_media(
        self,
        message_ref: dict[str, Any],
        file_name: str,
        progress: Callable[[int, int], None] | None = None,
    ) -> Any:
        """按定位取音频。``via_bot`` 的消息改走 bot 会话（FR-LINK-04）。

        转发进 bot 私聊的消息只存在于 bot 会话：bot 侧 chat_id 是发信用户的 user_id，
        登录账号拿同一个 id 去查会落到自己的收藏夹。bot 没起（token 空/启动失败）时
        明确报出来，而不是让任务以一句取不到音频收场。
        """
        if message_ref.get("via_bot"):
            bot = self._mgr.bot
            if bot is None:
                raise AppError(
                    "bot_unavailable",
                    "bot 未运行：转发进 bot 的音频只能由 bot 会话取，请检查 bot_token",
                )
            return bot.download_media(message_ref, file_name, progress=progress)
        c = self._mgr.authorized_client()
        if c is None:
            raise AppError("not_connected", "telegram 未登录：请先在 Web 完成初始化登录")
        return c.download_media(message_ref, file_name, progress=progress)

    def get_messages(self, chat_id: int, message_ids: list[int]) -> Any:
        """取消息（meta 补全用）。只服务登录账号看得见的对话——转发入队的 meta 在 bot
        侧就补全完了（``DownloadService._run_task`` 对 ``via_bot`` 跳过补全）。"""
        c = self._mgr.authorized_client()
        if c is None:
            raise AppError("not_connected", "telegram 未登录：请先在 Web 完成初始化登录")
        return c.get_messages(chat_id, message_ids=message_ids)


class TelegramManager:
    """持有 User/Bot Client 与其生命周期（FR-AUTH-01/02/04）。

    ``user`` 在「已连接但未授权」（登录中途）时也非 None，故是否已登录一律看
    ``authorized``；``authorized_client()`` 是下载/搜索/同步层取客户端的唯一入口。
    """

    _downloads: DownloadQueueServiceProto | None = None
    _search: Any = None

    def __init__(self, secrets: SecretConfig, session_dir: Path) -> None:
        self.secrets = secrets
        self.session_dir = session_dir
        self.user: UserClient | None = None
        self.bot: BotClient | None = None
        self.authorized = False
        self.user_client_proxy = _UserProxy(self)
        self.download_client_proxy = _DownloadProxy(self)

    def authorized_client(self) -> UserClient | None:
        """已登录则返回 UserClient，否则 None（调用方决定报错还是降级）。"""
        return self.user if self.authorized else None

    async def start(self) -> None:
        """启动已配置的客户端：User 需已有会话；Bot 可选。

        连接失败不阻断启动——Web 向导就是用来修配置与重新登录的（FR-OPS-02）；
        会话文件被其它实例占用仍视为致命错误，由上层退出。
        """
        session_file = self.session_dir / "musicdown.session"
        if self.secrets.has_credentials and session_file.exists():
            try:
                await self.connect_user()
            except SessionLockedError:
                raise
            except AppError as e:  # 连不上（代理/网络）：留给向导修，Web 照常可用
                logger.warning("user client not connected at startup: %s", e.message)
            except Exception:  # noqa: BLE001  其余启动期异常只记录，不挡 Web
                logger.warning("user client connect failed at startup", exc_info=True)
        await self.start_bot_configured()

    def set_services(self, downloads: DownloadQueueServiceProto, search: Any) -> None:
        """注入下载队列与搜索（Bot handlers 需要）。"""
        self._downloads = downloads
        self._search = search

    async def me(self) -> dict[str, Any] | None:
        """已登录账号信息；未连接/未授权返回 None。"""
        if self.authorized_client() is None:
            return None
        try:
            return await self.user.get_me()  # type: ignore[union-attr]
        except AppError:
            return None

    async def connect_user(self) -> dict[str, Any] | None:
        """连接/重建 UserClient；返回 me（未授权时 None）。会话无效抛出，由路由提示重登。"""
        await self.disconnect_user()
        self.user = UserClient(self.secrets, self.session_dir)
        try:
            await self.user.connect()
            self.authorized = await self.user.is_authorized()
        except sqlite3.OperationalError as e:
            self.user = None
            self.authorized = False
            if "locked" not in str(e).lower():
                raise
            raise SessionLockedError(
                f"会话文件被另一个运行实例占用（{self.session_dir}），请先停止旧进程再启动"
            ) from e
        return await self.user.get_me() if self.authorized else None

    def mark_authorized(self) -> None:
        """登录成功后置位（FR-AUTH-01）。"""
        self.authorized = True

    async def start_bot_configured(self) -> bool:
        """登录后按当前密钥启动 Bot（FR-AUTH-04）：bot_token 为空则不动。

        Bot 起不来（token 错、连不上）不该阻断登录本身：记录警告、留 None，
        用户仍可用 Web 搜索与下载。
        """
        if self._downloads is None or not self.secrets.bot_token:
            return False
        if self.bot is not None:
            return True
        allowed: set[int] = set()
        info = await self.me()
        if info and info.get("id"):
            allowed.add(int(info["id"]))  # allowed_user_ids 默认 me（FR-AUTH-04）
        self.bot = BotClient(
            self.secrets,
            self.session_dir,
            downloads=self._downloads,  # 装配期已保证非 None（上方早退）
            search=self._search,
            allowed_user_ids=allowed,
        )
        try:
            await self.start_bot()
        except SessionLockedError:
            raise
        except Exception as e:  # noqa: BLE001  Bot 不可用不是致命错误
            logger.warning("bot start failed: %s", e)
            self.bot = None
            return False
        return True

    async def disconnect_user(self) -> None:
        if self.user is not None:
            try:
                await self.user.disconnect()
            except Exception:  # noqa: BLE001  断连失败不影响重登
                logger.warning("user disconnect failed", exc_info=True)
            self.user = None
        self.authorized = False

    async def logout(self, *, purge_bot_session: bool = False) -> list[str]:
        """登出（FR-AUTH-02）：停 Bot、断 User、删会话文件；下载文件与数据库不动。

        必须删会话文件：留着它下次启动会判「已有有效会话」直接放行，用户点了退出却还在登录。
        Bot 会话只在重新初始化时一并删——那时 bot_token 多半要换，旧会话文件会让新 token
        复用旧 Bot 身份（ApiBot 的 auth key 与 token 绑定）。
        """
        await self.stop()
        return self.delete_sessions(purge_bot_session=purge_bot_session)

    def delete_sessions(self, *, purge_bot_session: bool = False) -> list[str]:
        """删除会话文件（含 sqlite 的 -journal/-wal 边车），返回被删文件名。"""
        names = [USER_SESSION_NAME]
        if purge_bot_session:
            names.append(BOT_SESSION_NAME)
        removed: list[str] = []
        for name in names:
            for path in sorted(self.session_dir.glob(f"{name}.session*")):
                path.unlink(missing_ok=True)
                removed.append(path.name)
        return removed

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
