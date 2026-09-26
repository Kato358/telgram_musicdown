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
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from app.db.models import Task
from app.domain import (
    DownloadRequest,
    TemplateConfig,
    TrackMeta,
    card_to_meta,
    message_to_card,
    meta_from_dict,
)
from app.errors import AppError, TaskNotFoundError
from app.events import Event, EventBus
from app.ports import IStore
from app.ports.telegram import TelegramClientProto
from app.services.history_writer import HistoryWriter, history_row
from app.services.path_builder import render_path, resolve_conflict
from app.services.progress import ProgressReporter, TaskRunState
from app.services.tags import TagService

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_BASE_SEC = 30
RETRY_CAP_SEC = 3600


def backoff_sec(retry_count: int) -> int:
    """指数退避 min(2^n * 30s, 1h)。"""
    return int(min(2**retry_count * RETRY_BASE_SEC, RETRY_CAP_SEC))


def _utc_after(seconds: int) -> str:
    """now + seconds 的 UTC ISO-8601 文本（与 store 的时间戳约定一致）。"""
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat(timespec="seconds")


class DownloadQueueServiceProto(Protocol):
    """下载队列对外协议面（bot 层依赖它，不依赖 DownloadService 具体类）。"""

    store: IStore

    async def enqueue(self, req: DownloadRequest) -> int | None: ...
    async def cancel_task(self, task_id: int) -> None: ...


