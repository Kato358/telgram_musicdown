"""两个会话共用的取数动作：按 (chat_id, message_id) 取消息、下载到临时路径。

**为什么是共用的一层**：同一段 Kurigram 调用要在两个会话上跑——登录账号取频道消息，
bot 取「用户转发进它私聊」的消息。逻辑一旦抄两份，退避、异常翻译、空消息判定就会各
走各的。所以本模块只吃一个裸的 Kurigram Client，由 ``UserClient``/``BotClient`` 各自
把 ``self.client`` 传进来（编码规范 §2.3：调用全部经 ``with_flood_retry``）。

**为什么转发任务必须走 bot 会话**：那条消息只存在于 bot 私聊里。bot 看到的
``chat.id`` 是发信用户的 user_id；登录账号拿同一个 id 去 ``get_messages``，peer 落到
的是它自己的「收藏夹」，取回一条空消息（``empty=True``），交给 ``download_media``
只会得到一句 ``This message doesn't contain any downloadable media``。

**不要改成把 file_id 交接给登录账号**：Telegram 的 ``file_reference`` 是按账号签发
的，跨账号拿它调 ``upload.getFile`` 会被服务端判成 ``FILE_REFERENCE_EXPIRED``。消息
在哪一侧看得见，就由那一侧去取。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pyrogram.client import Client
from pyrogram.errors import FloodWait

# RPCError 只有从定义它的模块导入 mypy 才看得见：pyrogram.errors 是用 `import *`
# 逐层再导出的，strict 模式下那条链不被当成显式再导出（运行期是同一个类对象）。
from pyrogram.errors.rpc_error import RPCError
from pyrogram.types import Message

from app.errors import AppError, SourceUnreachableError
from app.telegram.flood import with_flood_retry

# Kurigram 把同一 RPC 错误按 HTTP code 拆成多个类（403/406 变体带数字后缀）。只认无后缀
# 的那个，服务端真按 403 回时就会掉进兜底的 not_joined，界面于是给出错的修复指引。
_BANNED_ERRORS = frozenset(
    {
        "ChatAdminRequired",
        "ChatAdminRequired403",
        "ChannelPrivate",
        "ChannelPrivate406",
        "UserBannedInChannel",
        "UserBannedInChannel403",
    }
)
_INVALID_LINK_ERRORS = frozenset({"UsernameInvalid", "UsernameNotOccupied"})


def translate_rpc(e: RPCError) -> SourceUnreachableError:
    """底层异常 → 领域异常（编码规范 §2.4）。

    FloodWait 单独成一档：它是限流，不是权限。落进兜底的 `not_joined` 会让界面对着一句
    错的修复指引（「先用该账号加入频道」），而 SRS FR-SEARCH-01 要求的正是**限流可见**。
    """
    if isinstance(e, FloodWait):
        return SourceUnreachableError("flood_wait", f"FloodWait {e.seconds or 0}s")
    name = type(e).__name__
    if name in _BANNED_ERRORS:
        return SourceUnreachableError("banned", str(e))
    if name in _INVALID_LINK_ERRORS:
        return SourceUnreachableError("invalid_link", str(e))
    return SourceUnreachableError("not_joined", str(e))


def message_dict(m: Message) -> dict[str, Any]:
    """Message → 层内通用 dict（搜索、取消息、bot 转发共用同一份映射）。"""
    audio = m.audio
    doc = m.document
    return {
        "chat_id": chat_id_of(m),
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
        # 全局搜索（FR-SEARCH-01 global 模式）按它给卡片标来源频道；逐源搜索不用（源标题已知）
        "chat_title": chat_title_of(m),
    }


def chat_id_of(m: Message) -> int:
    """消息所在对话的 id。

    Kurigram 把 ``Chat.id`` / ``Message.chat`` 都标成可空（min 对话、已删除的会话），
    而业务侧（去重、Bot 搜索结果序号）必须拿到一个可比较的整数：取不到时回退 0，
    与"没有对话"等价，不把 None 混进 ``tasks.chat_id``。
    """
    chat = m.chat
    return chat.id if chat is not None and chat.id is not None else 0


def chat_title_of(m: Message) -> str | None:
    """消息所在对话的标题（全局搜索按它给结果卡片标来源频道）。

    取不到就留 None：宁可卡片上少一个频道名，也不编一个假名（同 ``message_to_card`` 约定）。
    """
    chat = m.chat
    if chat is None:
        return None
    return getattr(chat, "title", None) or getattr(chat, "first_name", None) or None


async def get_messages(
    client: Client, chat_id: int, message_ids: list[int]
) -> list[dict[str, Any]]:
    """按 id 取消息 dict（DownloadService meta 补全用）；单条返回也归一为列表。"""

    async def _call() -> list[dict[str, Any]]:
        result = await client.get_messages(chat_id, message_ids=message_ids)
        msgs = result if isinstance(result, list) else [result]
        return [message_dict(m) for m in msgs if m is not None]

    try:
        return await with_flood_retry(_call, label="get_messages")
    except RPCError as e:
        raise translate_rpc(e) from e


async def download_media(
    client: Client,
    message_ref: dict[str, Any],
    file_name: str,
    progress: Callable[[int, int], None] | None = None,
) -> str | None:
    """按 (chat_id, message_id) 取到消息再下到 ``file_name``。

    FR-DL-01：``file_name`` 是临时路径，返回落盘后的路径（取不到消息为 None）。
    经 with_flood_retry（NFR-09）。
    """
    chat_id = message_ref["chat_id"]
    message_id = message_ref["message_id"]

    async def _dl() -> str | None:
        result = await client.get_messages(chat_id, message_ids=[message_id])
        msgs = result if isinstance(result, list) else [result]
        if not msgs or msgs[0] is None:
            return None
        # 空消息（id 在这个会话里对不上）没有附件，交给 Kurigram 只会抛一句看不懂的
        # ValueError；这里说清楚是哪条消息取不到。
        if msgs[0].empty or (msgs[0].audio is None and msgs[0].document is None):
            raise AppError(
                "no_media",
                f"消息 {chat_id}/{message_id} 取不到音频：它不在该对话里，或不是音频",
            )
        out = await client.download_media(
            msgs[0],
            file_name=file_name,
            progress=progress,
        )
        return out if isinstance(out, str) else None

    try:
        return await with_flood_retry(_dl, label="download_media")
    except RPCError as e:
        raise translate_rpc(e) from e
