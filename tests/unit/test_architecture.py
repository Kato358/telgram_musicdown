"""端口与架构组件单测：IStore 协议、appsettings、DI 容器（NFR-07）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.appsettings import (
    DEFAULT_DIR_TEMPLATE,
    DEFAULT_FILE_TEMPLATE,
    AppSettings,
    load_app_settings,
)
from app.container import Overrides, build_container
from app.db.models import Task
from app.db.store import Store
from app.ports import IStore
from tests.fakes import FakeUserClient, make_audio_message


def test_store_implements_istore(tmp_path: Path) -> None:
    """SQLite 适配器满足 IStore 协议（DIP：services 依赖端口，实现可替换）。"""
    assert isinstance(Store(tmp_path / "app.db"), IStore)


def test_next_queued_task_marks_downloading(tmp_path: Path) -> None:
    """next_queued_task：取最早一条 queued 并标 downloading（SQL 收口在仓储，SRP）。"""
    store = Store(tmp_path / "app.db")
    first = store.create_task(Task(id=None, type="link", payload_json="{}"))
    second = store.create_task(Task(id=None, type="link", payload_json="{}"))

    task = store.next_queued_task()
    assert task is not None and task["id"] == first
    assert store.get_task(first).status == "downloading"  # type: ignore[union-attr]
    assert store.get_task(second).status == "queued"  # type: ignore[union-attr]
    # 下一轮不再取到同一条
    nxt = store.next_queued_task()
    assert nxt is not None and nxt["id"] == second


def test_load_app_settings_defaults(tmp_path: Path) -> None:
    """缺省值集中管理：空 settings 表退回默认模板与并发。"""
    store = Store(tmp_path / "app.db")
    settings = load_app_settings(tmp_path, store)
    assert isinstance(settings, AppSettings)
    assert settings.template.dir_template == DEFAULT_DIR_TEMPLATE
    assert settings.template.file_template == DEFAULT_FILE_TEMPLATE
    assert settings.download.max_concurrent == 3
    assert settings.preview.max_bytes == 512 * 1024 * 1024


def test_load_app_settings_reads_db(tmp_path: Path) -> None:
    """DB settings 表覆盖缺省值（FR-CFG-03）。"""
    store = Store(tmp_path / "app.db")
    store.set_setting("max_download_task", "5")
    store.set_setting("dir_template", "{title}")
    settings = load_app_settings(tmp_path, store)
    assert settings.download.max_concurrent == 5
    assert settings.template.dir_template == "{title}"


def test_build_container_wires_services(tmp_path: Path) -> None:
    """DI 容器：按依赖顺序装配，服务互相引用接口而非具体类（DIP）。"""
    container = build_container(
        tmp_path,
        Overrides(download_client=FakeUserClient([make_audio_message(1)])),
    )
    try:
        assert container.downloads.store is container.store
        assert container.sources.store is container.store
        assert container.settings.download.max_concurrent == 3
        assert isinstance(container.store, IStore)
    finally:
        container.close()


def test_build_container_respects_override(tmp_path: Path) -> None:
    """测试注入假客户端：容器照常装配其余组件，不 mock 被测对象。"""
    fake = FakeUserClient([make_audio_message(1)])
    container = build_container(tmp_path, Overrides(download_client=fake))
    try:
        assert container.downloads.client is fake
        assert container.preview.client is fake
    finally:
        container.close()


def test_build_container_closes_store(tmp_path: Path) -> None:
    """容器统一释放资源（SRP：生命周期收口）。"""
    container = build_container(tmp_path)
    container.close()
    with pytest.raises(Exception):  # noqa: B017  sqlite3.ProgrammingError
        container.store.all_settings()
