"""API 测试：认证、错误包络、401、0.0.0.0 无密码拒启（SDD §7）。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import SecretConfig
from app.db.store import Store
from app.domain import TemplateConfig
from app.errors import WebAuthConfigError
from app.events import Event, EventBus
from app.services.download import DownloadService
from app.services.preview import PreviewService
from app.services.source import SearchService, SourceService
from app.telegram.manager import TelegramManager
from app.web import auth as web_auth
from app.web.routes import create_app


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    store = Store(tmp_path / "app.db")
    events = EventBus()
    sources = SourceService(store, None)  # type: ignore[arg-type]
    search = SearchService(store, None)  # type: ignore[arg-type]
    downloads = DownloadService(
        store,
        None,
        events,
        tmp_path / "temp",
        TemplateConfig(save_path=tmp_path / "library"),  # type: ignore[arg-type]
    )
    preview = PreviewService(store, None, events, tmp_path / "temp" / "preview")  # type: ignore[arg-type]
    tg = TelegramManager(SecretConfig(), tmp_path / "sessions")
    app = create_app(
        store,
        events,
        downloads,
        sources,
        search,
        preview,
        tg,
        base_dir=tmp_path,
        web_host="127.0.0.1",
        web_login_secret="",
    )
    return TestClient(app)


def test_no_session_local_exempt_returns_200(client: TestClient) -> None:
    # 127.0.0.1 且无密码：豁免认证（FR-WEB-02）
    assert client.get("/api/me").status_code == 200


def test_bad_link_returns_error_not_crash(client: TestClient) -> None:
    r = client.post("/api/downloads", json={"urls": ["bad-link"]})
    assert r.status_code == 200
    items = r.json()["items"]
    assert items[0]["error"]


def test_history_empty(client: TestClient) -> None:
    assert client.get("/api/history").json() == []


def test_zero_zero_host_without_secret_rejected(tmp_path: Path) -> None:
    # FR-WEB-02：0.0.0.0 无密码启动报错退出
    with pytest.raises(WebAuthConfigError):
        web_auth.check_auth_config("0.0.0.0", "")  # noqa: S104  测试注入的是绑定字符串


def test_auth_required_with_secret(tmp_path: Path) -> None:
    # 有 secret：无会话 401（验收 #16）
    store = Store(tmp_path / "app.db")
    events = EventBus()
    sources = SourceService(store, None)  # type: ignore[arg-type]
    search = SearchService(store, None)  # type: ignore[arg-type]
    downloads = DownloadService(
        store,
        None,
        events,
        tmp_path / "temp",
        TemplateConfig(save_path=tmp_path / "library"),  # type: ignore[arg-type]
    )
    preview = PreviewService(store, None, events, tmp_path / "temp" / "preview")  # type: ignore[arg-type]
    tg = TelegramManager(SecretConfig(), tmp_path / "sessions")
    app = create_app(  # 测试注入 0.0.0.0 验证强制密码路径
        store,
        events,
        downloads,
        sources,
        search,
        preview,
        tg,
        base_dir=tmp_path,
        web_host="0.0.0.0",  # noqa: S104
        web_login_secret="s3cret",  # noqa: S106
    )
    c = TestClient(app)
    assert c.get("/api/me").status_code == 401
    token = web_auth.make_session_token("s3cret")
    c.cookies.set(web_auth.SESSION_COOKIE, token)
    assert c.get("/api/me").status_code == 200


def test_setup_secrets_writes_to_base_dir(tmp_path: Path) -> None:
    # FR-OPS-02：密钥写入 base_dir/config.yaml；session_directory 可配置到别处也不影响
    store = Store(tmp_path / "app.db")
    events = EventBus()
    sources = SourceService(store, None)  # type: ignore[arg-type]
    search = SearchService(store, None)  # type: ignore[arg-type]
    downloads = DownloadService(
        store,
        None,
        events,
        tmp_path / "temp",
        TemplateConfig(save_path=tmp_path / "downloads"),  # type: ignore[arg-type]
    )
    preview = PreviewService(store, None, events, tmp_path / "temp" / "preview")  # type: ignore[arg-type]
    tg = TelegramManager(SecretConfig(), tmp_path / "elsewhere" / "sessions")
    app = create_app(
        store, events, downloads, sources, search, preview, tg, base_dir=tmp_path
    )
    r = TestClient(app).post("/api/setup/secrets", json={"api_id": 12345, "api_hash": "abc"})
    assert r.status_code == 200
    assert "abc" in (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert not (tmp_path / "elsewhere" / "config.yaml").exists()


def test_sse_event_bus_delivers_event() -> None:
    # SSE /api/events 依赖的 EventBus 投递行为（SDD §1.4）。
    # SSE 流是无限流，不用 TestClient 阻塞读取验证；直接验证投递语义。
    async def scenario() -> str:
        events = EventBus()
        q = await events.subscribe()
        await events.publish(Event("task.status", {"task_id": 1, "status": "queued"}))
        ev = await q.get()
        await events.unsubscribe(q)
        return ev.type

    assert asyncio.run(scenario()) == "task.status"
