"""UserClient：登录、搜索、取消息、下载（FR-AUTH-01/03、FR-SEARCH-01、SDD §2.1）。

services 层不 import pyrogram；本层把底层异常翻译为领域异常（编码规范 §2.4）。
Pyrogram 调用全部经 ``with_flood_retry()``（编码规范 §2.3）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from pyrogram.client import Client
from pyrogram.enums import MessagesFilter
from pyrogram.errors import (  # type: ignore[attr-defined]  # 运行时存在
    PasswordHashInvalid,
    PhoneCodeExpired,
    PhoneCodeInvalid,
    PhoneNumberBanned,
    PhoneNumberInvalid,
    RPCError,
    SessionPasswordNeeded,
    Unauthorized,
)
from pyrogram.types import Message

from app.config import SecretConfig
from app.errors import AuthError, SourceUnreachableError
from app.telegram.flood import with_flood_retry

logger = logging.getLogger(__name__)

MAX_DIALOG_SCAN = 200  # 候选源扫描上限：一次 get_dialogs，不做逐会话额外请求（FR-SRC-05）
CONNECT_TIMEOUT_SEC = 30  # Pyrogram 对连不上的代理会无限重试，故本层给硬超时（FR-AUTH-03）
SESSION_NAME = "musicdown"  # 会话文件名（sessions/musicdown.session）；登出按此名删除


def _proxy_text(cfg: SecretConfig) -> str:
    proxy = cfg.proxy
    if proxy is None:
        return "直连"
    return f"{proxy.scheme} {proxy.hostname}:{proxy.port}"


def _connect_error(cfg: SecretConfig, cause: BaseException) -> AuthError:
    """连不上 Telegram（超时/被拒）：给出可操作的修复方向（FR-AUTH-03）。"""
    logger.warning("telegram unreachable via %s: %s", _proxy_text(cfg), cause)
    return AuthError(
        "connect_failed",
        f"连不上 Telegram（{_proxy_text(cfg)}）：检查网络，或回第 1 步改代理设置后重试",
    )


def _proxy_dict(cfg: SecretConfig) -> dict[str, Any] | None:
    if cfg.proxy is None:
        return None
    return {
        "scheme": cfg.proxy.scheme,
        "hostname": cfg.proxy.hostname,
        "port": cfg.proxy.port,
        "username": cfg.proxy.username,
        "password": cfg.proxy.password,
    }


class UserClient:
    """User Client 包装：登录、搜索、取消息、下载。"""

    def __init__(self, secrets: SecretConfig, session_dir: Any) -> None:
        self.secrets = secrets
        self.client = Client(
            SESSION_NAME,
            api_id=secrets.api_id,
            api_hash=secrets.api_hash,
            workdir=str(session_dir),
            proxy=_proxy_dict(secrets),  # type: ignore[arg-type]  # pyrogram 期望 dict
            in_memory=False,
        )

    async def connect(self) -> None:
        """连接（FR-AUTH-01/03）：Pyrogram 的会话启动对网络失败是无限重试，故设硬超时。"""
        try:
            await asyncio.wait_for(self.client.connect(), timeout=CONNECT_TIMEOUT_SEC)
        except TimeoutError as e:
            await self.disconnect()
            raise _connect_error(self.secrets, e) from e
        except (OSError, ConnectionError) as e:
            raise _connect_error(self.secrets, e) from e

    async def disconnect(self) -> None:
        try:
            await self.client.disconnect()
        except Exception:  # noqa: BLE001  断连失败不该盖住真正的错误
            logger.warning("user client disconnect failed", exc_info=True)

    async def is_authorized(self) -> bool:
        """会话是否已登录（FR-AUTH-02）：未授权时 get_me 报 Unauthorized。"""
        try:
            await with_flood_retry(self.client.get_me, label="get_me")
        except Unauthorized:
            return False
        except (OSError, ConnectionError) as e:
            raise _connect_error(self.secrets, e) from e
        return True

    async def send_code(self, phone: str) -> str:
        """发验证码（FR-AUTH-01）。返回 phone_code_hash。"""
        try:
            sent = await with_flood_retry(lambda: self.client.send_code(phone), label="send_code")
        except PhoneNumberInvalid as e:
            raise AuthError("phone_invalid", "手机号无效：要带国家码，例如 +8613800000000") from e
        except PhoneNumberBanned as e:
            raise AuthError("phone_banned", "该手机号已被 Telegram 封禁，无法登录") from e
        except (OSError, ConnectionError) as e:
            raise _connect_error(self.secrets, e) from e
        return sent.phone_code_hash or ""

    async def sign_in(
        self, phone: str, code: str, phone_code_hash: str, password: str | None = None
    ) -> None:
        """登录（FR-AUTH-01）；两步验证缺密码时抛 password_required，由前端展开密码框。"""
        try:
            await with_flood_retry(
                lambda: self.client.sign_in(phone, phone_code_hash, code), label="sign_in"
            )
            return
        except SessionPasswordNeeded as e:
            if not password:
                raise AuthError("password_required", "该账号开启了两步验证：请填写登录密码") from e
        except PhoneCodeInvalid as e:
            raise AuthError("code_invalid", "验证码不正确：Telegram 给的是 5 位数字") from e
        except PhoneCodeExpired as e:
            raise AuthError("code_expired", "验证码已过期：请重新发送验证码") from e
        except (OSError, ConnectionError) as e:
            raise _connect_error(self.secrets, e) from e
        except RPCError as e:  # 其余 RPC 失败如实上报，不假装成功
            raise AuthError("sign_in_failed", f"登录失败：{type(e).__name__}") from e
        try:
            await with_flood_retry(
                lambda: self.client.check_password(password or ""), label="check_password"
            )
        except PasswordHashInvalid as e:
            raise AuthError("password_invalid", "两步验证密码不正确") from e

    async def get_me(self) -> dict[str, Any]:
        try:
            me = await with_flood_retry(self.client.get_me, label="get_me")
        except (OSError, ConnectionError) as e:
            raise _connect_error(self.secrets, e) from e
        return {
            "id": me.id,
            "display_name": f"{me.first_name or ''} {me.last_name or ''}".strip(),
            "username": me.username,
            "premium": bool(me.is_premium),
            "connected": True,
        }

    async def list_dialogs(self, limit: int = MAX_DIALOG_SCAN) -> list[dict[str, Any]]:
        """账号已加入的对话（FR-SRC-05）：一次扫描取回，供候选源筛选。

        只取对话对象自带字段（含成员数，若 Telegram 给了），不做逐会话 get_chat——
        每个候选再发一次请求会成倍放大 FloodWait 风险。
        """

        async def _collect() -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            async for dialog in self.client.get_dialogs(limit=limit):  # type: ignore[union-attr]
                chat = dialog.chat
                if chat is None:
                    continue
                chat_type = getattr(getattr(chat, "type", None), "value", None) or "user"
                if chat_type == "bot":  # 机器人不是音乐源
                    continue
                title = getattr(chat, "title", None)
                if not title:
                    first = getattr(chat, "first_name", None) or ""
                    last = getattr(chat, "last_name", None) or ""
                    title = f"{first} {last}".strip() or str(chat.id)
                out.append(
                    {
                        "chat_id": chat.id,
                        "title": title,
                        "username": getattr(chat, "username", None),
                        "type": chat_type,
                        "members": getattr(chat, "members_count", None),
                    }
                )
                if len(out) >= limit:
                    break
            return out

        return await with_flood_retry(_collect, label="get_dialogs")

    async def get_chat(self, entity: str | int) -> dict[str, Any]:
        """添加源：get_chat（FR-SRC-01）；失败翻译为领域异常。"""
        try:
            chat = await with_flood_retry(lambda: self.client.get_chat(entity), label="get_chat")
        except RPCError as e:
            raise _translate_rpc(e) from e
        chat_id = getattr(chat, "id", 0)
        title = getattr(chat, "title", None) or getattr(chat, "first_name", None) or str(chat_id)
        chat_type = getattr(chat, "type", None)
        type_val = getattr(chat_type, "value", None) or "user"
        return {
            "chat_id": chat_id,
            "title": title,
            "type": type_val,
            "username": getattr(chat, "username", None),
            "can_view_history": True,
        }

    async def search_messages(
        self, chat_id: int, query: str, limit: int, offset: int
    ) -> list[dict[str, Any]]:
        """对话内搜索（FR-SEARCH-01）：服务端 messages.search + Audio filter。"""
        try:
            gen = self.client.search_messages(
                chat_id,
                limit=limit,
                offset=offset,
                query=query,
                filter=MessagesFilter.AUDIO,
            )
            msgs: list[Message] = []
            # pyrogram stub 把 search_messages 的生成器标成可空，实际不会返回 None
            async for m in gen:  # type: ignore[union-attr]
                msgs.append(m)
                if len(msgs) >= limit:
                    break
        except RPCError as e:
            raise _translate_rpc(e) from e
        return [_message_dict(m) for m in msgs]

    async def iter_messages(
        self, chat_id: int, reverse: bool, offset_id: int, limit: int
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        try:
            # get_chat_history 是 pyrogram v2 的历史遍历 API（v1 的 iter_messages 已移除）
            gen = self.client.get_chat_history(  # type: ignore[call-arg]
                chat_id, offset_id=offset_id, reverse=reverse, limit=limit
            )
            if gen is not None:
                async for m in gen:
                    out.append(_message_dict(m))
                    if len(out) >= limit:
                        break
        except RPCError as e:
            raise _translate_rpc(e) from e
        return out

    async def get_messages(self, chat_id: int, message_ids: list[int]) -> list[dict[str, Any]]:
        """按 id 取消息 dict（DownloadService meta 补全用）；单条返回也归一为列表。"""
        try:
            result = await self.client.get_messages(chat_id, message_ids=message_ids)
        except RPCError as e:
            raise _translate_rpc(e) from e
        msgs = result if isinstance(result, list) else [result]
        return [_message_dict(m) for m in msgs if m is not None]

    async def download_media(
        self,
        message_ref: dict[str, Any],
        file_name: str,
        progress: Callable[[int, int], None] | None = None,
    ) -> str | None:
        """下载（FR-DL-01）：msg → temp 路径；经 with_flood_retry（NFR-09）。"""
        chat_id = message_ref["chat_id"]
        message_id = message_ref["message_id"]

        async def _dl() -> str | None:
            result = await self.client.get_messages(chat_id, message_ids=[message_id])
            msgs = result if isinstance(result, list) else [result]
            if not msgs or msgs[0] is None:
                return None
            out = await self.client.download_media(
                msgs[0], file_name=file_name, progress=progress
            )
            return out if isinstance(out, str) else None

        try:
            return await with_flood_retry(_dl, label="download_media")
        except RPCError as e:
            raise _translate_rpc(e) from e


def _message_dict(m: Message) -> dict[str, Any]:
    audio = m.audio
    doc = m.document
    return {
        "chat_id": m.chat.id if m.chat else 0,
        "message_id": m.id,
        "audio": {
            "title": audio.title,
            "performer": audio.performer,
            "duration": audio.duration,
            "file_size": audio.file_size,
            "mime_type": audio.mime_type,
            "file_unique_id": audio.file_unique_id,
            "file_name": audio.file_name,
            "bitrate": getattr(audio, "bitrate", None),
        }
        if audio
        else None,
        "document": {
            "mime_type": doc.mime_type,
            "file_size": doc.file_size,
            "file_unique_id": doc.file_unique_id,
            "file_name": doc.file_name,
            "duration": None,
            "bitrate": None,
        }
        if doc
        else None,
        "voice": m.voice is not None,
        "caption": m.caption,
        "message_date": m.date.isoformat() if m.date else None,
        "has_thumb": bool(
            (audio and audio.thumbs) or (doc and doc.thumbs)
        ),
    }


def _translate_rpc(e: RPCError) -> SourceUnreachableError:
    """底层异常 → 领域异常（编码规范 §2.4）。"""
    name = type(e).__name__
    if name in ("ChatAdminRequired", "ChannelPrivate", "UserBannedInChannel"):
        return SourceUnreachableError("banned", str(e))
    if name in ("UsernameInvalid", "UsernameNotOccupied"):
        return SourceUnreachableError("invalid_link", str(e))
    return SourceUnreachableError("not_joined", str(e))
