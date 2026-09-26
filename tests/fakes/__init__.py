"""假适配器与假客户端（NFR-07，编码规范 §4.2）。

- ``FakeUserClient``：实现 Telegram 协议面（search_messages / get_chat / get_messages /
  download_media），可脚本化注入：正常返回、FloodWait、大小不符、非音频。
- ``FakeChkszClient``：实现 ChKSz 协议面（三个平台各一组搜索/解析），可脚本化注入
  搜索结果、解析详情与下载内容。
- ``fake_registry``：按生产装配方式拼一个来源索引（测试里三个服务共用同一份）。

单测禁止真实网络。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.ports.repository import IStore
from app.registry import SourceRegistry


class FakeFloodWait(Exception):
    """模拟 Kurigram 的 FloodWait：只保留退避包装真正读的 ``seconds``。"""

    def __init__(self, seconds: int) -> None:
        super().__init__(f"flood wait {seconds}s")
        self.seconds = seconds


class FakeUserClient:
    """可脚本化注入的假客户端。"""

    def __init__(
        self,
        messages: list[dict[str, Any]],
        *,
        content: bytes = b"x" * 100,
        dialogs: list[dict[str, Any]] | None = None,
        global_messages: list[dict[str, Any]] | None = None,
    ) -> None:
        self.messages = messages
        self.content = content
        self.dialogs = dialogs or []
        self.flood_queue: list[int] = []  # download_media 前依次弹出的 FloodWait
        self.size_override: int | None = None  # 注入大小不符
        self.download_calls = 0
        # 全局搜索（FR-SEARCH-01 global 模式）：脚本化的「账号全部对话」命中集
        self.global_messages = global_messages or []
        self.global_calls = 0
        self.global_error: Exception | None = None

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
        if self.flood_queue:
            raise FakeFloodWait(self.flood_queue.pop(0))
        return [
            m
            for m in self.messages
            if query.lower() in str(m.get("audio", {}).get("title", "")).lower()
        ][offset : offset + limit]

    async def search_global(self, query: str, limit: int) -> list[dict[str, Any]]:
        """全局搜索：跨对话返回命中（与 search_messages 同一套「服务端匹配」口径）。"""
        self.global_calls += 1
        if self.global_error is not None:
            raise self.global_error
        return [
            m
            for m in self.global_messages
            if query.lower() in str(m.get("audio", {}).get("title", "")).lower()
        ][:limit]


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


class FakeChkszClient:
    """可脚本化注入的假 ChKSz 客户端。

    按平台名分别给搜索行与解析详情；``downloaded`` 记每次下载的 URL，
    便于断言「请求的是哪一档」。
    """

    def __init__(
        self,
        *,
        rows: dict[str, list[dict[str, Any]]] | None = None,
        detail: dict[str, Any] | None = None,
        content: bytes = b"f" * 64,
    ) -> None:
        self.rows = rows or {}
        self.detail = detail or {}
        self.content = content
        self.downloaded: list[str] = []
        #: 每次解析请求的 (平台, 曲目 id, 音质)，用来断言档位真的传到了上游
        self.resolved: list[tuple[str, str, str]] = []
        self.search_calls: list[tuple[str, str, int, int]] = []

    async def search_163(self, keyword: str, limit: int, offset: int) -> list[dict[str, Any]]:
        self.search_calls.append(("163", keyword, limit, offset))
        return self.rows.get("163", [])[:limit]

    async def search_qq(self, keyword: str, limit: int) -> list[dict[str, Any]]:
        self.search_calls.append(("qq", keyword, limit, 0))
        return self.rows.get("qq", [])[:limit]

    async def search_kugo(self, keyword: str, limit: int) -> list[dict[str, Any]]:
        self.search_calls.append(("kugo", keyword, limit, 0))
        return self.rows.get("kugo", [])[:limit]

    async def resolve_163(self, track_id: str, quality: str) -> dict[str, Any]:
        self.resolved.append(("163", track_id, quality))
        return self.detail

    async def resolve_qq(self, mid: str, quality: str) -> dict[str, Any]:
        self.resolved.append(("qq", mid, quality))
        return self.detail

    async def resolve_kugo(self, track_id: str, quality: str) -> dict[str, Any]:
        self.resolved.append(("kugo", track_id, quality))
        return self.detail

    async def download(
        self,
        url: str,
        dest: Path,
        progress: Callable[[int, int | None], None] | None = None,
    ) -> int:
        self.downloaded.append(url)
        size = len(self.content)
        if progress is not None:
            progress(size, size)
        # ASYNC240 豁免：Fake 客户端允许直接写文件（测试环境无网络 IO）
        dest.write_bytes(self.content)  # noqa: ASYNC240
        return size


def fake_registry(
    store: IStore, client: FakeUserClient, chksz: FakeChkszClient | None = None
) -> SourceRegistry:
    """按生产装配方式拼一个来源索引（在线源默认关闭，测试要开就自己 apply_chksz）。"""
    return SourceRegistry(store, client, client, chksz_client=chksz)


def make_audio_message(
    message_id: int,
    *,
    title: str = "晴天",
    size: int = 100,
    mime: str = "audio/mpeg",
    voice: bool = False,
    chat_id: int = -100123,
    chat_title: str | None = None,
) -> dict[str, Any]:
    """构造音频消息 dict（协议面形态）。"""
    return {
        "chat_id": chat_id,
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
        "chat_title": chat_title,
    }


def make_audio_document_message(message_id: int, *, title: str = "夜的第七章") -> dict[str, Any]:
    """MIME 为 audio/* 的 document：``is_audio_message`` 认的第二档（FR-SEARCH-02）。"""
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
    """视频消息：不是音频，任何链路都不该把它当歌（验收 #11）。"""
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
