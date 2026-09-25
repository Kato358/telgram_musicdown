"""试听（FR-PLAY-01~03，SDD §2.4）。

- 预览槽独立于下载队列（并发 1，FR-PLAY-03）。
- 试听不入队、不写 save_path、不写 success 历史（FR-PLAY-02）。
- LRU 淘汰：总量 > preview_cache_max_bytes（默认 512MB）或条数 > 50。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from app.db.models import PreviewCache
from app.db.store import utcnow
from app.errors import AppError
from app.events import Event, EventBus
from app.ports import IStore
from app.ports.telegram import TelegramClientProto

logger = logging.getLogger(__name__)

MAX_PREVIEW_FILES = 50


class PreviewBusyError(AppError):
    """预览槽被占用（并发 1）。"""

    def __init__(self) -> None:
        super().__init__("preview_busy", "another preview is running")


class PreviewService:
    """试听缓存（LRU）与预览槽（FR-PLAY-02/03）。"""

    def __init__(
        self,
        store: IStore,
        client: TelegramClientProto,
        events: EventBus,
        preview_dir: Path,
        max_bytes: int = 512 * 1024 * 1024,
    ) -> None:
        self.store = store
        self.client = client
        self.events = events
        self.preview_dir = preview_dir
        self.max_bytes = max_bytes
        self._sem = asyncio.Semaphore(1)

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

    async def thumb_path(self, chat_id: int, message_id: int) -> Path | None:
        """消息封面（Telegram 内嵌缩略图）：缓存命中直接返回；没有封面返回 None。

        与试听共用 preview 目录（LRU 淘汰同款规则），但不进 preview_cache 表——
        封面是纯装饰，丢了就重新下载，不值得占一条记录。
        """
        final = self.preview_dir / f"thumb_{chat_id}_{message_id}.jpg"
        if final.exists():
            return final
        async with self._sem:
            if final.exists():  # 并发等待期间别人已下好
                return final
            self.preview_dir.mkdir(parents=True, exist_ok=True)
            temp_path = self.preview_dir / f"thumb_{chat_id}_{message_id}.tmp"
            try:
                await self.client.download_thumb(
                    {"chat_id": chat_id, "message_id": message_id}, str(temp_path)
                )
            except Exception as e:
                if temp_path.exists():
                    temp_path.unlink()
                logger.info("thumb download skipped chat=%s msg=%s: %s", chat_id, message_id, e)
                return None
            if not temp_path.exists():
                return None
            temp_path.replace(final)
            return final
