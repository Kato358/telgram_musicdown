"""标签读写与预览缓存测试（FR-META-01/FR-PLAY-02/03，NFR-10）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.db.models import PreviewCache
from app.db.store import Store
from app.domain import TrackMeta
from app.errors import AppError, UnsupportedContainerError
from app.events import EventBus
from app.services.preview import PreviewService
from app.services.tags import TagService
from tests.fakes import FakeUserClient, make_audio_message


def make_silent_mp3(tmp_path: Path, name: str = "song.mp3") -> Path:
    """构造最小可解析 MP3（mutagen 可写 ID3）。"""
    p = tmp_path / name
    # 最小 MPEG 帧 header (111 bytes 静音帧) + padding
    frame = b"\xff\xfb\x90\x44" + b"\x00" * 107
    p.write_bytes(frame)
    return p


def make_silent_flac(tmp_path: Path, name: str = "song.flac") -> Path:
    """构造最小合法 FLAC（mutagen 可写 Vorbis Comment）。"""
    p = tmp_path / name
    # fLaC magic + STREAMINFO
    body = (
        b"fLaC"
        + b"\x00\x00\x00\x22"
        + b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x10"
        + b"\x00\x00\x00\x10\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    )
    p.write_bytes(body)
    return p


def test_write_and_read_id3(tmp_path: Path) -> None:
    # FR-META-01：mp3 写 ID3 后读回
    p = make_silent_mp3(tmp_path)
    tags = TagService()
    meta = TrackMeta(
        chat_id=-100, message_id=1, title="晴天", artist="周杰伦", album="叶惠美", track=3
    )
    tags.write_tags(p, meta)
    got = tags.read_tags(p)
    assert got["title"] == "晴天"
    assert got["artist"] == "周杰伦"
    assert got["album"] == "叶惠美"


def test_tag_write_failure_does_not_corrupt(tmp_path: Path) -> None:
    # NFR-10：标签写失败音轨字节不变（不支持容器）
    p = tmp_path / "song.cue"
    p.write_bytes(b"cue sheet content")
    tags = TagService()
    with pytest.raises(UnsupportedContainerError):
        tags.write_tags(p, TrackMeta(chat_id=-100, message_id=1, title="x"))
    assert p.read_bytes() == b"cue sheet content"


async def test_preview_cache_roundtrip(tmp_path: Path) -> None:
    # FR-PLAY-02：试听写 preview 缓存、不写 save_path
    store = Store(tmp_path / "app.db")
    events = EventBus()
    content = b"x" * 100
    client = FakeUserClient([make_audio_message(1)], content=content)
    preview = PreviewService(store, client, events, tmp_path / "temp" / "preview")
    pid = await preview.request_preview(-100123, 1, file_size=100)
    assert pid is not None
    hit = store.get_preview(-100123, 1)
    assert hit is not None
    # 缓存命中第二次直接返回
    pid2 = await preview.request_preview(-100123, 1, file_size=100)
    assert pid2 == pid
    assert client.download_calls == 1


async def test_preview_lru_eviction(tmp_path: Path) -> None:
    # FR-PLAY-02：LRU 淘汰（总量超 max_bytes）
    store = Store(tmp_path / "app.db")
    events = EventBus()
    content = b"x" * 100
    client = FakeUserClient([make_audio_message(1), make_audio_message(2)], content=content)
    preview = PreviewService(store, client, events, tmp_path / "temp" / "preview", max_bytes=50)
    await preview.request_preview(-100123, 1, file_size=100)
    total, cnt = store.preview_totals()
    assert cnt <= 1  # 超限淘汰后 ≤1 条


def test_preview_stream_path_only_accepts_id(tmp_path: Path) -> None:
    # NFR-02：流接口只接受 id（路径服务端拼装），不接受 ?path=
    store = Store(tmp_path / "app.db")
    file = tmp_path / "temp" / "preview" / "p.mp3"
    file.parent.mkdir(parents=True)
    file.write_bytes(b"data")
    store.put_preview(
        PreviewCache(
            id=None, chat_id=1, message_id=1, file_path=str(file), file_size=4, last_access_at=""
        )
    )
    preview = PreviewService(store, None, EventBus(), tmp_path / "temp" / "preview")  # type: ignore[arg-type]
    assert preview.stream_path(1) == file
    with pytest.raises(AppError):
        preview.stream_path(999)
