"""BotClient：命令/链接/转发处理（FR-LINK-03/04/05，SDD §4.2）。

- t.me 链接 → 解析入队 → 回复任务 id；完成后回复路径或原因。
- 转发/直接上传音频 → 按全局模板保存。
- 非授权用户消息忽略（allowed_user_ids，默认 me）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from pyrogram import filters
from pyrogram.client import Client
from pyrogram.types import Message

from app.config import SecretConfig
from app.domain import DownloadRequest, TrackMeta, card_to_meta
from app.errors import AppError
from app.services.download import DownloadQueueServiceProto
from app.telegram.user_client import CONNECT_TIMEOUT_SEC, _connect_error, _proxy_dict
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
            bot_token=secrets.bot_token or None,  # type: ignore[arg-type]
            workdir=str(session_dir),
            proxy=_proxy_dict(secrets),  # type: ignore[arg-type]  # pyrogram 期望 dict
        )
        self._register_handlers()

    def _register_handlers(self) -> None:
        c = self.client

        @c.on_message(filters.command("download"))
        async def on_download(_client: Client, msg: Message) -> None:
            if not self._is_allowed(msg):
                return
            await self._handle_download(msg)

        @c.on_message(filters.private & (filters.audio | filters.document))
        async def on_audio(_client: Client, msg: Message) -> None:
            if not self._is_allowed(msg):
                return
            await self._handle_forwarded_audio(msg)

        @c.on_message(filters.command("status"))
        async def on_status(_client: Client, msg: Message) -> None:
            if not self._is_allowed(msg):
                return
            tasks = self.downloads.store.list_tasks(limit=5)
            lines = [f"#{t.id} {t.status} {t.type}" for t in tasks]
            await msg.reply("\n".join(lines) or "no tasks")

        @c.on_message(filters.command("cancel"))
        async def on_cancel(_client: Client, msg: Message) -> None:
            if not self._is_allowed(msg):
                return
            parts = (msg.text or "").split()
            if len(parts) < 2 or not parts[1].isdigit():  # noqa: PLR2004  命令参数语义
                await msg.reply("usage: /cancel <id>")
                return
            await self.downloads.cancel_task(int(parts[1]))
            await msg.reply(f"cancelled #{parts[1]}")

        @c.on_message(filters.command("help"))
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

        @c.on_message(filters.command("search"))
        async def on_search(_client: Client, msg: Message) -> None:
            if not self._is_allowed(msg):
                return
            await self._handle_search(msg)

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
        self._last_results[msg.chat.id if msg.chat else 0] = resp.results
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
        results = self._last_results.get(msg.chat.id if msg.chat else 0)
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
        task_id = await self.downloads.enqueue(
            DownloadRequest(
                meta=TrackMeta(chat_id=msg.chat.id if msg.chat else 0, message_id=msg.id)
            )
        )
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
