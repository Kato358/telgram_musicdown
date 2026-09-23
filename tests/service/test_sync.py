"""服务层测试：源同步与历史回溯（FR-SRC-04）。

FakeUserClient 注入依赖，不 mock 被测对象（编码规范 §6 反模式表）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.db.models import Source, Task
from app.db.store import Store
from app.domain import TemplateConfig
from app.events import EventBus
from app.services.download import DownloadService
from app.services.sync import SyncRunner
from tests.fakes import (
    FakeUserClient,
    make_audio_document_message,
    make_audio_message,
    make_video_message,
)

SOURCE_ID = 1


@pytest.fixture()
def env(tmp_path: Path) -> tuple[SyncRunner, DownloadService, Store, FakeUserClient]:
    """源同步环境：一条源 + 假客户端 + 下载队列。"""
    store = Store(tmp_path / "app.db")
    messages = [
        make_audio_message(1),
        make_audio_message(2),
        make_audio_document_message(3),
        make_video_message(4),
        make_audio_message(5, voice=True),
    ]
    client = FakeUserClient(messages)
    events = EventBus()
    downloads = DownloadService(
        store, client, events, tmp_path / "temp", TemplateConfig(save_path=tmp_path / "library")
    )
    runner = SyncRunner(store, client, downloads)
    downloads.set_sync_runner(runner)
    store.upsert_source(
        Source(id=None, telegram_chat_id=-100123, title="Music Channel", type="channel")
    )
    return runner, downloads, store, client


def _task(store: Store, **payload: object) -> int:
    return store.create_task(
        Task(
            id=None,
            type="sync",
            payload_json=json.dumps({"source_id": SOURCE_ID, **payload}, ensure_ascii=False),
        )
    )


async def test_run_enqueues_audio_only_and_sets_cursor(
    env: tuple[SyncRunner, DownloadService, Store, FakeUserClient],
) -> None:
    # 媒体范围默认 audio + audio_document：mp3 与 flac document 入队，视频与 voice 不入队
    runner, downloads, store, _ = env
    task_id = _task(store, direction="backward")
    outcome = await runner.run(task_id, json.loads(store.get_task(task_id).payload_json))  # type: ignore[union-attr]

    assert outcome.scanned == 5
    assert outcome.queued == 3
    assert len(store.list_tasks()) == 4  # 3 条下载 + 1 条 sync 任务本身
    # 游标写回最新扫到的 message_id（FR-SRC-04）
    assert store.get_source(SOURCE_ID).last_message_id == 5  # type: ignore[union-attr]
    # 入队即建 history，落盘前的状态是 queued
    rows = store.list_history()
    assert {r.message_id for r in rows} == {1, 2, 3}
    assert all(r.status == "queued" for r in rows)
    assert all(r.source_id == SOURCE_ID for r in rows)


async def test_run_respects_limit(
    env: tuple[SyncRunner, DownloadService, Store, FakeUserClient],
) -> None:
    # 向导第 3 步的初始导入：最近 N 条即上限，扫够就停（这里最近 3 条里含 1 条 flac document）
    runner, _, store, _ = env
    task_id = _task(store, direction="backward", limit=3)
    outcome = await runner.run(task_id, json.loads(store.get_task(task_id).payload_json))  # type: ignore[union-attr]
    assert outcome.scanned == 3
    assert outcome.queued == 1
    assert {r.message_id for r in store.list_history()} == {3}


async def test_run_skips_already_downloaded(
    env: tuple[SyncRunner, DownloadService, Store, FakeUserClient],
) -> None:
    # 二次回溯：已在库的消息走去重（FR-DL-05），计数进 skipped 而不是重复入队
    runner, _, store, _ = env
    first = _task(store, direction="backward")
    await runner.run(first, json.loads(store.get_task(first).payload_json))  # type: ignore[union-attr]
    for row in store.list_history():
        store.mark_history_status(int(row.id or 0), "success")

    second = _task(store, direction="backward", offset_id=0)
    outcome = await runner.run(second, json.loads(store.get_task(second).payload_json))  # type: ignore[union-attr]
    assert outcome.queued == 0
    assert outcome.skipped == 3


async def test_cancel_stops_after_current_batch(tmp_path: Path) -> None:
    # 取消按批生效：当前批处理完即停，不再拉下一批（这里 150 条 = 两批，第二批不该发生）
    store = Store(tmp_path / "app.db")
    client = FakeUserClient([make_audio_message(i) for i in range(1, 151)])
    events = EventBus()
    downloads = DownloadService(
        store, client, events, tmp_path / "temp", TemplateConfig(save_path=tmp_path / "library")
    )
    runner = SyncRunner(store, client, downloads)
    store.upsert_source(
        Source(id=None, telegram_chat_id=-100123, title="Music Channel", type="channel")
    )
    task_id = _task(store, direction="backward")
    store.update_task(task_id, status="cancelled")
    outcome = await runner.run(task_id, json.loads(store.get_task(task_id).payload_json))  # type: ignore[union-attr]
    assert outcome.scanned == 100  # 一批（BATCH）后就停
    assert outcome.queued == 100


async def test_media_scope_audio_only_excludes_documents(
    env: tuple[SyncRunner, DownloadService, Store, FakeUserClient],
) -> None:
    # FR-SRC-02 源级媒体范围：只留 audio 时，audio/* 的 document 不入队
    runner, _, store, _ = env
    src = store.get_source(SOURCE_ID)
    assert src is not None
    src.media_scope = '["audio"]'
    store.upsert_source(src)

    task_id = _task(store, direction="backward")
    outcome = await runner.run(task_id, json.loads(store.get_task(task_id).payload_json))  # type: ignore[union-attr]
    assert {r.message_id for r in store.list_history()} == {1, 2}
    assert outcome.queued == 2


async def test_source_filters_block_caption(
    env: tuple[SyncRunner, DownloadService, Store, FakeUserClient],
) -> None:
    # FR-SRC-02 源级过滤：caption 含排除词的不入队；无 caption 的消息不受排除词影响
    runner, _, store, _ = env
    src = store.get_source(SOURCE_ID)
    assert src is not None
    src.filters_json = json.dumps({"caption_exclude": ["周杰伦"]})
    store.upsert_source(src)

    task_id = _task(store, direction="backward")
    outcome = await runner.run(task_id, json.loads(store.get_task(task_id).payload_json))  # type: ignore[union-attr]
    assert outcome.queued == 1
    assert {r.message_id for r in store.list_history()} == {3}  # 只有无 caption 的 flac document


async def test_missing_source_is_noop(
    env: tuple[SyncRunner, DownloadService, Store, FakeUserClient],
) -> None:
    # 源在扫描前被删：任务不崩、不写游标（NFR-08）
    runner, _, store, _ = env
    task_id = _task(store, direction="backward")
    store.delete_source(SOURCE_ID)
    outcome = await runner.run(task_id, json.loads(store.get_task(task_id).payload_json))  # type: ignore[union-attr]
    assert outcome.scanned == 0


async def test_worker_dispatch_runs_sync_task_and_marks_success(
    env: tuple[SyncRunner, DownloadService, Store, FakeUserClient],
) -> None:
    # tasks.type='sync' 由下载 Worker 池执行：成功即 success；error 字段只放失败原因
    runner, downloads, store, _ = env
    task_id = _task(store, direction="backward", limit=3)
    row = store.get_task(task_id)
    assert row is not None
    await downloads._run_task(
        {
            "id": task_id,
            "type": "sync",
            "payload_json": row.payload_json,
            "history_id": None,
        }
    )
    done = store.get_task(task_id)
    assert done is not None
    assert done.status == "success"
    assert done.error is None
    assert len(store.list_history()) == 1  # 扫描结果落在真实产物上，不在 error 字段里


async def test_worker_fails_when_sync_runner_missing(
    env: tuple[SyncRunner, DownloadService, Store, FakeUserClient],
) -> None:
    # 未装配执行器时不静默跳过：任务标 failed 并给原因
    _, downloads, store, _ = env
    task_id = _task(store, direction="backward")
    downloads.sync_runner = None
    await downloads._run_task(
        {"id": task_id, "type": "sync", "payload_json": "{}", "history_id": None}
    )
    row = store.get_task(task_id)
    assert row is not None
    assert row.status == "failed"
    assert row.error


async def test_run_uses_last_message_id_as_anchor(
    env: tuple[SyncRunner, DownloadService, Store, FakeUserClient],
) -> None:
    # 游标已推进时，回溯从游标往回扫：只扫更早的消息
    runner, _, store, _ = env
    store.set_source_cursor(SOURCE_ID, 3)
    task_id = _task(store, direction="backward")
    outcome = await runner.run(task_id, json.loads(store.get_task(task_id).payload_json))  # type: ignore[union-attr]
    assert outcome.scanned == 2  # message 1、2
    assert {r.message_id for r in store.list_history()} == {1, 2}
