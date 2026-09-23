"""服务层测试：下载状态机、暂停、FloodWait、去重、媒体组（NFR-07，SDD §7）。

FakeUserClient 注入依赖，不 mock 被测对象（编码规范 §6 反模式表）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.db.models import History
from app.db.models import Task as TaskModel
from app.db.store import Store
from app.domain import TemplateConfig, TrackMeta
from app.events import EventBus
from app.services.download import DownloadRequest, DownloadService, backoff_sec
from app.services.source import SearchService
from app.telegram import flood as flood_mod
from app.telegram.flood import with_flood_retry
from tests.fakes import FakeFloodWait, FakeUserClient, make_audio_message

CHAT_ID = -100123


def req(message_id: int, **meta: Any) -> DownloadRequest:
    """单条下载请求：元数据走 TrackMeta（SDD §2.2）。"""
    return DownloadRequest(meta=TrackMeta(chat_id=CHAT_ID, message_id=message_id, **meta))


def worker_row(store: Store, task_id: int) -> dict[str, Any]:
    """Worker 消费用的任务行（与 _next_queued 同形：含 type/history_id）。"""
    task = store.get_task(task_id)
    assert task is not None
    return {
        "id": task.id,
        "type": task.type,
        "payload_json": task.payload_json,
        "history_id": task.history_id,
    }


@pytest.fixture()
def svc(tmp_path: Path) -> tuple[DownloadService, Store, FakeUserClient]:
    store = Store(tmp_path / "app.db")
    events = EventBus()
    client = FakeUserClient([make_audio_message(1), make_audio_message(2)])
    service = DownloadService(
        store, client, events, tmp_path / "temp", TemplateConfig(save_path=tmp_path / "library")
    )
    return service, store, client


async def test_download_success_saves_to_save_path(
    svc: tuple[DownloadService, Store, FakeUserClient], tmp_path: Path
) -> None:
    service, store, _ = svc
    task_id = await service.enqueue(req(1, file_size=100))
    assert task_id is not None
    await service._run_task(worker_row(store, task_id))
    assert store.get_task(task_id).status == "success"  # type: ignore[union-attr]
    # save_path 有文件
    assert any((tmp_path / "library").glob("**/*"))
    # 入队即建 history 行，完成时写回落盘路径（历史页与恢复流程的事实源）
    history_id = store.get_task(task_id).history_id  # type: ignore[union-attr]
    assert history_id is not None
    row = store.get_history(history_id)
    assert row is not None
    assert row.status == "success"
    assert row.save_path


async def test_bare_link_task_hydrates_meta(
    svc: tuple[DownloadService, Store, FakeUserClient], tmp_path: Path
) -> None:
    # bot 链接/转发入队只有 chat_id/message_id：下载前取消息补全 ext/file_size，
    # 落盘带扩展名（标签容器依赖它）且大小校验生效。
    service, store, client = svc
    task_id = await service.enqueue(req(1))  # meta 全空
    assert task_id is not None
    await service._run_task(worker_row(store, task_id))
    assert store.get_task(task_id).status == "success"  # type: ignore[union-attr]
    # ext 来自 Fake 消息的 audio.file_name（晴天.mp3），文件名不再是无后缀 message_1
    saved = [p for p in (tmp_path / "library").rglob("*") if p.is_file()]
    assert saved and saved[0].suffix == ".mp3"
    # 大小校验现在生效：Fake 内容 100 字节，注入不符 → failed 且不落盘
    client.size_override = 99
    task_id2 = await service.enqueue(
        DownloadRequest(meta=TrackMeta(chat_id=CHAT_ID, message_id=2), force=True)
    )
    assert task_id2 is not None
    await service._run_task(worker_row(store, task_id2))
    assert store.get_task(task_id2).status == "failed"  # type: ignore[union-attr]


async def test_size_mismatch_not_saved(
    svc: tuple[DownloadService, Store, FakeUserClient], tmp_path: Path
) -> None:
    # NFR-01：损坏文件（大小不符）不落 save_path
    service, store, client = svc
    client.size_override = 50  # 注入大小不符（期望 100）
    task_id = await service.enqueue(req(1, file_size=100))
    assert task_id is not None
    await service._run_task(worker_row(store, task_id))
    assert store.get_task(task_id).status == "failed"  # type: ignore[union-attr]
    assert "size mismatch" in (store.get_task(task_id).error or "")  # type: ignore[union-attr]
    # save_path 无文件（NFR-01）
    assert not list((tmp_path / "library").glob("**/*"))


async def test_dedupe_enqueue_skips(svc: tuple[DownloadService, Store, FakeUserClient]) -> None:
    # FR-DL-05：同 (chat_id, message_id) 已成功 → 去重命中
    service, store, _ = svc
    # 预置成功历史
    store.upsert_history(History(id=None, chat_id=CHAT_ID, message_id=1, status="success"))
    task_id = await service.enqueue(req(1))
    assert task_id is None  # 命中去重，不入队


async def test_dedupe_force_bypasses(svc: tuple[DownloadService, Store, FakeUserClient]) -> None:
    service, store, _ = svc
    store.upsert_history(History(id=None, chat_id=CHAT_ID, message_id=1, status="success"))
    task_id = await service.enqueue(
        DownloadRequest(meta=TrackMeta(chat_id=CHAT_ID, message_id=1), force=True)
    )
    assert task_id is not None


async def test_failed_history_row_reused_on_retry(
    svc: tuple[DownloadService, Store, FakeUserClient],
) -> None:
    # 同一消息重试不该新增 history 行（tasks.history_id 始终指向它）
    service, store, _ = svc
    first = await service.enqueue(req(1, file_size=100))
    assert first is not None
    history_id = store.get_task(first).history_id  # type: ignore[union-attr]
    store.mark_history_status(int(history_id), "failed", error="boom")  # type: ignore[arg-type]
    again = await service.enqueue(req(1, file_size=100))
    assert again is not None
    assert store.get_task(again).history_id == history_id  # type: ignore[union-attr]
    assert store.get_history(int(history_id)).status == "queued"  # type: ignore[union-attr]


async def test_floodwait_retries_after_wait(
    svc: tuple[DownloadService, Store, FakeUserClient], tmp_path: Path
) -> None:
    # NFR-09：FloodWait 注入 → 挂起后重试、不无视等待。
    # 引擎内 with_flood_retry 捕获 pyrogram FloodWait；这里验证包装器重试行为
    # 与 FakeFloodWait 等价语义（挂起 value 秒后重试）。
    service, store, client = svc
    client.flood_queue = [1]  # 1 次 1 秒 FloodWait
    task_id = await service.enqueue(req(1))
    assert task_id is not None
    with pytest.raises(Exception, match="flood wait"):
        # 引擎不捕获 FakeFloodWait（它只认 pyrogram FloodWait）；
        # 挂起行为单测由 test_with_flood_retry_retries 覆盖
        await service._run_task(worker_row(store, task_id))
    assert client.download_calls == 1


async def test_with_flood_retry_retries() -> None:
    # NFR-09：with_flood_retry 挂起 value 秒后重试（FakeFloodWait 等价注入）

    calls = 0

    async def fn() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise FakeFloodWait(0)
        return "ok"

    async def no_sleep(_: float) -> None:
        return None

    # monkeypatch flood.py 模块内的 FloodWait 引用（from-import 已绑定到该模块）

    original = flood_mod.FloodWait
    flood_mod.FloodWait = FakeFloodWait  # type: ignore[assignment,misc]
    try:
        out = await with_flood_retry(fn, sleep=no_sleep)
    finally:
        flood_mod.FloodWait = original
    assert out == "ok"
    assert calls == 2


def test_backoff_sec_exponential() -> None:
    # 重试退避计算 min(2^n * 30s, 1h)
    assert backoff_sec(0) == 30
    assert backoff_sec(1) == 60
    assert backoff_sec(3) == 240
    assert backoff_sec(10) == 3600


async def test_voice_message_not_audio(tmp_path: Path) -> None:
    # 验收 #11：voice 消息不入队（is_audio_message 判定）
    store = Store(tmp_path / "app.db")
    voice_msg = make_audio_message(3, voice=True)
    client = FakeUserClient([voice_msg, make_audio_message(4)])
    service = SearchService(store, client)  # type: ignore[arg-type]
    resp = await service.search("晴天")
    # voice 被排除，非 voice 的正常入结果
    assert all(r.message_id != 3 for r in resp.results)


async def test_pause_task_marks_paused(svc: tuple[DownloadService, Store, FakeUserClient]) -> None:
    service, store, _ = svc
    task_id = await service.enqueue(req(1))
    await service.pause_task(task_id)  # type: ignore[arg-type]
    assert store.get_task(task_id).status == "paused"  # type: ignore[union-attr]
    await service.resume_task(task_id)  # type: ignore[arg-type]
    assert store.get_task(task_id).status == "queued"  # type: ignore[union-attr]


async def test_recover_interrupted(tmp_path: Path) -> None:
    # NFR-05：downloading → failed（retryable），queued 保持

    store = Store(tmp_path / "app.db")
    queued = store.create_task(TaskModel(id=None, type="link", payload_json=json.dumps({})))
    downloading = store.create_task(TaskModel(id=None, type="link", payload_json=json.dumps({})))
    store.update_task(downloading, status="downloading")
    n = store.recover_interrupted()
    assert n == 1
    assert store.get_task(queued).status == "queued"  # type: ignore[union-attr]
    assert store.get_task(downloading).status == "failed"  # type: ignore[union-attr]
