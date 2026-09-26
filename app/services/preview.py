"""试听（FR-PLAY-01~03，SDD §2.4）与封面缓存。

- 预览槽独立于下载队列（并发 1，FR-PLAY-03）。
- 试听不入队、不写 save_path、不写 success 历史（FR-PLAY-02）。
- LRU 淘汰：总量 > preview_cache_max_bytes（默认 512MB）或条数 > 50。
- 封面按「歌名 + 歌手」从公共封面接口（api.lrc.cx）取，落盘缓存。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

from app.db.models import PreviewCache
from app.db.store import utcnow
from app.domain import AUDIO_EXTS
from app.errors import AppError
from app.events import Event, EventBus
from app.ports import IStore
from app.ports.telegram import TelegramClientProto
from app.services.tags import image_media_type

logger = logging.getLogger(__name__)

MAX_PREVIEW_FILES = 50

# ---- 封面（api.lrc.cx：按 title/artist 查 Apple Music 曲库，301 跳到 mzstatic 图）----
COVER_API = "https://api.lrc.cx/cover"
COVER_TIMEOUT = 8  # 秒；接口查不到歌时会挂住不回，必须设超时降级
COVER_FAIL_TTL = 600  # 失败负缓存秒数：查不到的查询期间内直接 404，不反复外呼空等
COVER_CONCURRENCY = 4  # 与试听槽（并发 1）分开限流：列表页几十行同时要封面
COVER_MAX_BYTES = 5 * 1024 * 1024

# 去扩展名用的音频后缀正则：由 domain.AUDIO_EXTS 生成，不手写第二份列表
_AUDIO_EXT_RE = re.compile(
    r"\.(" + "|".join(sorted(e.lstrip(".") for e in AUDIO_EXTS)) + r")$", re.IGNORECASE
)


def _clean_title(title: str | None) -> str:
    """封面查询用的歌名：document 音频的 title 是文件名，去掉扩展名与首尾空白。"""
    return _AUDIO_EXT_RE.sub("", (title or "").strip())


def _is_image(data: bytes) -> bool:
    """魔数嗅探（JPEG/PNG/GIF/WEBP），挡住接口偶发返回的错误页文本。

    判据与标签封面读取共用 tags.image_media_type（同一套魔数，避免两处漂移）。
    """
    return image_media_type(data) is not None


class _SmallCoverRedirect(HTTPRedirectHandler):
    """mzstatic 图床的 URL 以 ``/{宽}x{高}bb.jpg`` 结尾且支持任意尺寸改写。

    lrc.cx 默认跳 3000×3000 原图（约 2MB/张），行首/播放器小图 300px 足够，
    体积差 50 倍；URL 不匹配该模式时按原样跟随（接口换源也不受影响）。
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return super().redirect_request(req, fp, code, msg, headers, _SMALL_COVER_RE.sub(
            "/300x300bb.jpg", newurl
        ))


_SMALL_COVER_RE = re.compile(r"/\d+x\d+(?:bb)?\.jpg(?:\?.*)?$")

_OPENER = build_opener(_SmallCoverRedirect())


def _fetch_cover(title: str, artist: str) -> bytes | None:
    """同步抓取封面字节（to_thread 里跑）；跟随 301（跳转时改写为 300px 小图）。"""
    params = urlencode({"title": title, "artist": artist})
    req = Request(f"{COVER_API}?{params}", headers={"User-Agent": "Mozilla/5.0"})  # noqa: S310
    with _OPENER.open(req, timeout=COVER_TIMEOUT) as resp:  # noqa: S310
        data = resp.read(COVER_MAX_BYTES)
    return data if _is_image(data) else None


