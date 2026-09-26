"""端口与架构组件单测：IStore 协议、appsettings、DI 容器（NFR-07）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.appsettings import (
    DEFAULT_DIR_TEMPLATE,
    DEFAULT_FILE_TEMPLATE,
    AppSettings,
    load_app_settings,
    load_template_config,
)
from app.container import Overrides, build_container
from app.db.models import Source, Task
from app.db.store import Store
from app.domain import TemplateConfig
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


def test_load_app_settings_empty_save_path_falls_back_to_base_downloads(tmp_path: Path) -> None:
    # 空 save_path 不能回退相对 '.'（当前目录）——服务从别的目录启动时（workdir ≠ base_dir）
    # 文件会落到启动目录而不是曲库；缺省值必须是 base_dir/downloads。
    store = Store(tmp_path / "app.db")
    settings = load_app_settings(tmp_path, store)
    assert settings.template.save_path == tmp_path / "downloads"


def test_load_app_settings_reads_db(tmp_path: Path) -> None:
    """DB settings 表覆盖缺省值（FR-CFG-03）。"""
    store = Store(tmp_path / "app.db")
    store.set_setting("max_download_task", "5")
    store.set_setting("dir_template", "{title}")
    settings = load_app_settings(tmp_path, store)
    assert settings.download.max_concurrent == 5
    assert settings.template.dir_template == "{title}"


def test_load_template_config_empty_dir_template_means_flat(tmp_path: Path) -> None:
    """目录模板空值合法（空 = 平铺落根目录）：存过空键不再回退默认。

    文件名模板没有「空」语义，空值仍回退默认（文件总得有名字）。
    """
    store = Store(tmp_path / "app.db")
    store.set_setting("dir_template", "")
    store.set_setting("file_template", "")
    cfg = load_template_config(store, tmp_path)
    assert cfg.dir_template == ""
    assert cfg.file_template == DEFAULT_FILE_TEMPLATE


def test_load_template_config_missing_key_defaults(tmp_path: Path) -> None:
    """settings 表只存写过的键：缺键（从未保存）才用默认模板。"""
    cfg = load_template_config(Store(tmp_path / "app.db"), tmp_path)
    assert cfg.dir_template == DEFAULT_DIR_TEMPLATE
    assert cfg.file_template == DEFAULT_FILE_TEMPLATE
    # download 服务直接持有它：必须是真 TemplateConfig（含 caption 正则，而非鸭子类型）
    assert isinstance(cfg, TemplateConfig)
    assert cfg.caption_artist_re


def test_build_container_wires_services(tmp_path: Path) -> None:
    """DI 容器：按依赖顺序装配，服务互相引用接口而非具体类（DIP）。"""
    container = build_container(
        tmp_path,
        Overrides(media_client=FakeUserClient([make_audio_message(1)])),
    )
    try:
        assert container.downloads.store is container.store
        assert container.sources.store is container.store
        assert container.settings.download.max_concurrent == 3
        # 下载服务持有的模板就是装配配置那份（真 TemplateConfig，即时刷新同源）
        assert container.downloads.cfg is container.settings.template
        assert isinstance(container.store, IStore)
    finally:
        container.close()


def test_build_container_respects_override(tmp_path: Path) -> None:
    """测试注入假客户端：容器照常装配其余组件，不 mock 被测对象。"""
    fake = FakeUserClient([make_audio_message(1)])
    container = build_container(tmp_path, Overrides(media_client=fake, user_client=fake))
    try:
        assert container.downloads.client is fake
        # 搜索、下载、试听必须共用同一份来源索引：同一个来源在三条链路里得是同一个
        # 东西，否则搜索里点的和下载时取的不是一路。
        assert (
            container.search.registry
            is container.downloads.registry
            is container.preview.registry
            is container.registry
        )
    finally:
        container.close()


def test_build_container_closes_store(tmp_path: Path) -> None:
    """容器统一释放资源（SRP：生命周期收口）。"""
    container = build_container(tmp_path)
    container.close()
    with pytest.raises(Exception):  # noqa: B017  sqlite3.ProgrammingError
        container.store.all_settings()


async def test_container_registry_searches_with_injected_client(tmp_path: Path) -> None:
    """来源索引拿到的是注入的客户端，不是 None（回归：逐源搜索曾整条链路打不通）。

    索引是在容器里按客户端构造的；装配若漏掉客户端，症状不是报配置错，而是一次真实
    搜索才炸出的 ``AttributeError: 'NoneType' object has no attribute 'search_messages'``。
    这里把「加了一个源 → 搜它 → 命中」这条最短链路走完。
    """
    fake = FakeUserClient([make_audio_message(1)])
    container = build_container(tmp_path, Overrides(user_client=fake, media_client=fake))
    try:
        container.store.upsert_source(
            Source(id=None, telegram_chat_id=-100123, title="chan", type="channel")
        )
        window = await container.registry.targets()[0].search("晴天", 5)
        assert [card.message_id for card in window.items] == [1]
        assert fake.download_calls == 0  # 搜索不该走下载客户端
    finally:
        container.close()
