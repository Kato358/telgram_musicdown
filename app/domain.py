"""核心域类型与纯规则：TrackMeta、TemplateConfig、音频判定、源级过滤、候选源标签。

path_builder 是纯函数层，本模块为其提供唯一数据契约（SDD §2.2）。
两个以上服务（搜索、同步、下载、路由）共用的纯规则收在这里，避免 services 之间互相
import（编码规范 §1.2 依赖方向）。本模块不 import pyrogram/services/web，可单测。
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path, PurePath
from typing import Any


@dataclass(slots=True)
class TrackMeta:
    """待渲染音轨的元数据快照，字段缺省为 None 表示「无值」走回退链。"""

    chat_id: int
    message_id: int
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    track: int | None = None
    duration_sec: int | None = None
    file_size: int | None = None
    bitrate: int | None = None
    file_name: str | None = None
    mime: str | None = None
    caption: str | None = None
    message_date: str | None = None  # ISO-8601 UTC 文本（store 约定 §2.6）
    channel_title: str | None = None
    ext: str | None = None
    unique_id: str | None = None


@dataclass(slots=True)
class TemplateConfig:
    """模板配置：目录模板、文件名模板与日期格式（FR-NAME-01/06）。"""

    dir_template: str = "{artist}/{album}"
    file_template: str = "{track:02d} {title}"
    date_format: str = "%Y-%m"
    caption_artist_re: str = r"^\s*(?P<artist>.+?)\s*[-\u2013\u2014]\s*(?P<title>.+?)\s*$"
    save_path: PurePath = field(default_factory=lambda: Path("./downloads"))


# ---- 音频消息判定与卡片映射（FR-SEARCH-02，验收 #11）----


def is_audio_message(msg: dict[str, Any]) -> bool:
    """音频判定（SDD §2.1）：``msg.audio`` 或 MIME 为 ``audio/*`` 的 document。

    ``voice`` 一律排除（SRS §14 锁定决策 #3）。
    """
    if msg.get("voice"):
        return False
    if msg.get("audio"):
        return True
    doc = msg.get("document")
    return bool(doc and str(doc.get("mime_type", "")).startswith("audio/"))


def scope_allows(media_scope: list[str], msg: dict[str, Any]) -> bool:
    """源级媒体范围（FR-SRC-02）：``audio`` 指 ``Message.audio``，
    ``audio_document`` 指 MIME 为 ``audio/*`` 的 document；voice 两者都不含。"""
    if msg.get("voice"):
        return False
    if msg.get("audio"):
        return "audio" in media_scope
    doc = msg.get("document")
    if doc and str(doc.get("mime_type", "")).startswith("audio/"):
        return "audio_document" in media_scope
    return False


@dataclass(slots=True)
class SearchResultCard:
    """音频消息的卡片事实（FR-SEARCH-02）：搜索、同步、下载共用同一份字段。"""

    chat_id: int
    message_id: int
    title: str | None
    artist: str | None
    duration_sec: int | None
    file_size: int | None
    ext: str | None
    mime: str | None
    channel_title: str | None
    message_date: str | None
    caption: str | None
    file_unique_id: str | None = None


def message_to_card(msg: dict[str, Any], channel_title: str | None = None) -> SearchResultCard:
    """telegram 层消息 dict → 卡片（字段缺失留 None，不编造）。"""
    audio = msg.get("audio") or {}
    doc = msg.get("document") or {}
    file_name = audio.get("file_name") or doc.get("file_name")
    ext = file_name.rsplit(".", 1)[-1] if file_name and "." in file_name else None
    return SearchResultCard(
        chat_id=msg["chat_id"],
        message_id=msg["message_id"],
        title=audio.get("title") or file_name,
        artist=audio.get("performer"),
        duration_sec=audio.get("duration") or doc.get("duration"),
        file_size=audio.get("file_size") or doc.get("file_size"),
        ext=ext,
        mime=audio.get("mime_type") or doc.get("mime_type"),
        channel_title=channel_title,
        message_date=msg.get("message_date"),
        caption=msg.get("caption"),
        file_unique_id=audio.get("file_unique_id") or doc.get("file_unique_id"),
    )


def card_to_dict(card: SearchResultCard) -> dict[str, Any]:
    return asdict(card)


_TRACK_META_FIELDS = frozenset(f.name for f in fields(TrackMeta))


def meta_from_dict(raw: dict[str, Any]) -> TrackMeta:
    """dict → TrackMeta（未知键忽略）：任务 payload 与请求体的统一反序列化入口。"""
    return TrackMeta(**{k: v for k, v in raw.items() if k in _TRACK_META_FIELDS})


def card_to_meta(card: SearchResultCard) -> TrackMeta:
    """卡片 → 下载/渲染元数据（TrackMeta 是入队与落盘的唯一契约，SDD §2.2）。"""
    return TrackMeta(
        chat_id=card.chat_id,
        message_id=card.message_id,
        title=card.title,
        artist=card.artist,
        duration_sec=card.duration_sec,
        file_size=card.file_size,
        mime=card.mime,
        ext=card.ext,
        caption=card.caption,
        message_date=card.message_date,
        channel_title=card.channel_title,
        unique_id=card.file_unique_id,
    )


# ---- 源级过滤（FR-SRC-02）----


class SourceFilters:
    """源级过滤：``sources.filters_json`` 的结构化形式，同步与回溯按它筛音频。"""

    @staticmethod
    def parse(filters_json: str | None) -> dict[str, Any]:
        if not filters_json:
            return {}
        try:
            data = json.loads(filters_json)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def _out_of_duration(f: dict[str, Any], dur: int | None) -> bool:
        min_d, max_d = f.get("min_duration"), f.get("max_duration")
        if dur is None:
            return min_d is not None or max_d is not None
        return (min_d is not None and dur < min_d) or (max_d is not None and dur > max_d)

    @staticmethod
    def _caption_blocked(f: dict[str, Any], caption: str | None) -> bool:
        cap = caption or ""
        contains = f.get("caption_contains") or []
        excludes = f.get("caption_exclude") or []
        has_all = all(k in cap for k in contains) if contains else True
        has_none = not any(k in cap for k in excludes)
        return not (has_all and has_none)

    @staticmethod
    def matches(filters_json: str | None, card: SearchResultCard) -> bool:
        f = SourceFilters.parse(filters_json)
        if not f:
            return True
        if SourceFilters._out_of_duration(f, card.duration_sec):
            return False
        if (exts := f.get("ext_whitelist")) and card.ext not in exts:
            return False
        return not SourceFilters._caption_blocked(f, card.caption)


# ---- 候选源标签（FR-SRC-05）----

MUSIC_KEYWORDS: tuple[str, ...] = (
    "音乐",
    "歌单",
    "无损",
    "专辑",
    "古典",
    "推荐",
    "影视原声",
    "flac",
    "mp3",
    "m4a",
    "wav",
    "ape",
    "hi-res",
    "hires",
    "jazz",
    "classical",
    "lofi",
    "lo-fi",
    "remix",
    "ost",
    "music",
    "audio",
    "song",
    "track",
    "album",
    "playlist",
)


def _keyword_hit(keyword: str, haystack: str) -> bool:
    """ASCII 关键词按词边界匹配（避免 ``ost`` 命中 ``host``），CJK 直接子串匹配。"""
    if keyword.isascii():
        return re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", haystack) is not None
    return keyword in haystack


def source_tags(title: str, username: str | None = None) -> list[str]:
    """候选源标签（FR-SRC-05）：标题/用户名命中的音乐关键词，按关键词表顺序去重返回。

    只看这两个字段本身，不发额外 API 请求，故不会因扫描引入 FloodWait。
    """
    haystack = f"{title} {username or ''}".lower()
    return [kw for kw in MUSIC_KEYWORDS if _keyword_hit(kw, haystack)]