class PreviewService:
    """试听缓存（LRU）与预览槽（FR-PLAY-02/03）。"""

    def __init__(
        self,
        store: IStore,
        client: TelegramClientProto | None,
        events: EventBus,
        preview_dir: Path,
        max_bytes: int = 512 * 1024 * 1024,
    ) -> None:
        self.store = store
        # 装配期可为 None（容器先建服务、组合根再注入真实代理）；使用前必须已注入
        self._client = client
        self.events = events
        self.preview_dir = preview_dir
        self.max_bytes = max_bytes
        self._sem = asyncio.Semaphore(1)
        self._cover_sem = asyncio.Semaphore(COVER_CONCURRENCY)
        self._cover_failures: dict[str, float] = {}

    @property
    def client(self) -> TelegramClientProto:
        """Telegram 客户端；未注入即属装配错误（组合根负责先注入再用）。"""
        if self._client is None:
            raise AppError("not_connected", "telegram 客户端未注入：请先完成初始化登录")
        return self._client

    @client.setter
    def client(self, value: TelegramClientProto | None) -> None:
        self._client = value

    async def request_preview(
        self, chat_id: int, message_id: int, file_size: int | None = None
    ) -> int:
        """请求试听：缓存命中直接返回 preview_id；未命中占预览槽下载（SDD §2.4）。"""
        hit = self.store.get_preview(chat_id, message_id)
        if hit is not None and await asyncio.to_thread(self._cache_valid, hit, file_size):
            preview_id = hit.id
            if preview_id is not None:
                self.store.touch_preview(preview_id)
                return preview_id
            return await self._download_preview(chat_id, message_id, file_size)

        async with self._sem:
            return await self._download_preview(chat_id, message_id, file_size)

    def _cache_valid(self, hit: PreviewCache, file_size: int | None) -> bool:
        """缓存记录命中且文件完整（SDD §2.4）。"""
        p = Path(hit.file_path)
        if not p.exists():
            return False
        return p.stat().st_size == (file_size or hit.file_size)

    async def _download_preview(self, chat_id: int, message_id: int, file_size: int | None) -> int:
        self.preview_dir.mkdir(parents=True, exist_ok=True)
        temp_path = self.preview_dir / f"preview_{chat_id}_{message_id}.tmp"
        try:
            # download_media 是 async 协议方法；直接 await
            await self.client.download_media(
                {"chat_id": chat_id, "message_id": message_id},
                str(temp_path),
            )
        except Exception as e:
            if temp_path.exists():
                temp_path.unlink()
            raise AppError("preview_failed", f"preview download failed: {e}") from e
        actual = temp_path.stat().st_size
        if file_size is not None and actual != file_size:
            temp_path.unlink()
            raise AppError("preview_failed", f"preview size mismatch: {actual} != {file_size}")
        final = self.preview_dir / f"preview_{chat_id}_{message_id}{temp_path.suffix}"
        temp_path.replace(final)  # 原子替换（NFR-01 同款 temp → 目标）
        preview_id = self.store.put_preview(
            PreviewCache(
                id=None,
                chat_id=chat_id,
                message_id=message_id,
                file_path=str(final),
                file_size=actual,
                last_access_at=utcnow(),
            )
        )
        await self.events.publish(Event("preview.ready", {"preview_id": preview_id}))
        await asyncio.to_thread(self._evict_lru)
        return preview_id

    def _evict_lru(self) -> None:
        """LRU 淘汰：总量超 max_bytes 或条数 > 50，按 last_access_at 删（SDD §2.4）。"""
        while True:
            total, cnt = self.store.preview_totals()
            if total <= self.max_bytes and cnt <= MAX_PREVIEW_FILES:
                break
            previews = self.store.list_previews_by_access()
            if not previews:
                break
            victim = previews[0]
            p = Path(victim.file_path)
            if p.exists():
                p.unlink()
            self.store.delete_preview(victim.id)  # type: ignore[arg-type]
            logger.info("preview evicted id=%s path=%s", victim.id, victim.file_path)

    def stream_path(self, preview_id: int) -> Path:
        """返回试听文件路径；路由层只接受 id，禁止 ?path=（NFR-02）。"""
        for p in self.store.list_previews_by_access():
            if p.id == preview_id:
                path = Path(p.file_path)
                if path.exists():
                    self.store.touch_preview(preview_id)
                    return path
        raise AppError("not_found", f"preview {preview_id} not found")

    async def cover_path(self, title: str | None, artist: str | None) -> Path | None:
        """按「歌名 + 歌手」从公共封面接口取封面：缓存命中直接返回，取不到返回 None。

        缓存文件名是查询串的 md5——搜索行、播放器、飞片动画对同一首歌共用同一份
        文件；失败的查询负缓存 COVER_FAIL_TTL 秒（接口查不到时会空等超时，不值得
        反复试）。与试听共用 preview 目录，但不进 preview_cache 表——封面是纯装饰，
        丢了就重新抓，不值得占一条记录。
        """
        q_title = _clean_title(title)
        q_artist = (artist or "").strip()
        if not q_title and not q_artist:
            return None
        key = f"{q_title}|{q_artist}"
        digest = hashlib.md5(key.encode()).hexdigest()  # noqa: S324  缓存键，非安全用途
        final = self.preview_dir / f"cover_{digest}.jpg"
        if final.exists():
            return final
        failed_at = self._cover_failures.get(key)
        if failed_at is not None and time.monotonic() - failed_at < COVER_FAIL_TTL:
            return None
        async with self._cover_sem:
            if final.exists():  # 排队期间别的请求已取回
                return final
            data: bytes | None = None
            try:
                data = await asyncio.to_thread(_fetch_cover, q_title, q_artist)
            except Exception as e:  # noqa: BLE001  封面是装饰，失败负缓存后 404
                logger.info("cover fetch skipped title=%r artist=%r: %s", q_title, q_artist, e)
            if not data:
                logger.info("cover not found title=%r artist=%r", q_title, q_artist)
                self._cover_failures[key] = time.monotonic()
                return None
            self.preview_dir.mkdir(parents=True, exist_ok=True)
            self._cover_failures.pop(key, None)
            temp_path = self.preview_dir / f"cover_{digest}_{uuid4().hex[:8]}.tmp"
            temp_path.write_bytes(data)
            temp_path.replace(final)
            return final
