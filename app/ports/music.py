"""音乐来源端口（DIP）：搜索与取音频的统一协议面。

一个「音乐来源」= 任何能按关键词搜出歌、又能把歌取到本地的东西。Telegram 音乐源频道
（``app/telegram/music_source.py``）与 ChKSz 在线源平台（``app/chksz/source.py``）
是它的两个适配器，搜索、下载、试听三条链路都只认这个协议——它们不认识 Telegram，
也不认识 ChKSz。

加一个新来源 = 写一个实现本模块协议的类 + 在 ``app/registry.py`` 注册；
``SearchService`` / ``DownloadService`` / ``PreviewService`` 与数据表都不用动。

依赖方向：services → ports ← adapters（db / telegram / chksz）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.domain import PROVIDER_TELEGRAM, SearchResultCard, TrackMeta

# 下载进度回调：(已收字节, 总字节)；总字节未知时传 None。
ProgressCb = Callable[[int, int | None], None]


@dataclass(slots=True, frozen=True)
class FetchRef:
    """取一首音频的定位信息。

    Telegram 源用 ``chat_id``/``message_id``（那才是音频消息的身份），在线源用
    ``ref``（平台曲目 id）。两个实现各读自己那一半，另一半是 ``None``——不编造
    对方的定位方式。
    """

    provider: str
    scope_id: int
    chat_id: int | None = None
    message_id: int | None = None
    ref: str | None = None
    #: 目标音质（在线源的原生档位名）。Telegram 源忽略它——频道里的文件就是它本身。
    quality: str | None = None

    @classmethod
    def of(cls, meta: TrackMeta, scope_id: int, quality: str | None = None) -> FetchRef:
        """``TrackMeta`` + 音质 → 取数定位（入队时构造一次，之后只随任务走）。"""
        return cls(
            provider=meta.provider,
            scope_id=scope_id,
            chat_id=meta.chat_id,
            message_id=meta.message_id,
            ref=meta.ref,
            quality=quality,
        )

@dataclass(slots=True, frozen=True)
class SearchWindow:
    """一个来源在某个关键词上的一次取数结果。

    ``consumed`` 是**已消费的上游条数**而非卡片数：Telegram 的搜索会混着非音频
    消息，拿到 ``need`` 张卡片往往消费了更多上游条目，续取要从 ``consumed`` 接着
    往上翻而不是从卡片数往上翻（翻错会漏歌）。
    """

    items: list[SearchResultCard]
    consumed: int
    has_more: bool


@dataclass(slots=True, frozen=True)
class FetchResult:
    """取音频的结果：落盘后**实测**的事实。

    码率与格式是上游真正给到的那一档，不是用户请求的那一档——在线源会对没有的
    音质静默降级（母带歌多半没有），谎报成所选档等于骗人（SDD §2.7）。
    """

    file_size: int
    bitrate: int | None = None
    mime: str | None = None

    #: 上游真正给到的音质档位（在线源原生值，如 ``flac``/``lossless``）。
    #: Telegram 源为 None——频道里的文件就是它本身，没有「档」可言。
    level: str | None = None
    ext: str | None = None


class MusicSourceProto(Protocol):
    """音乐来源的协议面：搜得到 + 取得到。

    实现方自由决定身份怎么编码（音乐源频道用 ``sources.id``，在线源用保留负号
    scope），但 ``scope_id`` 必须全局唯一——二级缓存、定序前缀与历史行去重都拿它当键。
    """

    #: 取数缓存的 scope（domain.PROVIDER_SCOPES 或 sources.id）。
    scope_id: int

    #: 来源标识：``telegram`` 或在线源平台名（``domain.PROVIDER_SCOPES`` 的键）。
    provider: str

    #: 界面显示名（音乐源频道名 / 「网易云」…）。
    label: str

    async def search(self, keyword: str, need: int, offset: int = 0) -> SearchWindow:
        """从上游 ``offset`` 处续取，最多凑 ``need`` 张卡片。

        拿不到就抛 ``SourceUnreachableError``（reason 如 ``flood_wait``、
        ``invalid_key``）：搜索扇出会把它收进 ``meta.unreachable``，不废掉整次搜索。
        """
        ...



    async def fetch(
        self, ref: FetchRef, dest: Path, progress: ProgressCb | None = None
    ) -> FetchResult:
        ...


class MusicSourceIndexProto(Protocol):
    """来源索引：搜索要「当前所有可搜的源」，下载/试听要「按 scope 找回那个源」。

    服务层只依赖本协议（组合根注入具体实现），故 ``DownloadService`` 不必认识
    Telegram 客户端也不必认识 ChKSz。
    """

    def targets(self) -> list[MusicSourceProto]:
        """当前可搜的来源：启用的音乐源频道 + 启用的在线源平台。"""
        ...

    def by_scope(self, scope_id: int) -> MusicSourceProto | None:
        """按 scope 找回来源；已停用或已移除返回 None（调用方据此拒绝执行）。"""
        ...

    def by_meta(self, provider: str, source_id: int | None) -> MusicSourceProto | None:
        """按一条历史/任务记录的归属找回「能取到它」的来源。

        与 :meth:`by_scope` 的区别在于**取得到**不等于**搜得到**：一条已停用甚至
        已删除的音乐源，它的音频照样能按 (chat_id, message_id) 取回来，所以
        Telegram 侧在源行找不到时仍返回一个游离适配器。认不出的 provider 返回 None。
        """
        ...

    def online_sources(self) -> list[MusicSourceProto]:
        """清单里的在线源那一段（逐平台开关保存后即时变，界面据此摆药丸）。"""
        ...

    def apply_chksz(self, providers: tuple[str, ...], download_quality: str) -> None:
        """就地切换启用的在线平台与默认档位（FR-CFG-03：吃的是刚保存的当前值）。"""
        ...


def is_online(provider: str) -> bool:
    """是否在线源平台（供界面与文案分支；判据只此一处）。"""
    return provider != PROVIDER_TELEGRAM
