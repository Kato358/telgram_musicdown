"""API 测试：认证、错误包络、401、0.0.0.0 无密码拒启（SDD §7）。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import SecretConfig, load_secrets
from app.db.models import History, Task
from app.db.store import Store
from app.domain import TemplateConfig
from app.errors import WebAuthConfigError
from app.events import Event, EventBus
from app.services.download import DownloadService
from app.services.preview import PreviewService
from app.services.source import SearchService, SourceService
from app.services.sync import INITIAL_IMPORT_LIMIT
from app.telegram.manager import TelegramManager
from app.web import auth as web_auth
from app.web.routes import create_app
from tests.fakes import FakeUserClient

API_HASH = "0123456789abcdef0123456789abcdef"


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
    app = create_app(store, events, downloads, sources, search, preview, tg, base_dir=tmp_path)
    r = TestClient(app).post("/api/setup/secrets", json={"api_id": 1234567, "api_hash": API_HASH})
    assert r.status_code == 200
    assert API_HASH in (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert not (tmp_path / "elsewhere" / "config.yaml").exists()


def _client_with(
    tmp_path: Path,
    secrets: SecretConfig | None = None,
    *,
    web_host: str = "127.0.0.1",
    web_login_secret: str = "",
    static_dir: Path | None = None,
) -> tuple[TestClient, TelegramManager]:
    """带自定密钥的 API 客户端（向导相关用例共用）。"""
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
    tg = TelegramManager(secrets or SecretConfig(), tmp_path / "sessions")
    app = create_app(
        store,
        events,
        downloads,
        sources,
        search,
        preview,
        tg,
        base_dir=tmp_path,
        web_host=web_host,
        web_login_secret=web_login_secret,
        static_dir=static_dir,
    )
    return TestClient(app), tg


def test_setup_secrets_rejects_bad_api_id_and_keeps_file(tmp_path: Path) -> None:
    # FR-OPS-02：校验在写盘前做（api_id 必须 5–10 位数字）
    client, _ = _client_with(tmp_path)
    r = client.post("/api/setup/secrets", json={"api_id": 12, "api_hash": API_HASH})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_secrets"
    assert not (tmp_path / "config.yaml").exists()


def test_setup_secrets_proxy_null_clears_existing(tmp_path: Path) -> None:
    # 关掉「走代理」后保存：config.yaml 里的 proxy 段被清除（不残留旧代理）
    (tmp_path / "config.yaml").write_text(
        "api_id: 1234567\n"
        f"api_hash: {API_HASH}\n"
        "proxy:\n  scheme: http\n  hostname: 127.0.0.1\n  port: 7890\n",
        encoding="utf-8",
    )
    client, _ = _client_with(tmp_path)
    r = client.post("/api/setup/secrets", json={"proxy": None})
    assert r.status_code == 200
    assert "proxy" not in (tmp_path / "config.yaml").read_text(encoding="utf-8")


def test_setup_secrets_proxy_keeps_unseen_credentials(tmp_path: Path) -> None:
    # 向导不回显代理用户名/密码（NFR-02）：表单没填时沿用已有值，不能因为看不见就删掉
    (tmp_path / "config.yaml").write_text(
        "api_id: 1234567\n"
        f"api_hash: {API_HASH}\n"
        "proxy:\n  scheme: socks5\n  hostname: 10.0.0.9\n  port: 1080\n"
        "  username: alice\n  password: s3cret\n  enable_proxy: true\n",
        encoding="utf-8",
    )
    client, _ = _client_with(tmp_path)
    r = client.post(
        "/api/setup/secrets",
        json={"proxy": {"scheme": "http", "hostname": "10.0.0.10", "port": 8080}},
    )
    assert r.status_code == 200
    body = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert "hostname: 10.0.0.10" in body
    assert "username: alice" in body and "password: s3cret" in body
    assert "enable_proxy: true" in body


def test_setup_status_requires_login_for_complete(tmp_path: Path) -> None:
    # 放行判据 = 密钥 + 登录（向导第 3 步的源可选）：只有密钥不算完成
    client, tg = _client_with(tmp_path, SecretConfig(api_id=1234567, api_hash=API_HASH))
    body = client.get("/api/setup/status").json()
    assert body["has_api_id"] is True and body["has_api_hash"] is True
    assert body["connected"] is False
    assert body["complete"] is False
    tg.authorized = True
    assert client.get("/api/setup/status").json()["complete"] is True


def test_missing_frontend_serves_actionable_page(tmp_path: Path) -> None:
    # 首次部署没构建前端时首屏要说清怎么办（而不是一行 404 JSON），/api 仍回 JSON
    client, _ = _client_with(tmp_path)
    r = client.get("/")
    assert r.status_code == 503
    assert "npm run build" in r.text
    assert client.get("/api/nope").json() == {"detail": "Not Found"}


def test_spa_serves_index_and_falls_back_for_routes(tmp_path: Path) -> None:
    # 首次使用靠 URL 直达向导：非 /api 路径一律回退 index.html，否则 /setup 刷不出来
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    client, _ = _client_with(tmp_path, static_dir=dist)
    assert client.get("/").status_code == 200
    assert "spa" in client.get("/setup").text
    assert client.get("/api/nope").json() == {"detail": "Not Found"}


def test_setup_secrets_requires_session_when_auth_on(tmp_path: Path) -> None:
    # 密钥写入是改配置的敏感操作：开了认证就必须带会话（未带 → 401，不落盘）
    client, _ = _client_with(
        tmp_path,
        web_host="0.0.0.0",  # noqa: S104  测试注入的是绑定字符串
        web_login_secret="s3cret",  # noqa: S106
    )
    r = client.post("/api/setup/secrets", json={"api_id": 1234567, "api_hash": API_HASH})
    assert r.status_code == 401
    assert not (tmp_path / "config.yaml").exists()


def test_logout_deletes_session_files_and_keeps_data(tmp_path: Path) -> None:
    # FR-AUTH-02：退出登录 = 删会话文件（留着它下次启动会判「已有有效会话」直接放行）
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    (sessions / "musicdown.session").write_bytes(b"session")
    (sessions / "musicdown.session-journal").write_bytes(b"journal")
    (sessions / "musicdown-bot.session").write_bytes(b"bot")
    client, _ = _client_with(tmp_path)
    r = client.post("/api/auth/logout")
    assert r.status_code == 200
    assert r.json()["removed_sessions"] == ["musicdown.session", "musicdown.session-journal"]
    assert not (sessions / "musicdown.session").exists()
    assert (sessions / "musicdown-bot.session").exists()  # Bot 是独立身份，token 没换就留着


def test_logout_keeps_web_console_session(tmp_path: Path) -> None:
    # 退出的是 Telegram 账号，不是 Web 控制台：没有登录页，清掉 cookie 会把人锁在门外
    client, _ = _client_with(
        tmp_path,
        web_host="0.0.0.0",  # noqa: S104  测试注入的是绑定字符串
        web_login_secret="s3cret",  # noqa: S106
    )
    client.cookies.set(web_auth.SESSION_COOKIE, web_auth.make_session_token("s3cret"))
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/me").status_code == 200


def test_setup_reset_clears_keys_and_sessions(tmp_path: Path) -> None:
    # FR-OPS-02「重新执行初始化」：密钥段与会话全清，Web 口令与部署路径一个不动
    (tmp_path / "config.yaml").write_text(
        "api_id: 1234567\n"
        f"api_hash: {API_HASH}\n"
        "bot_token: 123456:ABC-DEF\n"
        "web_login_secret: keepme\n"
        "save_directory: /data/downloads\n"
        "proxy:\n  scheme: http\n  hostname: 10.0.0.9\n  port: 7890\n",
        encoding="utf-8",
    )
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    (sessions / "musicdown.session").write_bytes(b"session")
    (sessions / "musicdown-bot.session").write_bytes(b"bot")
    client, _ = _client_with(tmp_path, load_secrets(tmp_path))
    assert client.get("/api/setup/status").json()["has_api_id"] is True

    body = client.post("/api/setup/reset").json()
    assert body["cleared_keys"] == ["api_id", "api_hash", "bot_token", "proxy"]
    text = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert "api_id" not in text and "bot_token" not in text and "proxy" not in text
    assert "web_login_secret: keepme" in text
    assert "save_directory: /data/downloads" in text
    assert not list(sessions.glob("*.session"))  # User 与 Bot 会话都没了
    status = client.get("/api/setup/status").json()
    assert status["has_api_id"] is False and status["complete"] is False


def test_setup_status_hides_proxy_credentials(tmp_path: Path) -> None:
    # NFR-02：状态接口只回协议/地址/端口，用户名密码不出网
    (tmp_path / "config.yaml").write_text(
        "api_id: 1234567\n"
        f"api_hash: {API_HASH}\n"
        "proxy:\n  scheme: socks5\n  hostname: 10.0.0.9\n  port: 1080\n"
        "  username: u\n  password: p\n",
        encoding="utf-8",
    )
    client, _ = _client_with(tmp_path, load_secrets(tmp_path))
    proxy = client.get("/api/setup/status").json()["proxy"]
    assert proxy == {
        "scheme": "socks5",
        "hostname": "10.0.0.9",
        "port": 1080,
        "username": None,
        "password": None,
    }


def test_send_code_without_credentials_reports_missing_secrets(tmp_path: Path) -> None:
    # FR-AUTH-01：没保存密钥就点「发送验证码」→ 明确原因，不去连 Telegram
    client, _ = _client_with(tmp_path)
    r = client.post("/api/auth/telegram/send-code", json={"phone": "+8613800000000"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "secrets_missing"


def test_send_code_rejects_bad_phone(tmp_path: Path) -> None:
    client, _ = _client_with(tmp_path, SecretConfig(api_id=1234567, api_hash=API_HASH))
    r = client.post("/api/auth/telegram/send-code", json={"phone": "12"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "phone_invalid"


def test_discover_requires_login(tmp_path: Path) -> None:
    # FR-SRC-05：未登录时候选源接口报未连接，不返回空列表假装没有候选
    client, _ = _client_with(tmp_path)
    r = client.get("/api/sources/discover")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "not_connected"


def test_add_source_enqueues_initial_import(tmp_path: Path) -> None:
    # 向导第 3 步：添加源后立刻建一条 sync 任务（最近 200 条的一次性导入）
    store = Store(tmp_path / "app.db")
    events = EventBus()
    client = FakeUserClient([])
    sources = SourceService(store, client)  # type: ignore[arg-type]
    search = SearchService(store, client)  # type: ignore[arg-type]
    downloads = DownloadService(
        store,
        client,
        events,
        tmp_path / "temp",
        TemplateConfig(save_path=tmp_path / "downloads"),
    )
    preview = PreviewService(store, client, events, tmp_path / "temp" / "preview")
    tg = TelegramManager(SecretConfig(), tmp_path / "sessions")
    app = create_app(store, events, downloads, sources, search, preview, tg, base_dir=tmp_path)
    body = TestClient(app).post("/api/sources", json={"link": "@music_library"}).json()
    assert body["username"] == "music"
    task = store.get_task(body["import_task_id"])
    assert task is not None
    assert task.type == "sync"
    assert json.loads(task.payload_json)["limit"] == INITIAL_IMPORT_LIMIT


def test_backfill_unknown_source_404(tmp_path: Path) -> None:
    client, _ = _client_with(tmp_path)
    r = client.post("/api/sources/999/backfill", json={"direction": "backward"})
    assert r.status_code == 404


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


def test_event_bus_publish_nowait_from_worker_thread() -> None:
    async def scenario() -> str:
        events = EventBus()
        q = await events.subscribe()
        await asyncio.to_thread(
            events.publish_nowait, Event("task.progress", {"task_id": 7, "progress_bytes": 3})
        )
        event = await asyncio.wait_for(q.get(), timeout=1)
        return str(event.payload["progress_bytes"])
    assert asyncio.run(scenario()) == "3"



def test_download_list_and_lifecycle_routes(client: TestClient, tmp_path: Path) -> None:
    store = Store(tmp_path / "app.db")
    history_id = store.upsert_history(
        History(
            id=None,
            chat_id=-100123,
            message_id=1,
            title="History Song",
            artist="History Artist",
            status="failed",
            save_path="library/kept.mp3",
        )
    )
    task_id = store.create_task(
        Task(
            id=None,
            type="link",
            payload_json=json.dumps(
                {
                    "meta": {
                        "chat_id": -100123,
                        "message_id": 1,
                        "title": "Payload Song",
                        "artist": "Payload Artist",
                    }
                }
            ),
            status="failed",
            total_bytes=100,
            history_id=history_id,
        )
    )
    store.update_task(task_id, speed=7.5, error="temporary failure")
    fallback_id = store.create_task(
        Task(
            id=None,
            type="link",
            payload_json=json.dumps(
                {
                    "meta": {
                        "chat_id": -100123,
                        "message_id": 2,
                        "title": "Payload Fallback",
                        "artist": "Fallback Artist",
                    }
                }
            ),
        )
    )

    rows = {row["id"]: row for row in client.get("/api/downloads").json()}
    assert rows[task_id]["title"] == "History Song"
    assert rows[task_id]["artist"] == "History Artist"
    assert rows[task_id]["speed"] == 7.5
    assert rows[fallback_id]["title"] == "Payload Fallback"
    assert rows[fallback_id]["artist"] == "Fallback Artist"

    assert client.post(f"/api/downloads/{task_id}/retry").status_code == 200
    assert store.get_task(task_id).status == "queued"  # type: ignore[union-attr]
    assert client.delete(f"/api/downloads/{task_id}").status_code == 200
    assert store.get_task(task_id) is None
    assert store.get_history(history_id) is not None
    assert client.post("/api/downloads/999/retry").status_code == 404
    assert client.delete("/api/downloads/999").status_code == 404
