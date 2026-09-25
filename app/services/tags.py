"""标签读写（FR-META-01/FR-TAG-01~03，SDD §2.5）。

mutagen 是同步库，标签读写必须 to_thread（编码规范 §2.3）。
容器：ID3（mp3，ID3v2.4）/ VorbisComment（flac/ogg）/ MP4（m4a）。
失败抛 TagWriteError；只读容器（cue/ape/wma）抛 UnsupportedContainerError。
"""

from __future__ import annotations

import base64
import logging
import math
from pathlib import Path
from typing import Any

from mutagen import File as MutagenFile
from mutagen.flac import FLAC, Picture
from mutagen.id3 import ID3, ID3NoHeaderError
from mutagen.mp4 import MP4
from mutagen.oggvorbis import OggVorbis

from app.domain import TrackMeta
from app.errors import TagWriteError, UnsupportedContainerError

logger = logging.getLogger(__name__)

READONLY_EXTS = {".cue", ".ape", ".wma"}


def image_media_type(data: bytes) -> str | None:
    """内嵌封面字节 → MIME（魔数嗅探）；认不出返回 None，由调用方兜底。"""
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"GIF8"):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _read_cover_from_tags(tags: Any) -> bytes | None:
    """按标签容器取封面字节：ID3 APIC 帧 / MP4 covr 原子 / Ogg base64 picture 块。"""
    if hasattr(tags, "getall"):  # ID3（mp3，ID3v2）：APIC 帧
        frames = tags.getall("APIC")
        return bytes(frames[0].data) or None
    if not hasattr(tags, "get"):
        return None
    covr = tags.get("covr")  # MP4（m4a）：covr 原子
    if covr:
        return bytes(covr[0]) or None
    block = tags.get("metadata_block_picture")  # Ogg：VorbisComment 里的 base64 块
    if not block:
        return None
    try:
        return bytes(Picture(base64.b64decode(block[0])).data) or None
    except Exception:  # noqa: BLE001  坏的 picture 块按「无封面」处理
        return None


