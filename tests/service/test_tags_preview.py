"""标签读写与预览缓存测试（FR-META-01/FR-PLAY-02/03，NFR-10）。"""

from __future__ import annotations

import os
import struct
import time
from pathlib import Path

import pytest
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3
from mutagen.mp4 import MP4, MP4Cover

import app.services.preview as preview_mod
from app.db.models import PreviewCache
from app.db.store import Store
from app.domain import TrackMeta
from app.errors import AppError, UnsupportedContainerError
from app.events import EventBus
from app.services.preview import PreviewService
from app.services.tags import TagService, image_media_type
from app.telegram.unconnected import UnconnectedTelegramClient
from tests.fakes import FakeUserClient, fake_registry, make_audio_message

JPEG = b"\xff\xd8\xff\xe0FAKEJPEGDATA"
PNG = b"\x89PNG\r\n\x1a\nFAKEPNGDATA"


def make_silent_mp3(tmp_path: Path, name: str = "song.mp3") -> Path:
    """构造最小可解析 MP3（mutagen 可写 ID3，``MutagenFile`` 能同步到 MPEG 帧）。

    帧头按 MPEG-1 Layer III 128kbps/44.1kHz 声明 417 字节（144×128000÷44100）；
    至少两帧 ``MutagenFile`` 才肯同步（read_cover 全靠它探测），单帧报 HeaderNotFoundError。
    """
    p = tmp_path / name
    frame = b"\xff\xfb\x90\x00" + b"\x00" * 413
    p.write_bytes(frame * 2)
    return p


def make_silent_flac(tmp_path: Path, name: str = "song.flac") -> Path:
    """构造最小合法 FLAC（fLaC magic + 34 字节 STREAMINFO，长度声明必须与实际一致）。"""
    p = tmp_path / name
    streaminfo = (
        struct.pack(">HH", 4096, 4096)  # min/max blocksize
        + b"\x00\x00\x00" * 2  # min/max framesize（未知）
        + ((44100 << 44) | (1 << 41) | (15 << 36)).to_bytes(8, "big")  # 44.1kHz/单声道/16bit
        + b"\x00" * 16  # MD5
    )
    p.write_bytes(b"fLaC" + bytes([0x80, 0, 0, 34]) + streaminfo)
    return p


def make_silent_m4a(tmp_path: Path, name: str = "song.m4a") -> Path:
    """手工构造最小 MP4（ftyp + moov[mvhd + udta[meta[hdlr + ilst]]]），mutagen 可加载。"""

    def box(name: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", 8 + len(payload)) + name + payload

    mvhd = box(
        b"mvhd",
        bytes(4)
        + struct.pack(">IIII", 0, 1000, 1000, 0x00010000)
        + struct.pack(">HH", 0x0100, 0)
        + struct.pack(">HH", 0, 0)
        + struct.pack(">9I", *([0] * 9))
        + struct.pack(">I", 2),
    )
    hdlr = box(b"hdlr", bytes(4) + bytes(4) + b"mdir" + b"appl" + bytes(9))
    meta = box(b"meta", bytes(4) + hdlr + box(b"ilst", b""))
    p = tmp_path / name
    p.write_bytes(
        box(b"ftyp", b"M4A " + struct.pack(">I", 0) + b"M4A mp42isom")
        + box(b"moov", mvhd + box(b"udta", meta))
    )
    return p


def make_cover_mp3(tmp_path: Path, name: str = "song.mp3", image: bytes = JPEG) -> Path:
    """带 APIC 封面的最小 MP3（下载页封面展示的事实源 fixture）。"""
    p = make_silent_mp3(tmp_path, name)
    tags = ID3()
    tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=image))
    tags.update_to_v24()
    tags.save(p)
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


def test_read_cover_id3(tmp_path: Path) -> None:
    # mp3：APIC 帧里的封面字节原样读出，魔数嗅探给 image/jpeg
    p = make_cover_mp3(tmp_path)
    data = TagService().read_cover(p)
    assert data == JPEG
    assert image_media_type(data) == "image/jpeg"


def test_read_cover_flac_picture(tmp_path: Path) -> None:
    # flac：picture 块（png 封面）
    p = make_silent_flac(tmp_path)
    flac = FLAC(str(p))
    pic = Picture()
    pic.data = PNG
    pic.mime = "image/png"
    pic.type = 3
    flac.add_picture(pic)
    flac.save()
    data = TagService().read_cover(p)
    assert data == PNG
    assert image_media_type(data) == "image/png"


