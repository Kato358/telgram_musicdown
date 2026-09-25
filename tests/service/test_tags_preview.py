"""标签读写与预览缓存测试（FR-META-01/FR-PLAY-02/03，NFR-10）。"""

from __future__ import annotations

import struct
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
from tests.fakes import FakeUserClient, make_audio_message

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
    preview = PreviewService(None, None, EventBus(), tmp_path)  # type: ignore[arg-type]

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