class DownloadService:
    """下载队列：入队去重、worker 池、状态机、重试（FR-DL-01~06）。"""

    def __init__(
        self,
        store: IStore,
        client: TelegramClientProto | None,
        events: EventBus,
        temp_dir: Path,
        cfg: TemplateConfig,
        max_concurrent: int = 3,
    ) -> None:
        self.store = store
        # 装配期可为 None（容器先建服务、组合根再注入真实代理）；使用前必须已注入
        self._client = client
        self.events = events
        self.temp_dir = temp_dir
        self.cfg = cfg
        self.max_concurrent = max_concurrent
        self._sem = asyncio.Semaphore(max_concurrent)
        self._worker_tasks: list[asyncio.Task[None]] = []
        # 运行态（暂停/取消标记 + 在跑协程）：与进度回调共用同一份事实源
        self._run_state = TaskRunState()
        self.tags = TagService()
        # 历史行结算（展示字段/读数/终态）独立成协作对象：与队列调度无关
        self._history = HistoryWriter(store, self.tags, cfg)

    @property
    def client(self) -> TelegramClientProto:
        """Telegram 客户端；未注入即属装配错误（组合根负责先注入再用）。"""
        if self._client is None:
            raise AppError("not_connected", "telegram 客户端未注入：请先完成初始化登录")
        return self._client

    @client.setter
    def client(self, value: TelegramClientProto | None) -> None:
        self._client = value

    def apply_template(self, cfg: TemplateConfig) -> None:
        """运行中替换落盘模板（FR-CFG-03：设置保存即时生效，不必重启）。

        只覆盖模板与落盘根；下载并发数绑定 worker 池规模，改动仍在下次启动生效。
        """
        self.cfg = cfg
        self._history.apply_template(cfg)

    async def enqueue(self, req: DownloadRequest) -> int | None:
        """入队：去重检查（FR-DL-05）→ 建 history + task（FR-DL-01）；命中返回 None。"""
        meta = req.meta
        hit = self.store.find_history_success(meta.chat_id, meta.message_id, meta.unique_id)
        if hit and not req.force:
            logger.info("dedupe hit chat=%s msg=%s", meta.chat_id, meta.message_id)
            return None
        history_id = self.store.upsert_history(history_row(meta, req.source_id))
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
        await self._set_status(task_id, "queued", history_id)
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
        task = self._require_task(task_id)
        self._run_state.mark_paused(task_id)
        running = self._run_state.running_task(task_id)
        if running is not None and running is not asyncio.current_task():
            running.cancel()
        await self._set_status(task_id, "paused", task.history_id)

    async def resume_task(self, task_id: int) -> None:
        """恢复：重新入队 queued。"""
        task = self._require_task(task_id)
        self._run_state.clear(task_id)
        await self._set_status(task_id, "queued", task.history_id)

    async def cancel_task(self, task_id: int) -> None:
        task = self._require_task(task_id)
        self._run_state.mark_cancelled(task_id)
        running = self._run_state.running_task(task_id)
        if running is not None and running is not asyncio.current_task():
            running.cancel()
        await self._set_status(task_id, "cancelled", task.history_id)

    def _require_task(self, task_id: int) -> Task:
        task = self.store.get_task(task_id)
        if task is None:
            raise TaskNotFoundError("任务不存在")
        return task

    def _mirror_history_status(self, history_id: int | None, status: str) -> None:
        """把状态写回这条下载的历史行（下载页与状态筛选只认它，任务台账只补读数）。

        用户视角里「这条下载在干什么」只有一个答案，两处数据不能各写各的。
        """
        if history_id is not None:
            self.store.mark_history_status(history_id, status)

    async def _set_status(
        self,
        task_id: int,
        status: str,
        history_id: int | None = None,
        *,
        error: str | None = None,
        finished: bool = False,
    ) -> None:
        """任务状态变更的唯一出口：写台账 + 镜像历史行 + 发布 task.status 事件。

        这三步原先在 10 处各写一遍（且容易漏掉其中一步——历史行漏更新会让下载页
        永远显示旧状态），收口后「状态只有一个答案」这条不变量只需要维护一处。
        """
        self.store.update_task(task_id, status=status, error=error)
        if history_id is not None:
            self.store.mark_history_status(history_id, status, error=error, finished=finished)
        await self._publish_status(task_id, status, error)

    async def _publish_status(self, task_id: int, status: str, error: str | None = None) -> None:
        """仅发布 task.status 事件（状态已由别处写库时用，避免重复写）。"""
        await self.events.publish(
            Event("task.status", {"task_id": task_id, "status": status, "error": error})
        )

    async def retry_task(self, task_id: int) -> None:
        """把一条失败、取消或跳过的任务重新放回队列。"""
        task = self._require_task(task_id)
        if task.status not in {"failed", "cancelled", "skipped"}:
            raise AppError("invalid_task_state", "任务当前不可重试")
        self._run_state.clear(task_id)
        temp_path = self._temp_path_for_task(task)
        if temp_path is not None:
            await asyncio.to_thread(self._discard_temp, temp_path)
        self.store.reset_task_for_retry(task_id)
        if task.history_id is not None:
            self.store.reset_history_for_retry(task.history_id)
        await self._publish_status(task_id, "queued")

    async def delete_task(self, task_id: int) -> None:
        """删除任务台账记录和临时分片，保留 history 及已落盘文件。

        台账没了，这条下载就没有执行者：还把历史行留在非终态，列表里就会挂着一个
        永远不会动的「等待」，所以顺手把它结算成 `cancelled`。
        """
        task = self._require_task(task_id)
        if task.status == "downloading":
            await self.cancel_task(task_id)
        temp_path = self._temp_path_for_task(task)
        if temp_path is not None:
            await asyncio.to_thread(self._discard_temp, temp_path)
        if not self.store.delete_task(task_id):
            raise TaskNotFoundError("任务不存在")
        if task.status in {"queued", "downloading", "paused"}:
            self._mirror_history_status(task.history_id, "cancelled")

    async def retry_failed(self) -> int:
        """手动重试失败项（FR-DL-04）：以失败的历史行逐条重排。

        下载页的「失败项」就是这些行（统计卡、状态筛选与这个动作说的是同一件事），
        所以按行遍历：台账还在且可重试就复位它，台账没了就按消息重新入队。
        """
        rows = self.store.list_history(status="failed", limit=500)
        ids = [int(row.id) for row in rows if row.id is not None]
        task_ids = self.store.latest_task_ids(ids)
        count = 0
        for row in rows:
            task_id = task_ids.get(int(row.id or 0))
            task = self.store.get_task(task_id) if task_id is not None else None
            if task is not None and task.status in {"failed", "cancelled", "skipped"}:
                await self.retry_task(int(task.id or 0))
            else:
                await self.enqueue(
                    DownloadRequest(
                        meta=TrackMeta(chat_id=row.chat_id, message_id=row.message_id),
                        source_id=row.source_id,
                    )
                )
            count += 1
        return count

    # ---- worker ----

    async def _worker(self, name: str) -> None:
        logger.info("%s started (concurrency=%d)", name, self.max_concurrent)
        while True:
            task = self._next_queued()
            if task is None:
                await asyncio.sleep(0.5)
                continue
            task_id = int(task["id"])
            current = asyncio.current_task()
            if current is not None:
                self._run_state.register_running(task_id, current)
            try:
                await self._run_task(task)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # NFR-08：失败任务写人类可读原因到 tasks/history 再返回
                reason = f"task crashed: {e}"
                await self._fail_or_retry(task, reason)
                logger.exception("worker %s crashed on task %s", name, task_id)
            finally:
                if current is not None:
                    self._run_state.unregister_running(task_id, current)
                self._run_state.forget_cancelled(task_id)

    async def _fail_or_retry(self, task: dict[str, Any], reason: str) -> None:
        """失败结算（FR-DL-04）：未超上限则排入退避重试，超限则落 failed 终态。

        ``next_queued_task`` 只在 ``next_retry_at`` 到达后才取这条任务，因此退避期间
        它不会被重复消费；指数退避见 ``backoff_sec``。
        """
        task_id = int(task["id"])
        history_id = task.get("history_id")
        retry_count = int(task.get("retry_count") or 0)
        if retry_count < MAX_RETRIES:
            next_at = _utc_after(backoff_sec(retry_count))
            self.store.update_task(
                task_id,
                status="queued",
                retry_count=retry_count + 1,
                next_retry_at=next_at,
                error=reason,
            )
            self._mirror_history_status(history_id, "queued")
            logger.warning(
                "task %s failed (attempt %d/%d), retry at %s: %s",
                task_id,
                retry_count + 1,
                MAX_RETRIES,
                next_at,
                reason,
            )
            await self._publish_status(task_id, "queued", reason)
            return
        await self._set_status(task_id, "failed", history_id, error=reason)

    def _next_queued(self) -> dict[str, Any] | None:
        """取下一条可执行任务（FR-DL-01）：SQL 在仓储里，本层只补历史镜像。"""
        task = self.store.next_queued_task()
        if task is None:
            return None
        self._mirror_history_status(task.get("history_id"), "downloading")
        return task

    async def _run_task(self, task: dict[str, Any]) -> None:
        """执行一条下载任务（SDD §2.3）：台账里只有下载任务一种类型。"""
        if task["type"] != "link":
            # 老库里的残留（同步子系统已移除，v0.10）：结算成失败并留下原因，
            # 不当作下载去跑——它的 payload 里没有 meta，跑了只会以怪错误反复重试
            await self._set_status(
                task["id"],
                "failed",
                task.get("history_id"),
                error=f"任务类型已移除：{task['type']}",
            )
            return
        payload = json.loads(task["payload_json"])
        task_id: int = task["id"]
        history_id: int | None = task.get("history_id")
        meta = _meta_from_payload(payload)
        # bot 链接/转发入队只有 chat_id/message_id：下载前取一次消息补全 meta
        # （ext 决定落盘扩展名与标签容器，file_size 决定完整性校验）。
        if meta.ext is None and meta.file_size is None and meta.title is None:
            meta = await self._hydrate_meta(meta)
        if history_id is not None:
            self._history.sync_display(history_id, meta)
        chat_id: int = meta.chat_id
        message_id: int = meta.message_id
        expected_size: int | None = meta.file_size
        reporter = ProgressReporter(
            self.store, self.events, self._run_state, task_id, expected_size
        )

        async with self._sem:
            if self._run_state.is_stopped(task_id):
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
                if self._run_state.is_stopped(task_id):
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
        await self._set_status(task_id, "failed", history_id, error=reason)
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
            await asyncio.to_thread(self._discard_temp, temp_path)
            await self._set_status(task_id, "skipped", history_id, finished=True)
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(os.replace, temp_path, target)

        # 写标签（FR-META-01，失败仅日志不影响状态）；finish 同时把历史行
        # 结算成 success（带 save_path），故此处传 history_id=None 避免重复写。
        if history_id:
            await asyncio.to_thread(self._history.finish, history_id, str(target), payload, meta)
        await self._set_status(task_id, "success")

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

    def backfill_history_media(self, limit: int = 500) -> int:
        """启动回填（兼容入口）：委托 HistoryWriter。"""
        return self._history.backfill_media(limit)


def _meta_from_payload(payload: dict[str, Any]) -> TrackMeta:
    """payload['meta'] → TrackMeta；缺失字段走回退链（FR-NAME-02）。"""
    return meta_from_dict(payload.get("meta") or {})
