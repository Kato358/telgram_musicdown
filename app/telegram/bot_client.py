"""BotClient：命令/链接/转发处理（FR-LINK-03/04/05，SDD §4.2）。

- t.me 链接 → 解析入队 → 回复任务 id；完成后回复路径或原因。
- 转发/直接上传音频 → 按全局模板保存。这条消息只有 bot 会话定位得到，故 meta 标记
  ``via_bot``：取数按该标记走 bot 会话（见 ``app.telegram.media`` 的模块说明）。
- 非授权用户消息忽略（allowed_user_ids，默认 me）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from pyrogram import filters
from pyrogram.client import Client
from pyrogram.handlers import MessageHandler
from pyrogram.types import Message

from app.config import SecretConfig
from app.domain import (
    DownloadRequest,
    TrackMeta,
    card_to_meta,
    message_to_card,
)
from app.errors import AppError
from app.services.download import DownloadQueueServiceProto
from app.telegram.media import chat_id_of, message_dict
from app.telegram.media import download_media as media_download
from app.telegram.media import get_messages as media_get_messages
from app.telegram.user_client import (
    CONNECT_TIMEOUT_SEC,
    _connect_error,
    _proxy_dict,
)
from app.utils.linkparse import parse_link

logger = logging.getLogger(__name__)

SESSION_NAME = "musicdown-bot"  # 会话文件名（sessions/musicdown-bot.session）


class BotClient:
    """Bot Client：链接与音频指令（FR-LINK-03/04/05）+ 搜索（bot 可搜索音乐）。"""

    MAX_LIST = 10  # /search 回复最多列出的条数

    def __init__(
        self,
        secrets: SecretConfig,
        session_dir: Any,
        downloads: DownloadQueueServiceProto,
        search: Any = None,
        allowed_user_ids: set[int] | None = None,
    ) -> None:
        self.downloads = downloads
        self.search = search
        self.secrets = secrets
        self._last_results: dict[int, list[Any]] = {}
        self.allowed_user_ids = allowed_user_ids or set()
        self.client = Client(
            SESSION_NAME,
            api_id=secrets.api_id,
            api_hash=secrets.api_hash,
            bot_token=secrets.bot_token or None,
            workdir=str(session_dir),
            proxy=_proxy_dict(secrets),  # type: ignore[arg-type]  # scheme 是配置字符串，收窄不了
        )
        self._register_handlers()

    def _register_handlers(self) -> None:
        # Kurigram 的 on_message 装饰器把首参标成 `OnMessage | Filter | None`（为支持未绑定
        # 调用），绑定式 `@c.on_message(...)` 过不了 mypy；这里直接用它内部同一套
        # MessageHandler + add_handler，注册时机（构造期）、分组（0）与装饰器写法一致。
        c = self.client

        async def on_download(_client: Client, msg: Message) -> None:
            if not self._is_allowed(msg):
                return
            await self._handle_download(msg)

        c.add_handler(MessageHandler(on_download, filters.command("download")))

        async def on_audio(_client: Client, msg: Message) -> None:
            if not self._is_allowed(msg):
                return
            await self._handle_forwarded_audio(msg)

        c.add_handler(
            MessageHandler(on_audio, filters.private & (filters.audio | filters.document))
        )

        async def on_status(_client: Client, msg: Message) -> None:
            if not self._is_allowed(msg):
                return
            tasks = self.downloads.store.list_tasks(limit=5)
            lines = [f"#{t.id} {t.status} {t.type}" for t in tasks]
            await msg.reply("\n".join(lines) or "no tasks")

        c.add_handler(MessageHandler(on_status, filters.command("status")))

        async def on_cancel(_client: Client, msg: Message) -> None:
            if not self._is_allowed(msg):
                return
            parts = (msg.text or "").split()
            if len(parts) < 2 or not parts[1].isdigit():  # noqa: PLR2004  命令参数语义
                await msg.reply("usage: /cancel <id>")
                return
            await self.downloads.cancel_task(int(parts[1]))
            await msg.reply(f"cancelled #{parts[1]}")

        c.add_handler(MessageHandler(on_cancel, filters.command("cancel")))

        async def on_help(_client: Client, msg: Message) -> None:
            if not self._is_allowed(msg):
                return
            await msg.reply(
                "/search <关键词> - 在已启用音乐源内搜索\n"
                "/download <序号> - 下载上次搜索结果的第 n 条\n"
                "/download <link> [start] [end] - 下载链接；start=1 对话起始，0=最新\n"
                "/status - 最近 5 条任务\n"
                "/cancel <id> - 取消任务\n"
                "转发音频或直接发送音频文件可下载"
            )

        c.add_handler(MessageHandler(on_help, filters.command("help")))

        async def on_search(_client: Client, msg: Message) -> None:
            if not self._is_allowed(msg):
                return
            await self._handle_search(msg)

        c.add_handler(MessageHandler(on_search, filters.command("search")))

    def _is_allowed(self, msg: Message) -> bool:
        """非授权用户消息忽略（FR-AUTH-04）。"""
        if not self.allowed_user_ids:
            return True  # 默认 me（登录账号）经 user_id 注入
        return msg.from_user is not None and msg.from_user.id in self.allowed_user_ids

    async def _handle_download(self, msg: Message) -> None:
        """/download <序号>|<link> [start] [end]（FR-LINK-03/05 + Bot 搜索下载）。"""
        parts = (msg.text or "").split()
        if len(parts) < 2:  # noqa: PLR2004  命令参数语义
            await msg.reply("usage: /download <序号> 或 /download <link> [start] [end]")
            return
        if parts[1].isdigit():
            await self._download_by_index(msg, int(parts[1]))
            return
        try:
            link = parse_link(parts[1])
        except AppError as e:
            await msg.reply(f"解析失败: {e.message}")
            return
        if link.chat_id is None or link.message_id is None:
            await msg.reply("链接需要包含消息 id")
            return
        task_id = await self.downloads.enqueue(
            DownloadRequest(meta=TrackMeta(chat_id=link.chat_id, message_id=link.message_id))
        )
        if task_id is None:
            await msg.reply("任务 #dedupe：已存在，跳过")
            return
        await msg.reply(f"任务 #{task_id}")

    async def _handle_search(self, msg: Message) -> None:
        """/search <关键词>：在已启用源内搜索，回复编号列表供 /download <n>（需求：bot 可搜索）。"""
        if self.search is None:
            await msg.reply("搜索不可用：Telegram 未登录")
            return
        q = (msg.text or "").partition(" ")[2].strip()
        if not q:
            await msg.reply("usage: /search <关键词>")
            return
        try:
            resp = await self.search.search(q)
        except AppError as e:
            await msg.reply(f"搜索失败: {e.message}")
            return
        if not resp.results:
            reason = resp.meta.get("reason", "")
            if reason == "no_enabled_sources":
                await msg.reply("没有已启用的音乐源，先在 Web「音乐源」页添加频道")
            else:
                await msg.reply(f"没有找到「{q}」相关音频")
            return
        self._last_results[chat_id_of(msg)] = resp.results
        lines = [f"搜索「{q}」，回复 /download <序号> 下载："]
        for i, card in enumerate(resp.results[: self.MAX_LIST], start=1):
            d = card.duration_sec
            dur = f"{d // 60}:{d % 60:02d}" if d else "?"
            artist = card.artist or "?"
            lines.append(f"{i}. {artist} - {card.title} [{dur}, {card.ext or '?'}]")
        if len(resp.results) > self.MAX_LIST:
            lines.append(f"…共 {len(resp.results)} 条，仅显示前 {self.MAX_LIST}")
        await msg.reply("\n".join(lines))

    async def _download_by_index(self, msg: Message, index: int) -> None:
        """按上次搜索结果序号入队下载。"""
        results = self._last_results.get(chat_id_of(msg))
        if not results:
            await msg.reply("请先 /search 再按序号下载")
            return
        if not 1 <= index <= len(results):
            await msg.reply(f"序号需在 1~{len(results)} 之间")
            return
        card = results[index - 1]
        task_id = await self.downloads.enqueue(DownloadRequest(meta=card_to_meta(card)))
        if task_id is None:
            await msg.reply("已存在，跳过")
            return
        await msg.reply(f"任务 #{task_id}：{card.artist or ''} {card.title or ''}".strip())

    async def _handle_forwarded_audio(self, msg: Message) -> None:
        """转发/直接上传音频（FR-LINK-04）：不依赖源配置。"""
        task_id = await self.downloads.enqueue(DownloadRequest(meta=forwarded_meta(msg)))
        if task_id is None:
            await msg.reply("已存在，跳过")
            return
        await msg.reply(f"任务 #{task_id}")

    async def start(self) -> None:
        """启动 Bot（FR-AUTH-04）：与 User 同样的硬超时，避免死代理下无限重试。"""
        try:
            await asyncio.wait_for(self.client.start(), timeout=CONNECT_TIMEOUT_SEC)
        except TimeoutError as e:
            raise _connect_error(self.secrets, e) from e
        except (OSError, ConnectionError) as e:
            raise _connect_error(self.secrets, e) from e

    async def stop(self) -> None:
        await self.client.stop()

    async def get_messages(self, chat_id: int, message_ids: list[int]) -> list[dict[str, Any]]:
        """按 id 取消息 dict（转发入队的 meta 补全/取数都在这个会话上做）。"""
        return await media_get_messages(self.client, chat_id, message_ids)

    async def download_media(
        self,
        message_ref: dict[str, Any],
        file_name: str,
        progress: Callable[[int, int], None] | None = None,
    ) -> str | None:
        """按 (chat_id, message_id) 下载到临时路径（FR-DL-01）；经 with_flood_retry（NFR-09）。"""
        return await media_download(self.client, message_ref, file_name, progress)


def forwarded_meta(msg: Message) -> TrackMeta:
    """bot 私聊里转发的音频 → 入队元数据（FR-LINK-04）。

    **必须标 ``via_bot``**：这条消息只存在于 bot 会话里。bot 看到的 ``chat.id`` 是发信
    用户的 user_id，登录账号拿同一个 id 去 ``get_messages`` 落到的是它自己的收藏夹，
    取回一条空消息——任务于是以 ``task crashed: This message doesn't contain any
    downloadable media`` 收场。也不能改成交接 ``file_id`` 让登录账号下：Telegram 的
    ``file_reference`` 按账号签发，跨账号调 ``upload.getFile`` 会被判
    ``FILE_REFERENCE_EXPIRED``。消息在哪一侧看得见，就由那一侧去取。

    顺带把 ext/大小/标题一并落进 meta：落盘扩展名、标签容器与完整性校验不必再回查
    （登录账号那条消息本来就查不到）。
    """
    meta = card_to_meta(message_to_card(message_dict(msg)))
    meta.via_bot = True
    return meta
