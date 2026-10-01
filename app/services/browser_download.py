"""浏览器下载（FR-DL-08，SDD §2.8）：把搜索结果直接交给浏览器保存。

与正式下载（FR-DL-01~06）的分工：正式下载入队、按模板落 `save_path`、写历史与曲库、
可暂停重试；浏览器下载只把这首取回服务端临时区，再用
``Content-Disposition: attachment`` 推给浏览器——文件落在**打开网页那台电脑**的下载
目录里，不进队列、不写历史、不落 `save_path`、不参与去重。

与试听（FR-PLAY-02）的差别：试听按试听档位取、进 LRU 缓存预算、随时可被淘汰；浏览器
下载按用户选的档位取，取回后**在 TTL 内可重复取用**，不占试听预算。因此临时文件另开
``temp/browser/`` 子目录，与 ``task_*`` 分片同区不同前缀，清扫不会误伤在传的分片。

**为什么不是「取走即删」**：浏览器对大文件会分段取（`FileResponse` 自带 Range/206），
下载管理器还会在暂停后按 Range 续传。只允许取一次的话，这些请求拿到的是片段、且第二次
直接 404——用户看到的是「下载到一半就坏了」。故登记在 TTL 内保持有效，由
``sweep()`` 按 TTL 与并存上限（``MAX_PREPARED``）回收临时文件；回收的入口与时机
复用设置页那张缓存卡（``stats()`` / ``clear()``）与启动装配，不另起一套 UI。

取数与搜索/下载/试听共用同一份来源索引（``MusicSourceProto``）：本服务同样不认
Telegram、也不认 ChKSz，只管临时文件、token 与文件名。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import uuid4

from app.domain import AUDIO_EXTS, TemplateConfig, TrackMeta
from app.errors import AppError
from app.ports.music import FetchRef, MusicSourceIndexProto, ProgressCb
from app.services.path_builder import render_template
from app.utils.sanitize import sanitize_segment

logger = logging.getLogger(__name__)

#: 准备结果的存活时间（秒）：浏览器迟迟不来取（用户关了页签、导航失败）的临时文件
#: 与登记一并作废。取数本身可能长达几分钟，这个值要远大于任何一次正常取数；
#: 也要容得下「浏览器分段取 / 暂停后续传」这段时间。
TOKEN_TTL_SEC = 2 * 3600

#: 并存上限（份）：TTL 内可重复取，代价是文件要留一会儿。上限把「连点数首」
#: 的磁盘占用钉在可控范围（超出时最旧的先删），不至于攒出一个无界的临时堆。
MAX_PREPARED = 8

#: 临时区子目录名：与 download 服务的 ``task_*`` 分片同区不同名，清扫互不影响。
SUBDIR = "browser"

#: mime → 扩展名（FR-DL-08 的最后一档兜底）。为什么需要它：Telegram 源取数**不回报**
#: `ext`（消息里那个文件就是它本身），搜索卡片的 `file_name` 也可能没有点（document
#: 音频常见）——两个都没有时文件名就没了扩展名，用户双击打不开（浏览器不会替他补）。
#: 在线源另有 ``_ext_of``（format → URL 后缀 → 档位推定），正常走不到这里。
MIME_EXTS: dict[str, str] = {
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/flac": "flac",
    "audio/x-flac": "flac",
    "audio/mp4": "m4a",
    "audio/x-m4a": "m4a",
    "audio/ogg": "ogg",
    "audio/opus": "opus",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/aac": "aac",
}


def _ext_from_mime(mime: str | None) -> str | None:
    """mime → 扩展名（去掉 ``; charset=…`` 之类的参数再查表）。认不出返回 None，不编。"""
    if not mime:
        return None
    return MIME_EXTS.get(mime.split(";", 1)[0].strip().lower())


def _known_ext(ext: str | None) -> str | None:
    """客户端给的扩展名归一 + 白名单：小写、去点，且必须是 ``AUDIO_EXTS`` 里认得的。

    扩展名的唯一事实源是 ``domain.AUDIO_EXTS``（曲库扫描与标签容器都按它认人）；
    这里照同一份清单收口——``FLAC`` 归一成 ``flac``，``.`` / ``../evil`` / ``xyz``
    一律丢掉（宁可不给扩展名，也不给一个错的或带路径的）。
    """
    if not ext:
        return None
    candidate = ext.strip().lower().lstrip(".")
    return candidate if f".{candidate}" in AUDIO_EXTS else None


@dataclass(slots=True, frozen=True)
class Prepared:
    """一次准备的结果（路由层据此拼响应）。"""

    token: str
    file_name: str
    size: int


@dataclass(slots=True, frozen=True)
class BrowserCacheStats:
    """临时区占用（FR-DL-08）：与试听缓存同一口径——磁盘实际字节，不是登记里记的。"""

    bytes: int
    count: int


@dataclass(slots=True)
class _Entry:
    """一份已取回、待浏览器取走的临时文件（内部结构，不出服务）。"""

    path: Path
    file_name: str
    #: 复用的比对键（provider/chat/message/ref/quality）：同一首歌重复点要能认出是同一份。
    key: str
    #: 登记时刻（单调时钟）；命中复用时刷新（用户正在用它，不该在手的这份到期）。
    created: float


class BrowserDownloadService:
    """浏览器下载的取数与交付（FR-DL-08）：取回一次，TTL 内可重复取用。"""

    def __init__(
        self,
        registry: MusicSourceIndexProto,
        temp_dir: Path,
        cfg: TemplateConfig,
        ttl_sec: int = TOKEN_TTL_SEC,
    ) -> None:
        self.registry = registry
        #: 与下载服务同一份模板（组合根注入的是同一个对象）：浏览器下载目录里的文件名
        #: 与曲库里的名字一致，用户不必在两个地方认两套命名。
        self.cfg = cfg
        self.dir = temp_dir / SUBDIR
        self.ttl_sec = ttl_sec
        #: 取数并发 1：与试听槽（FR-PLAY-03）同口径但**各用各的**——浏览器下载连点
        #: 同样会打满上游吃 FloodWait，但它不该把试听槽占死，反之亦然。
        self._sem = asyncio.Semaphore(1)
        #: token → 已取回的那一份；条数受 MAX_PREPARED 约束，线性扫足够（≤8 条）。
        self._prepared: dict[str, _Entry] = {}

    async def prepare(
        self,
        meta: TrackMeta,
        quality: str | None = None,
        progress: ProgressCb | None = None,
    ) -> Prepared:
        """取回这一首并登记 token（FR-DL-08，TTL 内可重复取）。

        **同一首歌（同 provider/定位/档位）在 TTL 内重复点直接复用**已取回的那一份：
        不重打上游、不重写磁盘、连 token 都是同一个——「再点一次」从几分钟变成毫秒级。
        取数失败抛 ``AppError``：前端把它当成行内错误展示（与试听失败同款），
        不会把用户带到浏览器的错误页。

        ``progress`` 是**取数层的字节进度**（``(已写, 总量)``，总量可能报不出来）：
        路由层拿它推给前端画进度条（FR-DL-08 的「取回中」）。命中复用时不会被调用——
        那一份早就在磁盘上了，没有「进度」可言，调用方拿到返回值即是「已经好了」。
        """
        key = self._key(meta, quality)
        hit = self._reuse(key)
        if hit is not None:
            return hit
        target = self.registry.by_meta(meta.provider, None)
        if target is None:
            raise AppError("browser_download_failed", f"没有可用的取数来源：{meta.provider}")
        token = uuid4().hex
        temp_path = self.dir / f"{token}.part"
        async with self._sem:
            # 清扫排在取数之前、且在同一个槽里：这样它不会在别人正在写分片时判定旧文件
            self.dir.mkdir(parents=True, exist_ok=True)
            self.sweep()
            # 取数槽内再查一次：连点两次时，后到的那个在这里命中先到的刚登记的那份，
            # 不必排队等它下完再下一次（否则「重复点」的代价是两倍的流量与时间）
            hit = self._reuse(key)
            if hit is not None:
                return hit
            try:
                result = await target.fetch(
                    FetchRef.of(meta, target.scope_id, quality), temp_path, progress=progress
                )
            except asyncio.CancelledError:
                # 取消（前端断开 / 进程收尾）也要收拾：半截分片留到 TTL 才清是白占磁盘
                self._unlink(temp_path)
                raise
            except AppError:
                self._unlink(temp_path)
                raise
            except Exception as e:  # noqa: BLE001  上游异常一律翻译成业务错误（§2.4）
                self._unlink(temp_path)
                raise AppError("browser_download_failed", f"取数失败：{e}") from e
        # 扩展名的三档来源，按可信度排：上游实测（在线源会静默降级/升档，拿请求的档位
        # 命名等于把没下到的东西说成下到了）→ 卡片上的 ext → 卡片上的 mime 推定。
        # 卡片的 ext 过一遍归一与白名单（唯一事实源是 domain.AUDIO_EXTS）：它是客户端
        # 可控字段，"FLAC" 这种大小写与 "." / 空白 这类怪值都不该变成文件名的一部分。
        ext = result.ext or _known_ext(meta.ext) or _ext_from_mime(meta.mime)
        # 无条件覆盖成选定值：ext 为 None 时**必须把卡片上那个被否掉的 ext 擦掉**，
        # 否则 `_file_name` 会拿原值再清洗一遍（"../evil" 变成 "._evil" 继续进文件名）。
        name_meta = replace(meta, ext=ext)
        try:
            size = temp_path.stat().st_size
        except OSError:
            # 用户在设置页点了「清理缓存」，正好把这份正在取的临时文件删了（显式动作）。
            # 这是业务失败而不是服务端故障：走错误包络回 400，别漏成裸 500。
            raise AppError("browser_download_failed", "临时文件已被清理，请重新下载") from None
        # 卡片声明的字节数就是期望值（只有 Telegram 卡片带得上）：不一致等于要把半截
        # 文件交给浏览器，宁可不给（FR-DL-03；在线源声明不出大小，那一档由适配器自己兜住）。
        if meta.file_size is not None and size != meta.file_size:
            self._unlink(temp_path)
            raise AppError(
                "browser_download_failed", f"取回的大小不符：{size} != {meta.file_size}"
            )
        file_name = self._file_name(name_meta)
        self._evict_overflow()
        self._prepared[token] = _Entry(
            path=temp_path, file_name=file_name, key=key, created=time.monotonic()
        )
        logger.info(
            "browser download prepared token=%s name=%s bytes=%s", token, file_name, size
        )
        return Prepared(token=token, file_name=file_name, size=size)

    def resolve(self, token: str) -> tuple[Path, str] | None:
        """按 token 取回（FR-DL-08）：TTL 内可重复取，命中即返回路径与文件名。

        **不摘登记**：浏览器对大文件会分段取（Range/206），下载管理器暂停后还要续传——
        只给一次会让这类请求拿到片段、第二次直接 404。生命周期交给 ``sweep()``
        （TTL + 并存上限）与显式的缓存清理，不由某一次响应决定。
        """
        entry = self._prepared.get(token)
        if entry is None:
            return None
        return (entry.path, entry.file_name) if entry.path.exists() else None

    @staticmethod
    def _key(meta: TrackMeta, quality: str | None) -> str:
        """复用的比对键：**同一首歌**的判据（FR-DL-08）。

        定位三件套（provider + chat_id/message_id + ref）缺一不可：在线源的
        ``message_id`` 是曲目 id 的哈希，``ref`` 才是平台 id；档位也算进来——
        同一首歌选 320k 与选母带是两份不同的文件，不能互相复用。
        """
        return "|".join(
            (meta.provider, str(meta.chat_id), str(meta.message_id), meta.ref or "", quality or "")
        )

    def _reuse(self, key: str) -> Prepared | None:
        """命中已有准备就复用（FR-DL-08）：同一首歌短时间内重复点，不重打一次上游。

        命中即**续期**（``created`` 刷新到现在）：用户正在用它，不该在手的这份到期。
        文件不在了或已超 TTL 的登记就地作废并让调用方重新取数——复用只省重复的活，
        不改变「取不到就报错」的语义。条数受 ``MAX_PREPARED`` 约束，线性扫足够。
        """
        for token, entry in self._prepared.items():
            if entry.key != key:
                continue
            stale = time.monotonic() - entry.created > self.ttl_sec
            if stale or not entry.path.exists():
                self._prepared.pop(token, None)
                self._unlink(entry.path)
                return None
            try:
                size = entry.path.stat().st_size
            except OSError:
                # 刚被「清理缓存」删掉：当作没命中，让调用方重新取
                self._prepared.pop(token, None)
                return None
            entry.created = time.monotonic()
            logger.info("browser download reused token=%s name=%s", token, entry.file_name)
            return Prepared(token=token, file_name=entry.file_name, size=size)
        return None

    def _file_name(self, meta: TrackMeta) -> str:
        """浏览器里的文件名：曲库同一份文件名模板 + 实测扩展名（FR-DL-08）。

        模板渲染可能留下空段或首尾空白（``{track:02d}`` 无值时），一律清洗；
        **扩展名也清洗**——它跟着搜索卡片从客户端来（`message_refs`），
        带着分隔符或引号就直接进了 `Content-Disposition`。
        """
        stem = render_template(self.cfg.file_template, meta, self.cfg).strip()
        stem = sanitize_segment(stem or f"message_{meta.message_id}")
        # ext 在选定那一刻就过完白名单了（_known_ext / _ext_from_mime / 适配器实测）；
        # 这里仍走一遍 sanitize：它是**出口**，任何绕过选定的路径都不该直接进响应头。
        ext = sanitize_segment(meta.ext).lstrip(".") if meta.ext else ""
        return f"{stem}.{ext}" if ext else stem

    def sweep(self) -> None:
        """清掉超龄与崩溃残留的临时文件（FR-DL-08）。

        三道来源：① 内存登记里超 TTL 的；② 磁盘上比 TTL 更旧、登记里已经没有的
        （进程崩在半路时内存登记随进程消失，只剩文件）；③ 并存超过 ``MAX_PREPARED``
        时**最旧**的那些（dict 插入序即登记序）。只扫自己的子目录，不碰 ``task_*`` 分片。

        公开入口：启动装配后（与试听缓存启动修剪同一批）与「清理缓存」都用它。
        扫盘同步跑：这个目录最多 ``MAX_PREPARED`` 份外加几个孤儿，代价是微秒级；
        试听缓存那边走 ``to_thread`` 是因为它可能有几十上百个文件。
        """
        now = time.monotonic()
        for token in [
            t for t, entry in self._prepared.items() if now - entry.created > self.ttl_sec
        ]:
            self._unlink(self._prepared.pop(token).path)
        if not self.dir.is_dir():
            return
        cutoff = time.time() - self.ttl_sec
        alive = {entry.path.name for entry in self._prepared.values()}
        for p in self.dir.iterdir():
            try:
                if p.is_file() and p.name not in alive and p.stat().st_mtime < cutoff:
                    self._unlink(p)
            except OSError:
                continue  # 并发清理窗口：文件刚被删

    def stats(self) -> BrowserCacheStats:
        """临时区占用（FR-DL-08）：磁盘实际字节与份数，供设置页那张缓存卡合并显示。"""
        total = 0
        count = 0
        if self.dir.is_dir():
            for p in self.dir.iterdir():
                try:
                    if p.is_file():
                        total += p.stat().st_size
                        count += 1
                except OSError:
                    continue
        return BrowserCacheStats(bytes=total, count=count)

    def clear(self) -> None:
        """清空临时区（FR-DL-08）：「清理缓存」按钮一并执行。

        清掉的是**待取走**的临时文件——正在下的那一份也没了，用户重新点按钮即可
        （与试听缓存同款：清缓存是用户显式动作，不假装它不疼）。内存登记一并作废：
        留着指向已删文件的 token 只会给出 404。
        """
        self._prepared.clear()
        if not self.dir.is_dir():
            return
        for p in self.dir.iterdir():
            try:
                if p.is_file():
                    self._unlink(p)
            except OSError:
                continue

    def _evict_overflow(self) -> None:
        """并存上限（FR-DL-08）：TTL 内可重复取，代价是文件要留一会儿。

        上限保证「连点数首」的磁盘占用可控；超出时从最旧的开始删（那些多半是用户
        已经下完、或早就改主意的）。
        """
        overflow = len(self._prepared) - MAX_PREPARED + 1  # +1：给即将登记的这一份留位
        for token in list(self._prepared)[: max(0, overflow)]:
            self._unlink(self._prepared.pop(token).path)

    def _unlink(self, path: Path) -> None:
        """删临时文件；删不掉（Windows 上正被浏览器流占用）只记日志，不打断响应。"""
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("browser download temp busy, kept path=%s", path)