def test_read_cover_mp4_covr(tmp_path: Path) -> None:
    # m4a：covr 原子
    p = make_silent_m4a(tmp_path)
    m = MP4(str(p))
    m["covr"] = [MP4Cover(JPEG, imageformat=MP4Cover.FORMAT_JPEG)]
    m.save()
    assert TagService().read_cover(p) == JPEG


def test_read_cover_absent_returns_none(tmp_path: Path) -> None:
    # 没写封面的音轨：None（调用方回退缩略图/占位），不是空字节
    p = make_silent_mp3(tmp_path)
    assert TagService().read_cover(p) is None


def test_read_cover_broken_file_returns_none(tmp_path: Path) -> None:
    # 损坏/非音频文件：None 不抛错（封面是装饰，不允许它打断任何流程）
    p = tmp_path / "broken.mp3"
    p.write_bytes(b"not an audio file at all")
    assert TagService().read_cover(p) is None


def test_image_media_type_unknown_bytes() -> None:
    # 认不出的字节：None，由调用方兜底 media_type
    assert image_media_type(b"\x00\x00\x00\x18ftyp") is None


async def test_preview_cache_roundtrip(tmp_path: Path) -> None:
    # FR-PLAY-02：试听写 preview 缓存、不写 save_path
    store = Store(tmp_path / "app.db")
    events = EventBus()
    content = b"x" * 100
    client = FakeUserClient([make_audio_message(1)], content=content)
    preview = PreviewService(
        store, fake_registry(store, client), events, tmp_path / "temp" / "preview"
    )
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
    preview = PreviewService(
        store, fake_registry(store, client), events, tmp_path / "temp" / "preview", max_bytes=50
    )
    await preview.request_preview(-100123, 1, file_size=100)
    total, cnt = store.preview_totals()
    assert cnt <= 1  # 超限淘汰后 ≤1 条


async def test_cache_file_is_not_a_stale_temp(tmp_path: Path) -> None:
    # 缓存文件不能落成 .part/.tmp：那类名字是「崩溃残留」，修剪（启动/下载后/写完封面）会删掉，
    # 而表里仍留着记录 → 每次试听都重下一遍。顺带确认真残留会被清。
    store = Store(tmp_path / "app.db")
    content = b"x" * 100
    client = FakeUserClient([make_audio_message(1)], content=content)
    preview_dir = tmp_path / "temp" / "preview"
    preview = PreviewService(store, fake_registry(store, client), EventBus(), preview_dir)
    await preview.request_preview(-100123, 1, file_size=100)
    hit = store.get_preview(-100123, 1)
    assert hit is not None
    cached = Path(hit.file_path)

    stale = preview_dir / "preview_9_9.part"
    stale.write_bytes(b"half")
    # 旧版本把试听落成 .tmp：这类文件仍在表里记着，就不能当残留清掉（命名迁移期的自保）
    legacy = preview_dir / "preview_8_8.tmp"
    legacy.write_bytes(b"legacy")
    store.put_preview(
        PreviewCache(
            id=None,
            chat_id=8,
            message_id=8,
            file_path=str(legacy),
            file_size=6,
            last_access_at="",
        )
    )
    old = time.time() - 2 * preview_mod.STALE_TMP_SEC
    for path in (cached, stale, legacy):
        os.utime(path, (old, old))  # 都装成两小时前

    await preview.enforce_limits()

    assert cached.exists()  # noqa: ASYNC240  测试里的存在性检查，非热路径
    assert not stale.exists()  # noqa: ASYNC240  同上
    assert legacy.exists()  # noqa: ASYNC240  表里记着 → 留着
    assert await preview.request_preview(-100123, 1, file_size=100) == hit.id


async def test_cache_stats_report_disk_usage(tmp_path: Path) -> None:
    # FR-PLAY-02：占用 = 试听 + 封面的磁盘实际字节（不取表里记录的 file_size）
    store = Store(tmp_path / "app.db")
    content = b"x" * 100
    client = FakeUserClient([make_audio_message(1)], content=content)
    preview_dir = tmp_path / "temp" / "preview"
    preview = PreviewService(store, fake_registry(store, client), EventBus(), preview_dir)
    empty = await preview.cache_stats()
    assert empty == preview_mod.CacheStats(0, empty.max_bytes, 0, 0, 0, 0)

    await preview.request_preview(-100123, 1, file_size=100)
    (preview_dir / "cover_deadbeef.jpg").write_bytes(b"\xff\xd8jpeg")  # 6 字节
    preview.max_bytes = 1000

    stats = await preview.cache_stats()
    assert (stats.preview_bytes, stats.preview_count) == (100, 1)
    assert (stats.cover_bytes, stats.cover_count) == (6, 1)
    assert stats.total_bytes == 106
    assert stats.max_bytes == 1000


