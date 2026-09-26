"""核心域类型与纯规则：TrackMeta、TemplateConfig、音频判定、源级过滤、候选源标签。

path_builder 是纯函数层，本模块为其提供唯一数据契约（SDD §2.2）。
两个以上服务（搜索、同步、下载、路由）共用的纯规则收在这里，避免 services 之间互相
import（编码规范 §1.2 依赖方向）。本模块不 import Kurigram（导入名 pyrogram）/services/web，
可单测。
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


@dataclass(slots=True)
class DownloadRequest:
    """单条下载请求（tasks.payload_json 的结构化形式）。

    放在 domain 而非 services.download：它是跨服务的**数据契约**，
    sync 与 bot 都要构造它。若留在 download.py，sync 就得 import 整个下载服务
    模块（违反「services 之间不互相 import」）；这里谁都不依赖。
    """

    meta: TrackMeta
    source_id: int | None = None
    force: bool = False


# ---- 音频扩展名（唯一事实源）----
# 此前 tags/local_library/preview 各写一份且互不一致（.wav/.wma/.ape 只在曲库、
# .mp4 只在标签容器映射），导致「扫描得到的曲库文件」与「能写标签的文件」判据漂移。
# 这里按「本应用认得的音频扩展名」统一，各模块只做自己所需的子集筛选。

AUDIO_EXTS: frozenset[str] = frozenset(
    {
        ".mp3",
        ".flac",
        ".m4a",
        ".mp4",
        ".aac",
        ".ogg",
        ".opus",
        ".wav",
        ".wma",
        ".ape",
        ".aiff",
        ".aif",
        ".alac",
    }
)

# 曲库扫描关注的音频扩展名（与 AUDIO_EXTS 同源，含全部可播放容器）
LIBRARY_AUDIO_EXTS: frozenset[str] = AUDIO_EXTS


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
    bitrate: int | None = None


_DURATION_RE = re.compile(r"Duration:\s*(\d{1,2}):(\d{2})(?::(\d{2}))?", re.IGNORECASE)

_DURATION_PARTS = 3  # 正则三段：H:MM:SS；两段时 MM:SS（小时补 0）


def _duration_from_caption(caption: str | None) -> int | None:
    """消息说明里的「Duration: MM:SS / HH:MM:SS」→ 秒；认不出返回 None（不编造）。

    document 音频（Kurigram 的 Document 没有时长字段）的兜底：频道发歌时说明里
    常带 Duration，这是唯一拿得到时长的来源。
    """
    if not caption:
        return None
    match = _DURATION_RE.search(caption)
    if match is None:
        return None
    parts = [int(p) for p in match.groups() if p is not None]
    if len(parts) == _DURATION_PARTS:
        h, m, s = parts
    else:
        h, m, s = 0, parts[0], parts[1]
    return h * 3600 + m * 60 + s


def message_to_card(msg: dict[str, Any], channel_title: str | None = None) -> SearchResultCard:
    """telegram 层消息 dict → 卡片（字段缺失留 None，不编造）。

    时长在媒体元数据拿不到时（document 音频）从说明文案的「Duration:」兜底解析。
    """
    audio = msg.get("audio") or {}
    doc = msg.get("document") or {}
    file_name = audio.get("file_name") or doc.get("file_name")
    ext = file_name.rsplit(".", 1)[-1] if file_name and "." in file_name else None
    caption = msg.get("caption")
    duration = audio.get("duration") or doc.get("duration") or _duration_from_caption(caption)
    return SearchResultCard(
        chat_id=msg["chat_id"],
        message_id=msg["message_id"],
        title=audio.get("title") or file_name,
        artist=audio.get("performer"),
        duration_sec=duration,
        file_size=audio.get("file_size") or doc.get("file_size"),
        ext=ext,
        mime=audio.get("mime_type") or doc.get("mime_type"),
        channel_title=channel_title,
        message_date=msg.get("message_date"),
        caption=caption,
        file_unique_id=audio.get("file_unique_id") or doc.get("file_unique_id"),
        bitrate=audio.get("bitrate"),
    )


def card_to_dict(card: SearchResultCard) -> dict[str, Any]:
    return asdict(card)


_CARD_FIELDS = frozenset(f.name for f in fields(SearchResultCard))


def card_from_dict(raw: dict[str, Any]) -> SearchResultCard:
    """dict → 卡片（未知键忽略）：搜索缓存 L2 反序列化的唯一入口。

    与 ``meta_from_dict`` 同风格：缓存里躺的是旧版本写的 payload 时，
    多出来的键不该让整条缓存读崩——按当前字段表过滤即可。
    """
    return SearchResultCard(**{k: v for k, v in raw.items() if k in _CARD_FIELDS})


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
        bitrate=card.bitrate,
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


# ---- 搜索筛选与排序（FR-SEARCH-03）----
# 纯规则（不含 Telegram 依赖），故收在 domain：搜索路由与服务共用，且可直接单测。


def normalize_keyword(keyword: str) -> str:
    """关键词规范化：压平空白 + casefold。

    Telegram 的对话内搜索本就不区分大小写，故 ``Jay`` 与 ``jay`` 是同一个查询；
    空白压平让 ``"周杰伦  晴天"`` 与 ``"周杰伦 晴天"`` 也不重复取数（缓存键也用它）。
    """
    return " ".join(keyword.split()).casefold()


def keyword_tokens(keyword: str) -> list[str]:
    """规范化后的词元：多词关键词按「每个词都要有落点」计分与过滤。"""
    return normalize_keyword(keyword).split()


# 卡片上可用于本地匹配的字段（SRS FR-SEARCH-01「本地再按勾选字段过滤」）。
# album 卡片上没有，file_name 已由 message_to_card 回退进 title，故两者都映射到既有字段：
# 认不出的字段名直接忽略——宁可不按它过滤，也不能因为字段名对不上就把结果全筛没。
_SEARCH_FIELD_TEXT: dict[str, Any] = {
    "title": lambda c: c.title,
    "file_name": lambda c: c.title,
    "artist": lambda c: c.artist,
    "caption": lambda c: c.caption,
}

# 相关度：标题 > 表演者 > 说明（说明里常是频道推广文案，权重最低）
_RELEVANCE_FIELDS: tuple[tuple[str, int], ...] = (("title", 4), ("artist", 3), ("caption", 1))

SEARCH_SORTS: tuple[str, ...] = ("relevance", "date", "duration", "size")


def relevance_score(card: SearchResultCard, tokens: list[str]) -> tuple[int, int]:
    """相关度 = ``(命中词数, 命中质量总和)``（pansou 智能排序里「关键词得分」一维的对应）。

    先比覆盖面：两个词都命中的排在只命中一个的前面；再比命中质量：
    整字段相等 > 前缀 > 子串，且标题 > 表演者 > 说明（说明常是频道推广文案）。

    用元组而不是加权求和：加权需要一个凭空设定的量纲（pansou 那里是插件等级的 1000 分），
    本项目没有数据源分级，比较口径越直白越好。
    """
    covered = 0
    total = 0
    for token in tokens:
        best = 0
        for name, weight in _RELEVANCE_FIELDS:
            value = normalize_keyword(getattr(card, name) or "")
            if not value:
                continue
            if value == token:
                hit = 3
            elif value.startswith(token):
                hit = 2
            elif token in value:
                hit = 1
            else:
                continue
            best = max(best, hit * weight)
        if best:
            covered += 1
        total += best
    return covered, total


@dataclass(slots=True)
class SearchFilters:
    """二次筛选（FR-SEARCH-03）：时长/大小/日期区间与扩展名，逐项可选。

    区间边界为 None 表示该项不筛。**取不到值的卡片按不通过处理**：既然筛「时长 ≥ 3 分钟」，
    一条时长未知的卡片无法证明它满足，放进来就是编造。
    """

    duration_min: int | None = None
    duration_max: int | None = None
    size_min: int | None = None
    size_max: int | None = None
    date_from: str | None = None  # YYYY-MM-DD；与 message_date 的文本序比较
    date_to: str | None = None
    exts: tuple[str, ...] = ()

    @staticmethod
    def parse(raw: dict[str, Any] | None) -> SearchFilters:
        """请求体 ``filters`` → 结构化筛选；认不出的值忽略（不编造、不抛 500）。"""
        if not isinstance(raw, dict):
            return SearchFilters()
        exts = raw.get("exts")
        return SearchFilters(
            duration_min=_opt_int(raw.get("duration_min"), 0),
            duration_max=_opt_int(raw.get("duration_max"), 0),
            size_min=_opt_int(raw.get("size_min"), 0),
            size_max=_opt_int(raw.get("size_max"), 0),
            date_from=_opt_date(raw.get("date_from")),
            date_to=_opt_date(raw.get("date_to")),
            exts=tuple(_norm_ext(e) for e in exts if isinstance(e, str))
            if isinstance(exts, list)
            else (),
        )

    @property
    def active(self) -> bool:
        return any(
            (
                self.duration_min is not None,
                self.duration_max is not None,
                self.size_min is not None,
                self.size_max is not None,
                self.date_from is not None,
                self.date_to is not None,
                bool(self.exts),
            )
        )

    def matches(self, card: SearchResultCard) -> bool:
        if not _in_range(card.duration_sec, self.duration_min, self.duration_max):
            return False
        if not _in_range(card.file_size, self.size_min, self.size_max):
            return False
        day = (card.message_date or "")[:10]
        if self.date_from is not None and (not day or day < self.date_from):
            return False
        if self.date_to is not None and (not day or day > self.date_to):
            return False
        return not self.exts or (card.ext or "").lower().lstrip(".") in self.exts


def _opt_int(value: Any, floor: int) -> int | None:
    """非负整数取值；缺省/非法（含负数、bools）→ None（= 不筛）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = int(value)
    return number if number >= floor else None


