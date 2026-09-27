"""转发给 bot 的音频走哪一侧会话（FR-LINK-04）。

那条消息**只存在于 bot 会话里**：bot 看到的 chat_id 是发信用户的 user_id，登录账号拿
同一个 id 去 get_messages 落到的是它自己的收藏夹，取回一条空消息（empty=True），
交给 Kurigram 的 download_media 只会得到「This message doesn't contain any
downloadable media」。所以入队时标 ``via_bot``，取数按该标记改走 bot 会话——
而不是把 file_id 交接过去：file_reference 按账号签发，跨账号会吃
FILE_REFERENCE_EXPIRED。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pyrogram.enums import ChatType
from pyrogram.types import Chat, Document, Message

from app.config import SecretConfig
from app.domain import PROVIDER_TELEGRAM
from app.errors import AppError
from app.ports.music import FetchRef
from app.telegram.bot_client import BotClient, forwarded_meta
from app.telegram.manager import TelegramManager
from app.telegram.music_source import TelegramSource
from app.telegram.user_client import UserClient

BOT_CHAT = 777
BOT_MESSAGE_ID = 42


def _forwarded_document() -> Message:
    """bot 私聊里收到的一条转发（document 音频：转发 tg 音乐文件就是这个形态）。"""
    return Message(
        id=BOT_MESSAGE_ID,
        chat=Chat(id=BOT_CHAT, type=ChatType.PRIVATE),
        document=Document(
            file_id="BAACAgQAAx0CeF4CAADB0X-yOwo3Ni8D0U_ndc0FC-L0sBAACAgQAAx0CeF4C",
            file_unique_id="AgAD1234",
            file_name="01 晴天.flac",
            mime_type="audio/flac",
            file_size=4096,
        ),
        date=datetime(2026, 9, 27, tzinfo=UTC),
    )


def _channel_audio(message_id: int) -> Message:
    """频道里的一条音频：登录账号看得见，get_messages 拿得到。"""
    return Message(
        id=message_id,
        chat=Chat(id=-100123, type=ChatType.CHANNEL),
        document=Document(
            file_id="BAACAgQAAx0CeF4C",
            file_unique_id="AgAD5678",
            file_name="02 七里香.mp3",
            mime_type="audio/mpeg",
            file_size=2048,
        ),
    )


def test_forwarded_meta_marks_the_bot_session() -> None:
    meta = forwarded_meta(_forwarded_document())
    assert (meta.chat_id, meta.message_id) == (BOT_CHAT, BOT_MESSAGE_ID)
    # 没有这个标记，取数端会拿登录账号去查一条它看不见的消息
    assert meta.via_bot is True
    # 大小与扩展名一并带上：落盘与完整性校验不必回查（登录账号那条消息查不到）
    assert (meta.file_size, meta.ext) == (4096, "flac")


class _RecordingMedia:
    def __init__(self) -> None:
        self.refs: list[dict[str, Any]] = []

    async def download_media(
        self,
        message_ref: dict[str, Any],
        file_name: str,
        progress: Callable[[int, int], None] | None = None,
    ) -> str:
        self.refs.append(message_ref)
        return file_name


async def test_telegram_source_carries_the_session_marker(tmp_path: Path) -> None:
    media = _RecordingMedia()
    source = TelegramSource.detached(None, media)  # type: ignore[arg-type] # 只用 fetch
    ref = FetchRef(
        provider=PROVIDER_TELEGRAM,
        scope_id=-999,
        chat_id=BOT_CHAT,
        message_id=BOT_MESSAGE_ID,
        via_bot=True,
    )

    await source.fetch(ref, tmp_path / "out.flac")

    assert media.refs[0]["via_bot"] is True


class _StubKurigram:
    """只记调用的 Kurigram 替身（不连网）。"""

    def __init__(self, messages: Any) -> None:
        self.messages = messages
        self.lookups: list[tuple[Any, list[int]]] = []
        self.downloaded: list[Any] = []

    async def get_messages(self, chat_id: Any, message_ids: list[int]) -> Any:
        self.lookups.append((chat_id, message_ids))
        return self.messages

    async def download_media(self, message: Any, file_name: str, progress: Any = None) -> str:
        self.downloaded.append(message)
        return file_name


def _user_client(tmp_path: Path, stub: _StubKurigram) -> UserClient:
    client = UserClient(SecretConfig(), tmp_path)
    client.client = stub  # type: ignore[assignment] # 换成只记调用的替身
    return client


def _bot_client(tmp_path: Path, stub: _StubKurigram) -> BotClient:
    client = BotClient(SecretConfig(), tmp_path, downloads=None)  # type: ignore[arg-type]
    client.client = stub  # type: ignore[assignment] # 换成只记调用的替身
    return client


def _proxy(tmp_path: Path, user: UserClient | None, bot: BotClient | None) -> Any:
    """按生产装配出一个已登录的 manager（客户端是替身），取它的下载代理。"""
    mgr = TelegramManager(SecretConfig(), tmp_path)
    mgr.user = user  # type: ignore[assignment]
    mgr.bot = bot  # type: ignore[assignment]
    mgr.authorized = user is not None
    return mgr.download_client_proxy


async def test_forwarded_task_downloads_through_the_bot_session(tmp_path: Path) -> None:
    # 登录账号这边按 (chat_id, message_id) 查只会得到空消息；bot 那边才拿得到附件。
    user_stub = _StubKurigram(Message(id=BOT_MESSAGE_ID, empty=True))
    bot_stub = _StubKurigram(_forwarded_document())
    proxy = _proxy(
        tmp_path, _user_client(tmp_path, user_stub), _bot_client(tmp_path, bot_stub)
    )

    await proxy.download_media(
        {"chat_id": BOT_CHAT, "message_id": BOT_MESSAGE_ID, "via_bot": True},
        str(tmp_path / "song.flac"),
    )

    assert bot_stub.lookups == [(BOT_CHAT, [BOT_MESSAGE_ID])]
    assert user_stub.lookups == [], "转发入队的消息不该让登录账号去查"
    assert user_stub.downloaded == []


async def test_channel_task_still_downloads_through_the_user_session(tmp_path: Path) -> None:
    user_stub = _StubKurigram(_channel_audio(1))
    bot_stub = _StubKurigram(_forwarded_document())
    proxy = _proxy(
        tmp_path, _user_client(tmp_path, user_stub), _bot_client(tmp_path, bot_stub)
    )

    await proxy.download_media(
        {"chat_id": -100123, "message_id": 1, "via_bot": False},
        str(tmp_path / "song.flac"),
    )

    assert user_stub.lookups == [(-100123, [1])]
    assert bot_stub.lookups == []


def test_forwarded_task_without_a_running_bot_says_so(tmp_path: Path) -> None:
    # bot 没起时必须点名 bot_token，而不是让任务以一句「取不到音频」收场
    proxy = _proxy(tmp_path, _user_client(tmp_path, _StubKurigram(None)), None)

    with pytest.raises(AppError) as err:
        proxy.download_media(
            {"chat_id": BOT_CHAT, "message_id": BOT_MESSAGE_ID, "via_bot": True},
            str(tmp_path / "song.flac"),
        )

    assert err.value.code == "bot_unavailable"
    assert "bot_token" in err.value.message



async def test_message_without_media_gives_a_readable_reason(tmp_path: Path) -> None:
    # Kurigram 把「这个 id 在该对话里不存在」的消息解析成 empty=True 的空消息：
    # 原样交给 download_media 只会抛一句看不懂的 ValueError。
    stub = _StubKurigram(Message(id=BOT_MESSAGE_ID, empty=True))
    client = _user_client(tmp_path, stub)

    with pytest.raises(AppError) as err:
        await client.download_media(
            {"chat_id": BOT_CHAT, "message_id": BOT_MESSAGE_ID}, str(tmp_path / "song.flac")
        )

    assert err.value.code == "no_media"
    assert stub.downloaded == []