def _container(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in READONLY_EXTS:
        raise UnsupportedContainerError(f"container {ext} is read-only/unsupported")
    if ext == ".mp3":
        return "id3"
    if ext in {".flac", ".ogg", ".opus"}:
        return "vorbis"
    if ext in {".m4a", ".mp4", ".aac"}:
        return "mp4"
    raise UnsupportedContainerError(f"unsupported container: {ext}")


class TagService:
    """音频标签读写；失败不损坏音轨（NFR-10：写前副本由调用方负责）。"""

    def read_tags(self, path: Path) -> dict[str, str | None]:
        """读标签（FR-TAG-01）。字段缺省为 None。"""
        container = _container(path)
        if container == "id3":
            return self._read_id3(path)
        if container == "vorbis":
            return self._read_vorbis(path)
        return self._read_mp4(path)

    def media_facts(self, path: Path) -> dict[str, int | None]:
        """落盘实测（下载页「时长 / 大小 / 码率」列的事实源）：读不出为 None，不编造。

        用 ``mutagen.File`` 全容器探测而不是 read_tags 的三套容器：ape/wma 这类
        只读容器也能读出流信息；cue 等认不出的格式至少还给得到文件字节。
        """
        facts: dict[str, int | None] = {
            "duration_sec": None,
            "file_size": None,
            "bitrate": None,
        }
        try:
            facts["file_size"] = path.stat().st_size
        except OSError:
            return facts
        try:
            audio = MutagenFile(str(path))
        except Exception:  # noqa: BLE001  损坏/未识别容器按「无流信息」处理
            return facts
        if audio is None or audio.info is None:
            return facts
        length = getattr(audio.info, "length", None)
        if isinstance(length, (int, float)) and math.isfinite(length) and length > 0:
            facts["duration_sec"] = round(length)
        bitrate = getattr(audio.info, "bitrate", None)
        if isinstance(bitrate, (int, float)) and math.isfinite(bitrate) and bitrate > 0:
            facts["bitrate"] = round(bitrate / 1000)  # bps → kbps
        return facts

    def read_cover(self, path: Path) -> bytes | None:
        """读内嵌封面（下载页封面展示，从音频标签获取）：APIC（mp3）/ pictures（flac）/
        covr（m4a）/ METADATA_BLOCK_PICTURE（ogg）。无封面与损坏文件一律返回 None，
        不抛错——封面是装饰，调用方拿 None 自己回退（Telegram 缩略图 / 占位图）。

        与 media_facts 同用 ``mutagen.File`` 全容器探测：read_tags 不认的只读容器
        （ape/wma）也能把封面读出来。
        """
        try:
            audio = MutagenFile(str(path))
        except Exception:  # noqa: BLE001  损坏/未识别容器按「无封面」处理
            return None
        if audio is None:
            return None
        pictures = getattr(audio, "pictures", None)  # FLAC：专用 picture 块（在 tags 检查之前）
        if pictures:
            return bytes(pictures[0].data) or None
        if audio.tags is None:
            return None
        return _read_cover_from_tags(audio.tags)

    def write_tags(self, path: Path, meta: TrackMeta) -> None:
        """写标签（FR-META-01/02）：title、artist、album、track、year。"""
        container = _container(path)
        try:
            if container == "id3":
                self._write_id3(path, meta)
            elif container == "vorbis":
                self._write_vorbis(path, meta)
            else:
                self._write_mp4(path, meta)
        except (TagWriteError, UnsupportedContainerError):
            raise
        except Exception as e:
            raise TagWriteError(f"tag write failed: {e}") from e

    # ---- ID3 (mp3, ID3v2.4) ----

    def _load_id3(self, path: Path) -> ID3:
        try:
            return ID3(path)
        except ID3NoHeaderError:
            return ID3()

    def _read_id3(self, path: Path) -> dict[str, str | None]:
        tags = self._load_id3(path)

        def get(frame: str) -> str | None:
            return str(tags[frame].text[0]) if frame in tags else None

        return {
            "title": get("TIT2"),
            "artist": get("TPE1"),
            "album": get("TALB"),
            "track": get("TRCK"),
            "year": get("TDRC"),
        }

    def _write_id3(self, path: Path, meta: TrackMeta) -> None:
        tags = self._load_id3(path)
        from mutagen.id3 import (  # noqa: PLC0415  按容器惰性导入
            TALB,
            TDRC,
            TIT2,
            TPE1,
            TRCK,
        )

        if meta.title is not None:
            tags.add(TIT2(encoding=3, text=meta.title))
        if meta.artist is not None:
            tags.add(TPE1(encoding=3, text=meta.artist))
        if meta.album is not None:
            tags.add(TALB(encoding=3, text=meta.album))
        if meta.track is not None:
            tags.add(TRCK(encoding=3, text=f"{meta.track}"))
        if meta.message_date:
            tags.add(TDRC(encoding=3, text=meta.message_date[:4]))
        tags.update_to_v24()
        tags.save(path)

    # ---- Vorbis (flac/ogg) ----

    def _read_vorbis(self, path: Path) -> dict[str, str | None]:
        f = FLAC(path) if path.suffix.lower() == ".flac" else OggVorbis(path)
        raw = f.tags
        # VCFLACDict 运行时为 dict 子类；mutagen stub 缺失，收窄经 dict 别名
        c: dict[str, list[str]] = dict(raw) if raw else {}  # type: ignore[arg-type]

        def get(key: str) -> str | None:
            vals = c.get(key)
            return vals[0] if vals else None

        return {
            "title": get("title"),
            "artist": get("artist"),
            "album": get("album"),
            "track": get("tracknumber"),
            "year": get("date"),
        }

    def _write_vorbis(self, path: Path, meta: TrackMeta) -> None:
        f = FLAC(path) if path.suffix.lower() == ".flac" else OggVorbis(path)
        if f.tags is None:
            f.add_tags()
        c = f.tags
        if c is None:  # pragma: no cover - add_tags 后必有
            raise TagWriteError("vorbis tags unavailable after add_tags")
        vc: dict[str, list[str]] = c  # type: ignore[assignment]  # VCFLACDict 为 dict 子类

        def set1(key: str, v: str) -> None:
            vc[key] = [v]

        if meta.title is not None:
            set1("title", meta.title)
        if meta.artist is not None:
            set1("artist", meta.artist)
        if meta.album is not None:
            set1("album", meta.album)
        if meta.track is not None:
            set1("tracknumber", str(meta.track))
        if meta.message_date:
            set1("date", meta.message_date[:4])
        f.save()

    # ---- MP4 (m4a) ----

    def _read_mp4(self, path: Path) -> dict[str, str | None]:
        f = MP4(path)

        def get(key: str) -> str | None:
            if not f.tags:
                return None
            val = f.tags.get(key, [None])
            return str(val[0]) if val else None

        return {
            "title": get("\xa9nam"),
            "artist": get("\xa9ART"),
            "album": get("\xa9alb"),
            "track": get("trkn"),
            "year": get("\xa9day"),
        }

    def _write_mp4(self, path: Path, meta: TrackMeta) -> None:
        f = MP4(path)
        if meta.title is not None:
            f["\xa9nam"] = meta.title
        if meta.artist is not None:
            f["\xa9ART"] = meta.artist
        if meta.album is not None:
            f["\xa9alb"] = meta.album
        if meta.track is not None:
            f["trkn"] = [(meta.track, 0)]
        if meta.message_date:
            f["\xa9day"] = meta.message_date[:4]
        f.save()
