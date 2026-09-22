"""UserClient：登录、搜索、取消息、下载（FR-AUTH-01/03、FR-SEARCH-01、SDD §2.1）。

services 层不 import pyrogram；本层把底层异常翻译为领域异常（编码规范 §2.4）。
Pyrogram 调用全部经 ``with_flood_retry()``（编码规范 §2.3）。
"""

from __future__ import annotations

import logging
from typing import Any

from pyrogram.client import Client
from pyrogram.enums import MessagesFilter
from pyrogram.errors import RPCError  # type: ignore[attr-defined]  # 运行时存在
from pyrogram.types import Message

from app.config import SecretConfig
from app.errors import SourceUnreachableError
from app.telegram.flood import with_flood_retry

logger = logging.getLogger(__name__)


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
            "musicdown",
            api_id=secrets.api_id,
            api_hash=secrets.api_hash,
            workdir=str(session_dir),
            proxy=_proxy_dict(secrets),  # type: ignore[arg-type]  # pyrogram 期望 dict
            in_memory=False,
        )

    async def connect(self) -> None:
        await self.client.connect()

    async def disconnect(self) -> None:
        await self.client.disconnect()

    async def send_code(self, phone: str) -> str:
        """发验证码（FR-AUTH-01）。返回 phone_code_hash。"""
        sent = await with_flood_retry(lambda: self.client.send_code(phone), label="send_code")
        return sent.phone_code_hash or ""

    async def sign_in(
        self, phone: str, code: str, phone_code_hash: str, password: str | None = None
    ) -> Any:
        """登录；2FA 需 password（FR-AUTH-01）。"""
        try:
            return await with_flood_retry(
                lambda: self.client.sign_in(phone, phone_code_hash, code),
                label="sign_in",
            )
        except RPCError:
            if password:
                return await with_flood_retry(
                    lambda: self.client.check_password(password), label="check_password"
                )
            raise

    async def get_me(self) -> dict[str, Any]:
        me = await with_flood_retry(self.client.get_me, label="get_me")
        return {
            "display_name": f"{me.first_name or ''} {me.last_name or ''}".strip(),
            "username": me.username,
            "premium": bool(me.is_premium),
            "connected": True,
        }

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
            gen = self.client.search_messages(  # type: ignore[attr-defined]
                chat_id,
                limit=limit,
                offset=offset,
                query=query,
                filter=MessagesFilter.AUDIO,
            )
            msgs: list[Message] = []
            async for m in gen:
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
            # iter_messages 是 pyrogram 内置方法；mypy stub 不全
            async for m in self.client.iter_messages(  # type: ignore[attr-defined]
                chat_id, offset_id=offset_id, reverse=reverse
            ):
                out.append(_message_dict(m))
                if len(out) >= limit:
                    break
        except RPCError as e:
            raise _translate_rpc(e) from e
        return out

    async def download_media(self, message_ref: dict[str, Any], file_name: str) -> str | None:
        """下载（FR-DL-01）：msg → temp 路径；经 with_flood_retry（NFR-09）。"""
        chat_id = message_ref["chat_id"]
        message_id = message_ref["message_id"]

        async def _dl() -> str | None:
            result = await self.client.get_messages(chat_id, message_ids=[message_id])
            msgs = result if isinstance(result, list) else [result]
            if not msgs or msgs[0] is None:
                return None
            out = await self.client.download_media(msgs[0], file_name=file_name)
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
        }
        if audio
        else None,
        "document": {
            "mime_type": doc.mime_type,
            "file_size": doc.file_size,
            "file_unique_id": doc.file_unique_id,
            "file_name": doc.file_name,
            "duration": None,
        }
        if doc
        else None,
        "voice": m.voice is not None,
        "caption": m.caption,
        "message_date": m.date.isoformat() if m.date else None,
    }


def _translate_rpc(e: RPCError) -> SourceUnreachableError:
    """底层异常 → 领域异常（编码规范 §2.4）。"""
    name = type(e).__name__
    if name in ("ChatAdminRequired", "ChannelPrivate", "UserBannedInChannel"):
        return SourceUnreachableError("banned", str(e))
    if name in ("UsernameInvalid", "UsernameNotOccupied"):
        return SourceUnreachableError("invalid_link", str(e))
    return SourceUnreachableError("not_joined", str(e))
