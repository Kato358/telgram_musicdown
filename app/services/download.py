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
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from app.db.models import History, Task
from app.db.store import utcnow
from app.domain import TemplateConfig, TrackMeta, meta_from_dict
from app.errors import AppError
from app.events import Event, EventBus
from app.services.path_builder import render_path, resolve_conflict
from app.services.tags import TagService

if TYPE_CHECKING:
    from app.db.store import Store

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_BASE_SEC = 30
RETRY_CAP_SEC = 3600


def backoff_sec(retry_count: int) -> int:
    """指数退避 min(2^n * 30s, 1h)。"""
    return int(min(2**retry_count * RETRY_BASE_SEC, RETRY_CAP_SEC))


class TelegramClientProto(Protocol):
    """下载所需协议面（FakeUserClient 实现，NFR-07）。"""

    def download_media(self, message_ref: dict[str, Any], file_name: str) -> Any: ...


class SyncRunnerProto(Protocol):
    """源同步执行器协议面（FR-SRC-04，实现在 services/sync.py）。"""

    async def run(self, task_id: int, payload: dict[str, Any]) -> Any: ...


@dataclass(slots=True)
class DownloadRequest:
    """单条下载请求（payload_json 的结构化形式）。"""

    meta: TrackMeta
    source_id: int | None = None
    force: bool = False


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
        self._paused_tasks.add(task_id)
        self.store.update_task(task_id, status="paused")
        await self.events.publish(
            Event("task.status", {"task_id": task_id, "status": "paused", "error": None})
        )

    async def resume_task(self, task_id: int) -> None:
        """恢复：重新入队 queued。"""
        self._paused_tasks.discard(task_id)
        self.store.update_task(task_id, status="queued")
        await self.events.publish(
            Event("task.status", {"task_id": task_id, "status": "queued", "error": None})
        )

    async def cancel_task(self, task_id: int) -> None:
        self._paused_tasks.discard(task_id)
        self.store.update_task(task_id, status="cancelled")
        await self.events.publish(
            Event("task.status", {"task_id": task_id, "status": "cancelled", "error": None})
        )

    async def retry_failed(self) -> int:
        """手动重试失败项（FR-DL-04）。"""
        failed = self.store.list_tasks(status="failed", limit=500)
        for t in failed:
            if t.id is not None:
                self.store.update_task(t.id, status="queued", error=None)
        return len(failed)

    # ---- worker ----

    async def _worker(self, name: str) -> None:
        logger.info("%s started (concurrency=%d)", name, self.max_concurrent)
        while True:
            await self._paused.wait()
            task = self._next_queued()
            if task is None:
                await asyncio.sleep(0.5)
                continue
            try:
                await self._run_task(task)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # NFR-08：失败任务写人类可读原因到 tasks/history 再返回
                reason = f"task crashed: {e}"
                self.store.update_task(task["id"], status="failed", error=reason)
                history_id = task.get("history_id")
                if history_id:
                    self.store.mark_history_status(history_id, "failed", error=reason)
                logger.exception("worker %s crashed on task %s", name, task["id"])

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
        """按任务类型分发：sync 走源同步执行器，其余是单条下载（SDD §2.3 状态机）。"""
        if task["type"] == "sync":
            await self._run_sync(task)
            return
        payload = json.loads(task["payload_json"])
        task_id: int = task["id"]
        history_id: int | None = task.get("history_id")
        meta = _meta_from_payload(payload)
        chat_id: int = meta.chat_id
        message_id: int = meta.message_id
        expected_size: int | None = meta.file_size

        async with self._sem:
            if task_id in self._paused_tasks:
                return  # paused：保留 temp 分片，等 resume
            temp_path = self.temp_dir / f"task_{task_id}_{message_id}"
            self.temp_dir.mkdir(parents=True, exist_ok=True)
            temp_str = str(temp_path)
            try:
                # download_media 是 async 协议方法；直接 await，不进 to_thread
                await self.client.download_media(
                    {"chat_id": chat_id, "message_id": message_id},
                    temp_str,
                )
            except asyncio.CancelledError:
                if task_id in self._paused_tasks:
                    return  # 暂停：不标 failed，保留分片
                raise

            # 校验大小（FR-DL-03 / NFR-01）：损坏文件不落 save_path
            if expected_size is not None:
                actual = temp_path.stat().st_size if temp_path.exists() else -1
                if actual != expected_size:
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
                    return  # NFR-01：损坏文件不落 save_path
            # 渲染路径 + 冲突处理
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
                await asyncio.to_thread(
                    self._finish_history, history_id, str(target), payload, meta
                )
            self.store.update_task(task_id, status="success")
            await self.events.publish(
                Event("task.status", {"task_id": task_id, "status": "success", "error": None})
            )

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

    def _finish_history(
        self, history_id: int, save_path: str, payload: dict[str, Any], meta: TrackMeta
    ) -> None:
        self.store.mark_history_status(history_id, "success", save_path=save_path, finished=True)
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
