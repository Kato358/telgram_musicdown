"""假 Telegram 客户端（NFR-07，编码规范 §4.2）。

实现真实协议面：search_messages / get_chat / get_messages /
download_media。可脚本化注入：正常返回、FloodWait、大小不符、非音频。
单测禁止真实网络。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any


class FakeFloodWait(Exception):
    """模拟 pyrogram FloodWait。"""

    def __init__(self, value: int) -> None:
        super().__init__(f"flood wait {value}s")
        self.value = value


class FakeUserClient:
    """可脚本化注入的假客户端。"""

    def __init__(
        self,
        messages: list[dict[str, Any]],
        *,
        content: bytes = b"x" * 100,
        dialogs: list[dict[str, Any]] | None = None,
    ) -> None:
        self.messages = messages
        self.content = content
        self.dialogs = dialogs or []
        self.flood_queue: list[int] = []  # download_media 前依次弹出的 FloodWait
        self.size_override: int | None = None  # 注入大小不符
        self.download_calls = 0

    async def get_chat(self, entity: str | int) -> dict[str, Any]:
        return {
            "chat_id": -100123,
            "title": "Music Channel",
            "type": "channel",
            "username": "music",
            "can_view_history": True,
        }

    async def list_dialogs(self, limit: int) -> list[dict[str, Any]]:
        return self.dialogs[:limit]

    async def search_messages(
        self, chat_id: int, query: str, limit: int, offset: int
    ) -> list[dict[str, Any]]:
        return [
            m
            for m in self.messages
            if query.lower() in str(m.get("audio", {}).get("title", "")).lower()
        ][offset : offset + limit]

    async def get_messages(
        self, chat_id: int, message_ids: list[int]
    ) -> list[dict[str, Any] | None]:
        return [
            next((m for m in self.messages if m["message_id"] == mid), None) for mid in message_ids
        ]

    async def download_media(
        self,
        message_ref: dict[str, Any],
        file_name: str,
        progress: Callable[[int, int], None] | None = None,
    ) -> str | None:
        self.download_calls += 1
        if self.flood_queue:
            raise FakeFloodWait(self.flood_queue.pop(0))
        target = Path(file_name)
        size = self.size_override if self.size_override is not None else len(self.content)
        if progress is not None:
            progress(0, size)
            if size:
                progress(size // 2, size)
            progress(size, size)
        # ASYNC240 豁免：Fake 客户端允许直接写文件（测试环境无网络 IO）
        target.write_bytes(self.content[:size])  # noqa: ASYNC240
        return str(target)


def make_audio_message(
    message_id: int,
    *,
    title: str = "晴天",
    size: int = 100,
    mime: str = "audio/mpeg",
    voice: bool = False,
) -> dict[str, Any]:
    """构造音频消息 dict（协议面形态）。"""
    return {
        "chat_id": -100123,
        "message_id": message_id,
        "audio": {
            "title": title,
            "performer": "周杰伦",
            "duration": 269,
            "file_size": size,
            "mime_type": mime,
            "file_unique_id": f"uniq_{message_id}",
            "file_name": f"{title}.mp3",
        },
        "document": None,
        "voice": voice,
        "caption": "周杰伦 - 晴天",
        "message_date": "2026-01-01T00:00:00+00:00",
    }


def make_audio_document_message(message_id: int, *, title: str = "夜的第七章") -> dict[str, Any]:
    """MIME 为 audio/* 的 document（FR-SRC-02 里 media_scope 的第二档）。"""
    return {
        "chat_id": -100123,
        "message_id": message_id,
        "audio": None,
        "document": {
            "mime_type": "audio/flac",
            "file_size": 100,
            "file_unique_id": f"uniq_{message_id}",
            "file_name": f"{title}.flac",
            "duration": None,
        },
        "voice": False,
        "caption": None,
        "message_date": "2026-02-01T00:00:00+00:00",
    }


def make_video_message(message_id: int) -> dict[str, Any]:
    """视频消息：任何范围都不该入队（验收 #11）。"""
    return {
        "chat_id": -100123,
        "message_id": message_id,
        "audio": None,
        "document": {
            "mime_type": "video/mp4",
            "file_size": 100,
            "file_unique_id": f"uniq_{message_id}",
            "file_name": "clip.mp4",
            "duration": None,
        },
        "voice": False,
        "caption": None,
        "message_date": "2026-02-02T00:00:00+00:00",
    }