def _opt_date(value: Any) -> str | None:
    """YYYY-MM-DD 取值；形如 ``2024-1-2`` 这类不补零的写法不收（文本序比较会错）。"""
    if not isinstance(value, str):
        return None
    day = value.strip()[:10]
    return day if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) else None


def _norm_ext(ext: str) -> str:
    return ext.strip().lower().lstrip(".")


def _in_range(value: int | None, low: int | None, high: int | None) -> bool:
    if low is None and high is None:
        return True
    if value is None:
        return False
    return (low is None or value >= low) and (high is None or value <= high)


def matches_fields(card: SearchResultCard, tokens: list[str], fields: list[str] | None) -> bool:
    """勾选字段过滤（SRS FR-SEARCH-01「本地再按勾选字段过滤」）。

    ``fields`` 为空/None = 不按字段过滤（上游给什么算什么）；
    勾了但一个都不认得（如只有 ``album``）= 同样不过滤，避免「筛出空结果」这种假失败。
    """
    if not fields or not tokens:
        return True
    getters = [_SEARCH_FIELD_TEXT[f] for f in fields if f in _SEARCH_FIELD_TEXT]
    if not getters:
        return True
    for token in tokens:
        for get in getters:
            value = normalize_keyword(get(card) or "")
            if value and token in value:
                break
        else:
            return False
    return True


