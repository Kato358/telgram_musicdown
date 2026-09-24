"""下载队列引擎（FR-DL-01~06，SDD §2.3）。

状态机：
``queued → downloading → 校验大小 → 写标签 → success``；
``downloading → paused → queued``；``failed → 自动重试(≤3, 指数退避) → queued``；
``queued → cancelled``；入队去重命中 → ``skipped``。

关键点：
- 并发：asyncio.Semaphore（max_download_task，默认 3）。
- 入队同时建 history 行（status=queued）并把 id 挂到 tasks.history_id：历史页、恢复流程
  与去重都以它为准（SDD §2.3/§3.2）。
- 元数据走 TrackMeta 单一契约（SDD §2.2）：渲染路径、写 history、写标签共用一份。
- 完整性：os.path.getsize(temp) == file_size 必须一致才 os.replace（NFR-01）。
- 去重在入队时做（FR-DL-05）；「强制重新下载」绕过检查。
- 重试计数存 tasks 表；指数退避 min(2^n * 30s, 1h)。
- 依赖注入 TelegramClient 协议（FakeUserClient 可替换，NFR-07）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING, Any, Protocol

from app.db.models import History, Task
from app.db.store import utcnow
from app.domain import TemplateConfig, TrackMeta, card_to_meta, message_to_card, meta_from_dict
from app.errors import AppError, TaskNotFoundError
from app.events import Event, EventBus
from app.services.path_builder import render_path, resolve_conflict
from app.services.tags import TagService

if TYPE_CHECKING:
    from app.db.store import Store

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_BASE_SEC = 30
RETRY_CAP_SEC = 3600
PROGRESS_SSE_INTERVAL_SEC = 0.5
PROGRESS_DB_INTERVAL_SEC = 2.0


def backoff_sec(retry_count: int) -> int:
    """指数退避 min(2^n * 30s, 1h)。"""
    return int(min(2**retry_count * RETRY_BASE_SEC, RETRY_CAP_SEC))


class TelegramClientProto(Protocol):
    """下载所需协议面（FakeUserClient 实现，NFR-07）。"""

    def download_media(
        self,
        message_ref: dict[str, Any],
        file_name: str,
        progress: Callable[[int, int], None] | None = None,
    ) -> Any: ...

    def get_messages(self, chat_id: int, message_ids: list[int]) -> Any: ...


class SyncRunnerProto(Protocol):
    """源同步执行器协议面（FR-SRC-04，实现在 services/sync.py）。"""

    async def run(self, task_id: int, payload: dict[str, Any]) -> Any: ...


@dataclass(slots=True)
class DownloadRequest:
    """单条下载请求（payload_json 的结构化形式）。"""

    meta: TrackMeta
    source_id: int | None = None
    force: bool = False



class _ProgressReporter:
    """把 Pyrogram 同步进度回调转换成节流的 SSE 与 DB 更新。"""

    def __init__(
        self, service: DownloadService, task_id: int, total_bytes: int | None
    ) -> None:
        self._service = service
        self._task_id = task_id
        self._total_bytes = total_bytes
        self._current_bytes = 0
        self._speed: float | None = None
        self._last_sample_at = monotonic()
        self._last_sample_bytes = 0
        self._last_emit_at = 0.0
        self._last_emit_bytes = 0
        self._last_db_at = 0.0
        self._loop = asyncio.get_running_loop()

    def __call__(self, current: int, total: int) -> None:
        if (
            self._task_id in self._service._paused_tasks
            or self._task_id in self._service._cancelled_tasks
        ):
            return
        try:
            current_bytes = max(0, int(current))
            total_bytes = int(total)
        except (TypeError, ValueError):
            return
        if total_bytes > 0:
            self._total_bytes = total_bytes
        if self._total_bytes is not None:
            current_bytes = min(current_bytes, self._total_bytes)

        now = monotonic()
        elapsed = now - self._last_sample_at
        if elapsed > 0:
            delta = current_bytes - self._last_sample_bytes
            if delta >= 0:
                self._speed = delta / elapsed
            else:
                self._speed = None
        self._last_sample_at = now
        self._last_sample_bytes = current_bytes
        self._current_bytes = current_bytes

        final = self._total_bytes is not None and current_bytes >= self._total_bytes
        first = self._last_emit_at == 0.0
        if (
            not first
            and not final
            and now - self._last_emit_at < PROGRESS_SSE_INTERVAL_SEC
            and current_bytes - self._last_emit_bytes < 256 * 1024
        ):
            return
        self._last_emit_at = now
        self._last_emit_bytes = current_bytes

        # 最终帧在 async flush 中 await 发布，保证随后 task.status 的顺序。
        if not final:
            self._service.events.publish_nowait(
                Event("task.progress", self._payload())
            )

        if final or now - self._last_db_at >= PROGRESS_DB_INTERVAL_SEC:
            self._last_db_at = now
            self._schedule_persist()

    def _payload(self) -> dict[str, object]:
        eta: int | None = None
        if self._total_bytes is not None and self._speed and self._speed > 0:
            eta = max(0, int((self._total_bytes - self._current_bytes) / self._speed))
        return {
            "task_id": self._task_id,
            "progress_bytes": self._current_bytes,
            "total_bytes": self._total_bytes,
            "speed": self._speed,
            "eta": eta,
        }

    def _schedule_persist(self) -> None:
        def persist() -> None:
            self._service.store.update_task(
                self._task_id,
                progress_bytes=self._current_bytes,
                total_bytes=self._total_bytes,
                speed=self._speed or 0.0,
            )

        try:
            self._loop.call_soon_threadsafe(persist)
        except RuntimeError:
            logger.debug("task %s progress persistence skipped: loop closed", self._task_id)

    async def flush(self) -> None:
        cancelled = (
            self._task_id in self._service._paused_tasks
            or self._task_id in self._service._cancelled_tasks
        )
        self._service.store.update_task(
            self._task_id,
            progress_bytes=self._current_bytes,
            total_bytes=self._total_bytes,
            speed=self._speed or 0.0,
        )
        if not cancelled:
            await self._service.events.publish(Event("task.progress", self._payload()))

class DownloadService:
    """下载队列：入队去重、worker 池、状态机、重试（FR-DL-01~06）。"""

    def __init__(
        self,
        store: Store,
        client: TelegramClientProto,
        events: EventBus,
        temp_dir: Path,
        cfg: TemplateConfig,
        max_concurrent: int = 3,
    ) -> None:
        self.store = store
        self.client = client
        self.events = events
        self.temp_dir = temp_dir
        self.cfg = cfg
        self.max_concurrent = max_concurrent
        self._sem = asyncio.Semaphore(max_concurrent)
        self._paused = asyncio.Event()
        self._paused.set()
        self._worker_tasks: list[asyncio.Task[None]] = []
        self._paused_tasks: set[int] = set()
        self._cancelled_tasks: set[int] = set()
        self._running_tasks: dict[int, asyncio.Task[Any]] = {}
        self.tags = TagService()
        self.sync_runner: SyncRunnerProto | None = None

    def set_sync_runner(self, runner: SyncRunnerProto) -> None:
        """装配源同步执行器（FR-SRC-04）：tasks.type='sync' 由本 Worker 池执行。"""
        self.sync_runner = runner

    async def enqueue(self, req: DownloadRequest) -> int | None:
        """入队：去重检查（FR-DL-05）→ 建 history + task（FR-DL-01）；命中返回 None。"""
        meta = req.meta
        hit = self.store.find_history_success(meta.chat_id, meta.message_id, meta.unique_id)
        if hit and not req.force:
            logger.info("dedupe hit chat=%s msg=%s", meta.chat_id, meta.message_id)
            return None
        history_id = self.store.upsert_history(_history_row(meta, req.source_id))
        payload = {
            "meta": asdict(meta),
            "source_id": req.source_id,
            "force": req.force,
        }
        task_id = self.store.create_task(
            Task(
                id=None,
                type="link",
                payload_json=json.dumps(payload, ensure_ascii=False),
                history_id=history_id,
            )
        )
        await self.events.publish(
            Event("task.status", {"task_id": task_id, "status": "queued", "error": None})
        )
        return task_id

    async def start_workers(self, count: int | None = None) -> None:
        """启动 worker 池。"""
        n = count or self.max_concurrent
        for i in range(n):
            t = asyncio.create_task(self._worker(f"worker-{i}"))
            self._worker_tasks.append(t)

    async def stop_workers(self) -> None:
        for t in self._worker_tasks:
            t.cancel()
        await asyncio.gather(*self._worker_tasks, return_exceptions=True)
        self._worker_tasks.clear()

    async def pause_task(self, task_id: int) -> None:
        """用户暂停：取消当前协程，保留 temp 分片（SDD §2.3）。"""
        self._require_task(task_id)
        self._paused_tasks.add(task_id)
        self.store.update_task(task_id, status="paused")
        running = self._running_tasks.get(task_id)
        if running is not None and running is not asyncio.current_task():
            running.cancel()
        await self.events.publish(
            Event("task.status", {"task_id": task_id, "status": "paused", "error": None})
        )

    async def resume_task(self, task_id: int) -> None:
        """恢复：重新入队 queued。"""
        self._require_task(task_id)
        self._paused_tasks.discard(task_id)
        self._cancelled_tasks.discard(task_id)
        self.store.update_task(task_id, status="queued")
        await self.events.publish(
            Event("task.status", {"task_id": task_id, "status": "queued", "error": None})
        )

    async def cancel_task(self, task_id: int) -> None:
        self._require_task(task_id)
        self._paused_tasks.discard(task_id)
        self._cancelled_tasks.add(task_id)
        self.store.update_task(task_id, status="cancelled")
        running = self._running_tasks.get(task_id)
        if running is not None and running is not asyncio.current_task():
            running.cancel()
        await self.events.publish(
            Event("task.status", {"task_id": task_id, "status": "cancelled", "error": None})
        )

    def _require_task(self, task_id: int) -> Task:
        task = self.store.get_task(task_id)
        if task is None:
            raise TaskNotFoundError("任务不存在")
        return task

    async def retry_task(self, task_id: int) -> None:
        """把一条失败、取消或跳过的任务重新放回队列。"""
        task = self._require_task(task_id)
        if task.status not in {"failed", "cancelled", "skipped"}:
            raise AppError("invalid_task_state", "任务当前不可重试")
        self._paused_tasks.discard(task_id)
        self._cancelled_tasks.discard(task_id)
        temp_path = self._temp_path_for_task(task)
        if temp_path is not None:
            await asyncio.to_thread(self._discard_temp, temp_path)
        self.store.reset_task_for_retry(task_id)
        if task.history_id is not None:
            self.store.reset_history_for_retry(task.history_id)
        await self.events.publish(
            Event("task.status", {"task_id": task_id, "status": "queued", "error": None})
        )

    async def delete_task(self, task_id: int) -> None:
        """删除任务记录和临时分片，保留 history 及已落盘文件。"""
        task = self._require_task(task_id)
        if task.status == "downloading":
            await self.cancel_task(task_id)
        temp_path = self._temp_path_for_task(task)
        if temp_path is not None:
            await asyncio.to_thread(self._discard_temp, temp_path)
        if not self.store.delete_task(task_id):
            raise TaskNotFoundError("任务不存在")

    async def retry_failed(self) -> int:
        """手动重试失败项（FR-DL-04）。"""
        failed = self.store.list_tasks(status="failed", limit=500)
        count = 0
        for t in failed:
            if t.id is not None:
                await self.retry_task(t.id)
                count += 1
        return count

    # ---- worker ----

    async def _worker(self, name: str) -> None:
        logger.info("%s started (concurrency=%d)", name, self.max_concurrent)
        while True:
            await self._paused.wait()
            task = self._next_queued()
            if task is None:
                await asyncio.sleep(0.5)
                continue
            task_id = int(task["id"])
            current = asyncio.current_task()
            if current is not None:
                self._running_tasks[task_id] = current
            try:
                await self._run_task(task)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # NFR-08：失败任务写人类可读原因到 tasks/history 再返回
                reason = f"task crashed: {e}"
                self.store.update_task(task_id, status="failed", error=reason)
                history_id = task.get("history_id")
                if history_id:
                    self.store.mark_history_status(history_id, "failed", error=reason)
                logger.exception("worker %s crashed on task %s", name, task_id)
            finally:
                if current is not None and self._running_tasks.get(task_id) is current:
                    self._running_tasks.pop(task_id, None)
                self._cancelled_tasks.discard(task_id)

    def _next_queued(self) -> dict[str, Any] | None:
        row = self.store._conn.execute(
            "SELECT * FROM tasks WHERE status='queued'"
            " AND (next_retry_at IS NULL OR next_retry_at <= ?)"
            " ORDER BY id LIMIT 1",
            (utcnow(),),
        ).fetchone()
        if row is None:
            return None
        task = {k: row[k] for k in row.keys()}  # noqa: SIM118  sqlite3.Row
        self.store.update_task(task["id"], status="downloading")
        return task

    async def _run_task(self, task: dict[str, Any]) -> None:
        """按任务类型分发：sync 走源同步执行器，其余是单条下载（SDD §2.3）。"""
        if task["type"] == "sync":
            await self._run_sync(task)
            return
        payload = json.loads(task["payload_json"])
        task_id: int = task["id"]
        history_id: int | None = task.get("history_id")
        meta = _meta_from_payload(payload)
        # bot 链接/转发入队只有 chat_id/message_id：下载前取一次消息补全 meta
        #（ext 决定落盘扩展名与标签容器，file_size 决定完整性校验）。
        if meta.ext is None and meta.file_size is None and meta.title is None:
            meta = await self._hydrate_meta(meta)
        if history_id is not None:
            self._sync_history_display(history_id, meta)
        chat_id: int = meta.chat_id
        message_id: int = meta.message_id
        expected_size: int | None = meta.file_size
        reporter = _ProgressReporter(self, task_id, expected_size)

        async with self._sem:
            if task_id in self._paused_tasks or task_id in self._cancelled_tasks:
                return
            temp_path = self.temp_dir / f"task_{task_id}_{message_id}"
            self.temp_dir.mkdir(parents=True, exist_ok=True)
            temp_str = str(temp_path)
            await reporter.flush()
            try:
                # download_media 是 async 协议方法；同步 progress 回调由客户端线程池调用。
                await self.client.download_media(
                    {"chat_id": chat_id, "message_id": message_id},
                    temp_str,
                    progress=reporter,
                )
            except asyncio.CancelledError:
                if task_id in self._paused_tasks or task_id in self._cancelled_tasks:
                    return  # 暂停/取消：不标 failed，保留分片由后续动作处理
                raise
            finally:
                await reporter.flush()

            if not await self._validate_download_size(
                task_id, history_id, temp_path, expected_size
            ):
                return  # NFR-01：损坏文件不落 save_path
            await self._complete_download(
                task_id, history_id, temp_path, expected_size, payload, meta
            )

    async def _validate_download_size(
        self,
        task_id: int,
        history_id: int | None,
        temp_path: Path,
        expected_size: int | None,
    ) -> bool:
        """校验下载分片大小；失败时落库并发布失败状态。"""
        if expected_size is None:
            return True
        actual = await asyncio.to_thread(self._file_size_or_missing, temp_path)
        if actual == expected_size:
            return True
        await asyncio.to_thread(self._discard_temp, temp_path)
        reason = f"size mismatch: got {actual}, expected {expected_size}"
        self.store.update_task(task_id, status="failed", error=reason)
        if history_id:
            self.store.mark_history_status(history_id, "failed", error=reason)
        await self.events.publish(
            Event(
                "task.status",
                {"task_id": task_id, "status": "failed", "error": reason},
            )
        )
        return False

    async def _complete_download(
        self,
        task_id: int,
        history_id: int | None,
        temp_path: Path,
        expected_size: int | None,
        payload: dict[str, Any],
        meta: TrackMeta,
    ) -> None:
        """将已校验分片落盘，处理去重、写标签并发布完成状态。"""
        rendered = render_path(meta, self.cfg)
        target, dedupe_hit = resolve_conflict(Path(rendered), expected_size=expected_size)
        if dedupe_hit:
            self.store.update_task(task_id, status="skipped")
            if history_id:
                self.store.mark_history_status(history_id, "skipped", finished=True)
            await asyncio.to_thread(self._discard_temp, temp_path)
            await self.events.publish(
                Event("task.status", {"task_id": task_id, "status": "skipped", "error": None})
            )
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(os.replace, temp_path, target)

        # 写标签（FR-META-01，失败仅日志不影响状态）
        if history_id:
            await asyncio.to_thread(self._finish_history, history_id, str(target), payload, meta)
        self.store.update_task(task_id, status="success")
        await self.events.publish(
            Event("task.status", {"task_id": task_id, "status": "success", "error": None})
        )

    @staticmethod
    def _file_size_or_missing(path: Path) -> int:
        return path.stat().st_size if path.exists() else -1

    async def _hydrate_meta(self, meta: TrackMeta) -> TrackMeta:
        """meta 只有 chat_id/message_id 时（bot 链接/转发入队），取消息补全元数据。

        取不到消息或非音频 → 原样返回，行为与之前一致（message_{id} 回退）。
        """
        try:
            msgs = await self.client.get_messages(meta.chat_id, [meta.message_id])
        except AppError:
            return meta
        msg = (msgs if isinstance(msgs, list) else [msgs])[0]
        if msg is None:
            return meta
        card = message_to_card(msg, channel_title=meta.channel_title)
        hydrated = card_to_meta(card)
        if hydrated.ext is None and hydrated.mime:
            hydrated.ext = {"audio/flac": "flac", "audio/mpeg": "mp3"}.get(hydrated.mime)
        hydrated.track = meta.track
        hydrated.album = meta.album or hydrated.album
        return hydrated

    async def _run_sync(self, task: dict[str, Any]) -> None:
        """源同步/回溯（FR-SRC-04）：交给 SyncRunner，本处只做状态与错误落库。"""
        task_id: int = task["id"]
        if self.sync_runner is None:
            reason = "sync runner not wired"
            self.store.update_task(task_id, status="failed", error=reason)
            await self.events.publish(
                Event("task.status", {"task_id": task_id, "status": "failed", "error": reason})
            )
            return
        payload = json.loads(task["payload_json"])
        try:
            await self.sync_runner.run(task_id, payload)  # 扫描计数由 SyncRunner 记日志
        except AppError as e:  # 领域错误：写人类可读原因（NFR-08）
            self.store.update_task(task_id, status="failed", error=e.message)
            await self.events.publish(
                Event("task.status", {"task_id": task_id, "status": "failed", "error": e.message})
            )
            return
        self.store.update_task(task_id, status="success")
        await self.events.publish(
            Event("task.status", {"task_id": task_id, "status": "success", "error": None})
        )

    def _discard_temp(self, temp_path: Path) -> None:
        if temp_path.exists():
            temp_path.unlink()

    def _temp_path_for_task(self, task: Task) -> Path | None:
        try:
            payload: object = json.loads(task.payload_json)
            if not isinstance(payload, dict):
                return None
            meta: object = payload.get("meta")
            if not isinstance(meta, dict):
                return None
            raw_message_id: object = meta.get("message_id")
            if not isinstance(raw_message_id, (int, str)):
                return None
            message_id = int(raw_message_id)
        except (TypeError, ValueError, AttributeError):
            return None
        if task.id is None or message_id <= 0:
            return None
        return self.temp_dir / f"task_{task.id}_{message_id}"

    def _sync_history_display(self, history_id: int, meta: TrackMeta) -> None:
        if not (meta.title or meta.artist or meta.album):
            return
        history = self.store.get_history(history_id)
        if history is None:
            return
        self.store.set_history_display_tags(
            history_id,
            meta.title or history.title,
            meta.artist or history.artist,
            meta.album or history.album,
        )

    def _finish_history(
        self, history_id: int, save_path: str, payload: dict[str, Any], meta: TrackMeta
    ) -> None:
        self.store.mark_history_status(history_id, "success", save_path=save_path, finished=True)
        self._sync_history_display(history_id, meta)
        if payload.get("write_tags", True):
            try:
                self.tags.write_tags(Path(save_path), meta)
            except Exception:
                logger.exception("tag write failed for %s (task continues)", save_path)


def _history_row(meta: TrackMeta, source_id: int | None) -> History:
    """入队即建 history 行（status=queued）：历史页与恢复流程都以它为准。"""
    return History(
        id=None,
        chat_id=meta.chat_id,
        message_id=meta.message_id,
        source_id=source_id,
        file_unique_id=meta.unique_id,
        title=meta.title,
        artist=meta.artist,
        album=meta.album,
        duration_sec=meta.duration_sec,
        file_size=meta.file_size,
        mime=meta.mime,
        ext=meta.ext,
        caption=meta.caption,
        message_date=meta.message_date,
        status="queued",
    )


def _meta_from_payload(payload: dict[str, Any]) -> TrackMeta:
    """payload['meta'] → TrackMeta；缺失字段走回退链（FR-NAME-02）。"""
    return meta_from_dict(payload.get("meta") or {})
