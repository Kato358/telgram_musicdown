"""ChKSz 在线源适配器：把三个平台归一成 ``MusicSourceProto``（SDD §2.7）。

四家接口的字段名与形状各不相同（网易 ``data``/``artists``/``br``/``level``，
QQ 与酷狗 ``list``/``singer``/``bitrate``/``interval`` 文本），归一化只在这里发生，
服务层看到的永远是同一种卡片与同一种取数结果。

**不编造**：上游没给的字段一律留 None。搜索结果里只有酷狗给时长，另两家没给，
所以卡片的 ``duration_sec`` 常常是空的——界面显示「—」，而不是拿 0 冒充。
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Any

from app.chksz.client import ChkszClient, ChkszError
from app.chksz.quality import label_of, native_value, normalize, tier_of
from app.domain import AUDIO_EXTS, PROVIDER_SCOPES, SearchResultCard, chksz_unique_id
from app.errors import SourceUnreachableError
from app.ports.music import FetchRef, FetchResult, ProgressCb, SearchWindow

logger = logging.getLogger(__name__)

# 扩展名在本项目里一律**不带点**（domain.message_to_card 就是这么产的，path_builder
# 落盘时自己补点）。带点会渲成「歌名..flac」这种双点文件名。
_EXTS = frozenset(ext.lstrip(".") for ext in AUDIO_EXTS)

# 平台显示名（搜索页药丸与卡片来源标签用）。
PROVIDER_LABELS: dict[str, str] = {"163": "网易云", "qq": "QQ 音乐", "kugo": "酷狗"}

# 一次搜索向单个平台要多少条。QQ 的 num 上限 50，酷狗没有条数参数（上游给多少算多少）。
_MAX_PER_FETCH: dict[str, int] = {"163": 100, "qq": 50, "kugo": 50}

_INTERVAL_RE = re.compile(r"^\d{1,2}(?::\d{1,2}){1,2}$")
_SECONDS_MAX = 59

_HTTP_UNAUTHORIZED = 401
_HTTP_FORBIDDEN = 403
_HTTP_PAYMENT_REQUIRED = 402
_HTTP_TOO_MANY_REQUESTS = 429

# 档位 → 容器（不带点）。解析接口不一定给 format/扩展名，但总给得出档位。
_LEVEL_EXTS: dict[str, str] = {
    "flac": "flac",
    "lossless": "flac",
    "hires": "flac",
    "master": "flac",
    "jymaster": "flac",
    # 2026-09-27 实测：sky 与 jyeffect 同样落在 flac 容器上（br 分别 1488k / 2342k）
    "sky": "flac",
    "jyeffect": "flac",
    "320k": "mp3",
    "exhigh": "mp3",
    "128k": "mp3",
    "standard": "mp3",
}

_LEVEL_MIMES: dict[str, str] = {
    "flac": "audio/flac",
    "mp3": "audio/mpeg",
    "m4a": "audio/mp4",
    "ogg": "audio/ogg",
    "wav": "audio/wav",
    "aac": "audio/aac",
}


def _message_id(provider: str, ref: str) -> int:
    """平台曲目 id → 保留正整数 message_id。

    ``history`` 与 ``preview_cache`` 都以 UNIQUE(chat_id, message_id) 定位一条音频，
    而平台曲目 id 是字符串、跨平台还会重名，所以取 sha1 前 7 字节压成正整数。
    撞号理论上可能，但去重的第一顺位是 ``file_unique_id``，这个哈希只兜底。
    """
    digest = hashlib.sha1(  # noqa: S324  身份散列，不用于安全
        f"{provider}:{ref}".encode(), usedforsecurity=False
    ).digest()
    return int.from_bytes(digest[:7], "big") & ((1 << 62) - 1)


def _text(value: Any) -> str | None:
    """字符串字段取值：空串当没有（上游用 "" 表示「没给」）。"""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int(value: Any) -> int | None:
    """整数取值；字符串数字认，非数字与 0 认不出。"""
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number or None


def _duration(value: Any) -> int | None:
    """时长：秒数（酷狗搜索）或 ``"3:28"`` / ``"1:02:33"`` 文本（QQ 与酷狗的 interval）。"""
    if isinstance(value, str):
        text = value.strip()
        if not _INTERVAL_RE.match(text):
            return None
        parts = [int(p) for p in text.split(":")]
        if any(p > _SECONDS_MAX for p in parts[1:]):
            return None
        total = 0
        for part in parts:
            total = total * 60 + part
        return total
    return _int(value)


def _duration_ms(value: Any) -> int | None:
    """网易的 ``duration`` 是**毫秒**（实测 278961 ≈ 4'39"），其余两家是秒或 ``m:ss`` 文本。

    混用单位会渲出「278961s」这种一眼假的时长，所以按平台分开取。
    """
    millis = _int(value)
    return millis // 1000 if millis else None


def _tail_ext(url: str) -> str | None:
    """URL 路径末段的扩展名（先去掉查询串）。"""
    tail = url.split("?", 1)[0].rsplit("/", 1)[-1]
    return tail.rsplit(".", 1)[-1] if "." in tail else None


def _ext_of(url: str, fmt: str | None, level: str | None) -> str | None:
    """落盘扩展名：format → URL 后缀 → 档位推定。

    只认 ``AUDIO_EXTS`` 里的容器（domain 是扩展名的唯一事实源）：认不出来就返回
    None，让落盘走缺省，绝不写出一个应用不认的扩展名。
    """
    candidate = _text(fmt) or _tail_ext(url)
    if candidate is None and level:
        candidate = _LEVEL_EXTS.get(level)
    if not candidate:
        return None
    ext = candidate.strip().lower().lstrip(".")
    return ext if ext in _EXTS else None


def _mime_of(ext: str | None) -> str | None:
    return _LEVEL_MIMES.get(ext or "")


class ChkszSource:
    """一个在线源平台（网易云 / QQ 音乐 / 酷狗）适配器。"""

    def __init__(
        self, provider: str, client: ChkszClient, *, default_quality: str = "flac"
    ) -> None:
        if provider not in PROVIDER_SCOPES:
            raise ValueError(f"未知的在线源平台：{provider}")
        self.provider = provider
        self.scope_id = PROVIDER_SCOPES[provider]
        self.label = PROVIDER_LABELS[provider]
        self._client = client
        self._default_quality = default_quality

    def apply_settings(self, download_quality: str) -> None:
        """设置页保存后即时换默认档（FR-CFG-03：不必重启）。"""
        self._default_quality = download_quality

    # ---- 搜索 ----

    async def search(self, keyword: str, need: int, offset: int = 0) -> SearchWindow:
        """一次取数窗口。

        三个平台都只返回音乐，一轮就够——不像 Telegram 那样上游混着非音频消息、
        得反复续取才凑得齐卡片数。QQ 与酷狗没有 offset 参数（接口只给条数），
        传了也被忽略：深翻页由缓存窗口「重新要更深」实现，不靠上游翻页。
        """
        want = min(max(1, need), _MAX_PER_FETCH.get(self.provider, need))
        try:
            if self.provider == "163":
                rows = await self._client.search_163(keyword, want, offset)
            elif self.provider == "qq":
                rows = await self._client.search_qq(keyword, want)
            else:
                rows = await self._client.search_kugo(keyword, want)
        except ChkszError as e:
            raise SourceUnreachableError(self._reason_of(e), e.message) from e
        cards = (self._to_card(row) for row in rows)
        items = [card for card in cards if card is not None]
        # has_more 只能按「上游给满了我要的数量」判断：这三家没有可靠的总数字段。
        return SearchWindow(items=items, consumed=len(rows), has_more=len(rows) >= want)

    @staticmethod
    def _reason_of(e: ChkszError) -> str:
        """HTTP 状态 → 搜索层认得的 reason（前端据此给不同的修复提示）。"""
        if e.status in (_HTTP_UNAUTHORIZED, _HTTP_FORBIDDEN):
            return "invalid_key"
        if e.status == _HTTP_PAYMENT_REQUIRED:
            return "quota_exhausted"
        if e.status == _HTTP_TOO_MANY_REQUESTS:
            return "flood_wait"
        return "error"

    def _to_card(self, row: dict[str, Any]) -> SearchResultCard | None:
        """搜索行 → 卡片。

        id 的键名各平台不同：网易与酷狗是 ``id``，QQ 是 14 位字母数字的 ``mid``。
        缺 id 或缺歌名的行丢掉——认不出的行进了列表也是错的。
        """
        ref = _text(row.get("mid") if self.provider == "qq" else row.get("id"))
        title = _text(row.get("name"))
        if ref is None or title is None:
            return None
        # 网易的歌手键是复数 artists，QQ 与酷狗是 singer。
        singer = row.get("artists") if self.provider == "163" else row.get("singer")
        return SearchResultCard(
            chat_id=self.scope_id,
            message_id=_message_id(self.provider, ref),
            title=title,
            artist=_text(singer),
            # 网易给毫秒，酷狗给秒，QQ 压根不给；三家单位不统一，按平台取。
            duration_sec=(
                _duration_ms(row.get("duration"))
                if self.provider == "163"
                else _duration(row.get("duration"))
            ),
            file_size=None,
            ext=None,
            mime=None,
            channel_title=self.label,
            message_date=None,
            caption=_text(row.get("pay")),
            file_unique_id=chksz_unique_id(self.provider, ref),
            bitrate=None,
            provider=self.provider,
            ref=ref,
        )

    # ---- 取音频 ----

    async def fetch(
        self, ref: FetchRef, dest: Path, progress: ProgressCb | None = None
    ) -> FetchResult:
        """解析播放地址并流式落盘。

        解析不出地址不是网络问题，是**这首歌没有这一档**：单独给一个能说清的
        错误，别混进「解析失败」里让人以为是 Key 的问题。
        """
        track = ref.ref
        if not track:
            raise ChkszError(0, "在线源曲目缺少 ref：无法解析播放地址", "chksz_bad_ref")
        tier = normalize(self.provider, ref.quality, self._default_quality)
        native = native_value(self.provider, tier)
        try:
            if self.provider == "163":
                detail = await self._client.resolve_163(track, native)
            elif self.provider == "qq":
                detail = await self._client.resolve_qq(track, native)
            else:
                detail = await self._client.resolve_kugo(track, native)
        except ChkszError as e:
            raise ChkszError(e.status, f"{self.label} 解析失败：{e.message}", e.code) from e
        url = _text(detail.get("url"))
        if not url:
            raise ChkszError(
                404,
                f"{self.label} 没有「{label_of(tier)}」这一档的播放地址，这首歌可能不支持",
                "chksz_no_url",
            )
        ext = _ext_of(url, _text(detail.get("format")), _text(detail.get("level")))
        written = await self._client.download(url, dest, progress)
        return FetchResult(
            file_size=written,
            # 网易给的是 bps 真码率；QQ 与酷狗的 bitrate 是档位名（如 "flac"），
            # 那不是 kbps，硬折出来的数字是假的，索性不给。
            bitrate=self._bitrate_of(detail),
            mime=_mime_of(ext),
            ext=ext,
            # 回报**实际拿到**的档位（上游对没有的音质会静默降级），翻回语义档位给界面读。
            level=tier_of(self.provider, _text(detail.get("level")))
            or tier_of(self.provider, _text(detail.get("bitrate")))
            or tier,
        )

    @staticmethod
    def _bitrate_of(detail: dict[str, Any]) -> int | None:
        bps = _int(detail.get("br"))
        return bps // 1000 if bps else None