async def test_cover_cache_evicts_oldest_within_shared_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # FR-PLAY-02：封面不进 preview_cache 表，但算占用：超上限按 mtime 从最旧淘汰
    monkeypatch.setattr(preview_mod, "_fetch_cover", lambda title, artist: b"\xff\xd8jpeg")
    preview = PreviewService(
        Store(tmp_path / "app.db"), UnconnectedTelegramClient(), EventBus(), tmp_path, max_bytes=15
    )
    now = time.time()
    first = await preview.cover_path("A", "")
    assert first is not None
    os.utime(first, (now - 300, now - 300))
    second = await preview.cover_path("B", "")
    assert second is not None
    os.utime(second, (now - 200, now - 200))
    third = await preview.cover_path("C", "")  # 18 字节 > 15：写完即淘汰最旧的
    assert third is not None

    assert not first.exists()
    assert second.exists() and third.exists()
    stats = await preview.cache_stats()
    assert (stats.cover_count, stats.total_bytes) == (2, 12)


async def test_clear_cache_removes_files_and_records(tmp_path: Path) -> None:
    # FR-PLAY-02：清理缓存 = 删文件 + 清 preview_cache（试听能重下）；曲库与历史不在这条路径上
    store = Store(tmp_path / "app.db")
    content = b"x" * 100
    client = FakeUserClient([make_audio_message(1)], content=content)
    preview_dir = tmp_path / "temp" / "preview"
    preview = PreviewService(store, fake_registry(store, client), EventBus(), preview_dir)
    await preview.request_preview(-100123, 1, file_size=100)
    (preview_dir / "cover_deadbeef.jpg").write_bytes(b"\xff\xd8jpeg")

    stats = await preview.clear_cache()

    assert stats.total_bytes == 0 and stats.preview_count == 0 and stats.cover_count == 0
    assert list(preview_dir.iterdir()) == []
    assert store.preview_totals() == (0, 0)
    # 记录也清了：下一次试听是重新下载，不是返回已删文件的 id
    await preview.request_preview(-100123, 1, file_size=100)
    assert client.download_calls == 2


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
    preview = PreviewService(
        store, UnconnectedTelegramClient(), EventBus(), tmp_path / "temp" / "preview"
    )
    assert preview.stream_path(1) == file
    with pytest.raises(AppError):
        preview.stream_path(999)


async def test_cover_path_caches_query_and_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 封面（api.lrc.cx）：抓取结果按查询串 md5 落盘缓存，同一首歌只外呼一次
    # （扩展名/空白归一后视为同一查询）；查不到的查询负缓存期内不再外呼。
    calls: list[tuple[str, str]] = []

    def fake_fetch(title: str, artist: str) -> bytes | None:
        calls.append((title, artist))
        return b"\xff\xd8jpeg" if title == "晴天" else None

    monkeypatch.setattr(preview_mod, "_fetch_cover", fake_fetch)
    preview = PreviewService(
        Store(tmp_path / "app.db"), UnconnectedTelegramClient(), EventBus(), tmp_path
    )

    first = await preview.cover_path("晴天.flac", "周杰伦")
    assert first is not None and first.read_bytes() == b"\xff\xd8jpeg"
    assert first.parent == tmp_path and first.name.startswith("cover_")
    again = await preview.cover_path("晴天", " 周杰伦 ")  # 归一后同一查询 → 缓存命中
    assert again == first
    assert calls == [("晴天", "周杰伦")]

    assert await preview.cover_path("未知歌", "未知歌手") is None
    assert await preview.cover_path("未知歌", "未知歌手") is None  # 负缓存内不再外呼
    assert calls == [("晴天", "周杰伦"), ("未知歌", "未知歌手")]

    assert await preview.cover_path(None, None) is None  # 无可查字段：不外呼
    assert calls == [("晴天", "周杰伦"), ("未知歌", "未知歌手")]