def sort_cards(cards: list[SearchResultCard], sort: str, keyword: str) -> list[SearchResultCard]:
    """排序（FR-SEARCH-03）：默认相关度，可换日期/时长/大小；未知值按相关度。

    先按日期新→旧铺一遍，再按主键稳定排序——日期既当主键也当同分时的次序依据，
    不需要为每个维度再拼一套复合键。
    """
    by_date = sorted(cards, key=lambda c: c.message_date or "", reverse=True)
    if sort == "date":
        return by_date
    if sort == "duration":
        return sorted(by_date, key=lambda c: _or_neg(c.duration_sec), reverse=True)
    if sort == "size":
        return sorted(by_date, key=lambda c: _or_neg(c.file_size), reverse=True)
    tokens = keyword_tokens(keyword)
    return sorted(by_date, key=lambda c: relevance_score(c, tokens), reverse=True)


def _or_neg(value: int | None) -> int:
    """降序排序里「未知」排在最后：取 -1 当哨兵（时长/大小都非负）。"""
    return -1 if value is None else value


def card_key(card: SearchResultCard) -> str:
    """卡片行键：优先 ``file_unique_id``（同一文件跨频道转发的唯一标识），缺失时退回
    ``(chat_id, message_id)``。去重与「翻页已发过哪些行」都认它，故定义只此一处。"""
    return card.file_unique_id or f"{card.chat_id}:{card.message_id}"


def dedupe_cards(cards: list[SearchResultCard]) -> list[SearchResultCard]:
    """跨源去重（pansou ``mergeSearchResults`` 的对应）：同一份文件只留一份。

    调用方先排序再调它，于是保留的是新→旧里更靠前的那一份。
    """
    seen: set[str] = set()
    out: list[SearchResultCard] = []
    for card in cards:
        key = card_key(card)
        if key in seen:
            continue
        seen.add(key)
        out.append(card)
    return out
