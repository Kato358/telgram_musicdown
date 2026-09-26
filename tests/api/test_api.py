"""API 测试：认证、错误包络、401、0.0.0.0 无密码拒启（SDD §7）。"""

from __future__ import annotations

import asyncio
import json
from hashlib import md5
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
from app.web.routes import lyrics as lyrics_route
from tests.fakes import FakeUserClient
from tests.service.test_tags_preview import JPEG, make_cover_mp3

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


def test_history_rows_point_at_their_current_task(client: TestClient, tmp_path: Path) -> None:
    # 下载页一行 = 一条历史记录 + 它当前挂着的任务：实时读数与暂停/继续/取消都靠这个 id
    store = Store(tmp_path / "app.db")
    history_id = store.upsert_history(History(id=None, chat_id=-1009, message_id=1, title="Song"))
    store.create_task(Task(id=None, type="link", payload_json="{}", history_id=history_id))
    latest = store.create_task(Task(id=None, type="link", payload_json="{}", history_id=history_id))
    store.create_task(Task(id=None, type="sync", payload_json="{}"))  # 无历史行的同步任务不该被串上

    rows = client.get("/api/history").json()
    assert [(row["id"], row["task_id"]) for row in rows] == [(history_id, latest)]


def test_cancel_settles_history_row(client: TestClient, tmp_path: Path) -> None:
    # 取消后这一行不能再显示「等待」：状态筛选按 history.status 过滤，两边必须一致
    store = Store(tmp_path / "app.db")
    history_id = store.upsert_history(History(id=None, chat_id=-1009, message_id=2, title="Song"))
    task_id = store.create_task(
        Task(id=None, type="link", payload_json="{}", history_id=history_id)
    )

    assert client.post(f"/api/downloads/{task_id}/cancel").status_code == 200

    row = store.get_history(history_id)
    assert row is not None and row.status == "cancelled"
    assert [r["id"] for r in client.get("/api/history?status=cancelled").json()] == [history_id]
    assert client.get("/api/history?status=queued").json() == []


def test_history_filter_accepts_several_statuses(client: TestClient, tmp_path: Path) -> None:
    # 下载页「进行中」页签一次要拿三类在跑的行（等待/下载中/已暂停）：多值筛选仍然在服务端做
    # （前端过滤会把分页算错）；空的分段（如 "nonsense,,queued"）被忽略，不整条筛选失效。
    store = Store(tmp_path / "app.db")
    for index, status in enumerate(["queued", "downloading", "paused", "success"], start=1):
        store.upsert_history(
            History(id=None, chat_id=-1010, message_id=index, title=f"S{index}", status=status)
        )

    rows = client.get("/api/history?status=queued,downloading,paused").json()
    assert [row["status"] for row in rows] == ["paused", "downloading", "queued"]
    assert [row["status"] for row in client.get("/api/history?status=success,queued").json()] == [
        "success",
        "queued",
    ]
    assert [row["status"] for row in client.get("/api/history?status=nonsense,,queued").json()] == [
        "queued"
    ]


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


def test_web_login_issues_session_cookie(tmp_path: Path) -> None:
    """FR-WEB-02：口令换 cookie——这是受保护部署唯一的进门方式（无它则恒 401）。"""
    client, _ = _client_with(
        tmp_path,
        web_host="0.0.0.0",  # noqa: S104  测试注入的是绑定字符串
        web_login_secret="s3cret",  # noqa: S106
    )
    # 未登录：受保护端点 401，会话自述 required+未认证
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/auth/session").json() == {"required": True, "authenticated": False}
    # 口令错：401 且不发 cookie
    assert client.post("/api/auth/login", json={"secret": "wrong"}).status_code == 401
    assert client.get("/api/me").status_code == 401
    # 口令对：200 且此后放行
    assert client.post("/api/auth/login", json={"secret": "s3cret"}).status_code == 200
    assert client.get("/api/auth/session").json() == {"required": True, "authenticated": True}
    assert client.get("/api/me").status_code == 200


def test_web_session_logout_clears_cookie(tmp_path: Path) -> None:
    """退出 Web 控制台：清 cookie 后重新 401（与 Telegram 账号登出互不影响）。"""
    client, _ = _client_with(
        tmp_path,
        web_host="0.0.0.0",  # noqa: S104
        web_login_secret="s3cret",  # noqa: S106
    )
    client.post("/api/auth/login", json={"secret": "s3cret"})
    assert client.get("/api/stats").status_code == 200
    assert client.post("/api/auth/session/logout").status_code == 200
    assert client.get("/api/stats").status_code == 401


def test_web_login_exempt_when_localhost_without_secret(tmp_path: Path) -> None:
    """本机免密模式：不要求登录，也不签发 cookie（前端据此跳过登录页）。"""
    client, _ = _client_with(tmp_path, web_host="127.0.0.1", web_login_secret="")
    assert client.get("/api/auth/session").json() == {"required": False, "authenticated": True}
    assert client.get("/api/stats").status_code == 200


def test_web_login_switch_disabled(tmp_path: Path) -> None:
    """web_login_enabled=False：显式关闭登录——有口令、绑 0.0.0.0 也全放行。"""
    # 启动校验也不再拦「0.0.0.0 无密码」：开关关了还拦，公网部署就永远开不起来
    web_auth.check_auth_config("0.0.0.0", "", web_login_enabled=False)  # noqa: S104
    client, _ = _client_with(
        tmp_path,
        web_host="0.0.0.0",  # noqa: S104  测试注入的是绑定字符串
        web_login_secret="s3cret",  # noqa: S106
        web_login_enabled=False,
    )
    assert client.get("/api/auth/session").json() == {"required": False, "authenticated": True}
    assert client.get("/api/me").status_code == 200
    # 登录端点同样放行且不签 cookie（与免密模式同语义，前端不会出现登录页）
    assert client.post("/api/auth/login", json={"secret": "whatever"}).status_code == 200
    assert not client.cookies.get(web_auth.SESSION_COOKIE)


def test_events_stream_requires_session(tmp_path: Path) -> None:
    """/api/events 是最后一个无鉴权的 /api/*：未登录可订阅等于把任务进度与错误日志公网裸奔。

    只断言 401——无限流不进 TestClient 阻塞读取（项目约定见 test_sse_event_bus_delivers_event）；
    放行路径复用与 /api/stats 等同一个 check_session 依赖，其放行语义已由免密用例覆盖。
    """
    client, _ = _client_with(
        tmp_path,
        web_host="0.0.0.0",  # noqa: S104  测试注入的是绑定字符串
        web_login_secret="s3cret",  # noqa: S106
    )
    assert client.get("/api/events").status_code == 401


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
    web_login_enabled: bool = True,
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
        web_login_enabled=web_login_enabled,
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


def test_stats_counts_written_tracks_and_tasks(client: TestClient, tmp_path: Path) -> None:
    # 统计卡的判据：已入库 = 有落盘路径的行（入队但还没写盘的不算，免得数字比曲库大）；
    # 占用去磁盘上量，所以「记录里有路径、文件却不在磁盘」的那条不算占用。
    library = tmp_path / "library"
    library.mkdir()
    on_disk = library / "on-disk.mp3"
    on_disk.write_bytes(b"x" * 1000)
    store = Store(tmp_path / "app.db")
    store.upsert_history(
        History(
            id=None,
            chat_id=-1001,
            message_id=1,
            title="On Disk",
            status="success",
            save_path=str(on_disk),
            file_size=999,  # DB 里的账目与磁盘不一致时，占用以磁盘为准
        )
    )
    store.upsert_history(
        History(
            id=None,
            chat_id=-1001,
            message_id=2,
            title="Moved Away",
            status="success",
            save_path=str(library / "gone.mp3"),
        )
    )
    store.upsert_history(
        History(id=None, chat_id=-1001, message_id=3, title="Queued", status="queued")
    )
    store.upsert_history(
        History(id=None, chat_id=-1001, message_id=4, title="Failed", status="failed")
    )
    store.create_task(Task(id=None, type="link", payload_json="{}", status="downloading"))

    body = client.get("/api/stats").json()
    assert body["library"] == {
        "tracks": 2,
        "bytes": 1000,
        "failed": 1,
        "local_present": 0,
        "local_missing": 0,
        "local_bytes": 0,
    }
    assert body["tasks"]["downloading"] == 1
    assert body["tasks"]["success"] == 0
    assert body["uptime_sec"] >= 0


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


def test_history_reveal_opens_folder(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, tmp_path: Path
) -> None:
    # 「打开所在文件夹」：落盘目录在 → 200 且把目录交给文件管理器；
    # 文件被移走但目录还在仍可打开；目录都没了或记录不存在 → 404。
    opened: list[Path] = []

    def record_open(folder: Path) -> None:
        opened.append(folder)

    monkeypatch.setattr("app.web.routes.history.open_in_file_manager", record_open)
    library = tmp_path / "library"
    library.mkdir()
    on_disk = library / "a.mp3"
    on_disk.write_bytes(b"x")
    store = Store(tmp_path / "app.db")
    hid = store.upsert_history(
        History(
            id=None,
            chat_id=-1001,
            message_id=1,
            title="A",
            status="success",
            save_path=str(on_disk),
        )
    )
    moved_id = store.upsert_history(
        History(
            id=None,
            chat_id=-1001,
            message_id=2,
            title="Moved",
            status="success",
            save_path=str(library / "gone.mp3"),
        )
    )
    vanished_id = store.upsert_history(
        History(
            id=None,
            chat_id=-1001,
            message_id=3,
            title="Vanished",
            status="success",
            save_path=str(tmp_path / "no-such-dir" / "a.mp3"),
        )
    )

    resp = client.post(f"/api/history/{hid}/reveal")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert client.post(f"/api/history/{moved_id}/reveal").status_code == 200
    assert opened == [library, library]
    assert client.post(f"/api/history/{vanished_id}/reveal").status_code == 404
    assert client.post("/api/history/99999/reveal").status_code == 404


def test_history_reveal_reports_open_failure(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, tmp_path: Path
) -> None:
    # 文件管理器打不开（OSError）→ 500 且说原因，不静默。
    def boom(folder: Path) -> None:
        del folder
        raise OSError("no file manager")

    monkeypatch.setattr("app.web.routes.history.open_in_file_manager", boom)
    library = tmp_path / "library"
    library.mkdir()
    on_disk = library / "a.mp3"
    on_disk.write_bytes(b"x")
    store = Store(tmp_path / "app.db")
    hid = store.upsert_history(
        History(
            id=None,
            chat_id=-1001,
            message_id=1,
            title="A",
            status="success",
            save_path=str(on_disk),
        )
    )

    resp = client.post(f"/api/history/{hid}/reveal")
    assert resp.status_code == 500
    assert "no file manager" in resp.text


def test_delete_history_record_removes_row_not_file(client: TestClient, tmp_path: Path) -> None:
    # 下载页行删除（FR-DL-06 补全）：DELETE /api/history/{id} 删记录，落盘文件保留。
    library = tmp_path / "library"
    library.mkdir()
    on_disk = library / "a.mp3"
    on_disk.write_bytes(b"x")
    store = Store(tmp_path / "app.db")
    hid = store.upsert_history(
        History(
            id=None,
            chat_id=-1001,
            message_id=1,
            title="A",
            status="success",
            save_path=str(on_disk),
        )
    )

    resp = client.delete(f"/api/history/{hid}")
    assert resp.status_code == 200
    assert store.get_history(hid) is None
    assert client.get("/api/history").json() == []
    assert on_disk.exists()  # 落盘文件不动


def test_delete_history_record_with_active_task_cancels_task(
    client: TestClient, tmp_path: Path
) -> None:
    # 记录还挂着在跑的任务：删记录前先取消并删台账，不留永不动的「等待」。
    store = Store(tmp_path / "app.db")
    hid = store.upsert_history(
        History(id=None, chat_id=-1002, message_id=5, title="B", status="queued")
    )
    task_id = store.create_task(Task(id=None, type="link", payload_json="{}", history_id=hid))

    resp = client.delete(f"/api/history/{hid}")
    assert resp.status_code == 200
    assert store.get_history(hid) is None
    assert store.get_task(task_id) is None


def test_delete_missing_history_returns_404(client: TestClient) -> None:
    # 不存在的记录：404，前端照常弹失败提示。
    assert client.delete("/api/history/99999").status_code == 404


def test_cover_streams_cached_api_cover(client: TestClient, tmp_path: Path) -> None:
    # 全局封面（api 段）：按歌名/歌手查公共封面接口，查询串 md5 命中磁盘缓存 → 200 回图片字节；
    # 无可查字段（title/artist 都空）→ 404，前端退回音符占位。
    preview = tmp_path / "temp" / "preview"
    preview.mkdir(parents=True)
    digest = md5("晴天|周杰伦".encode()).hexdigest()  # noqa: S324
    (preview / f"cover_{digest}.jpg").write_bytes(b"\xff\xd8jpeg")

    r = client.get("/api/cover", params={"title": "晴天", "artist": "周杰伦"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/jpeg")
    assert r.content == b"\xff\xd8jpeg"
    assert client.get("/api/cover").status_code == 404


def test_cover_local_tag_beats_api(client: TestClient, tmp_path: Path) -> None:
    # 全局封面（本地段）：按标题找到已落盘的成功记录，优先读文件的内嵌封面——
    # 即使 api 缓存也有同名封面，本地仍优先；200 出 APIC 字节 + ETag，重访 304。
    store = Store(tmp_path / "app.db")
    audio = make_cover_mp3(tmp_path, name="song.mp3")
    store.upsert_history(
        History(
            id=None,
            chat_id=-1002,
            message_id=1,
            title="Song",
            status="success",
            save_path=str(audio),
        )
    )
    preview = tmp_path / "temp" / "preview"
    preview.mkdir(parents=True)
    digest = md5(b"Song|").hexdigest()  # noqa: S324
    (preview / f"cover_{digest}.jpg").write_bytes(b"\xff\xd8jpeg-api")

    r = client.get("/api/cover", params={"title": "Song"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/jpeg")
    assert r.content == JPEG  # 本地标签字节，不是 api 的占位字节
    r304 = client.get(
        "/api/cover", params={"title": "Song"}, headers={"If-None-Match": r.headers["etag"]}
    )
    assert r304.status_code == 304


def test_cover_without_local_file_falls_back_to_api(client: TestClient, tmp_path: Path) -> None:
    # 全局封面（api 兜底）：没落盘（save_path 空）→ 预置 api 磁盘缓存命中，不打外网；
    # 连歌名/歌手都没有的行无处可查 → 404。
    store = Store(tmp_path / "app.db")
    store.upsert_history(History(id=None, chat_id=-1002, message_id=9, title="Song"))
    preview = tmp_path / "temp" / "preview"
    preview.mkdir(parents=True)
    digest = md5(b"Song|").hexdigest()  # noqa: S324
    (preview / f"cover_{digest}.jpg").write_bytes(b"\xff\xd8jpeg")

    r = client.get("/api/cover", params={"title": "Song"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/jpeg")
    assert r.content == b"\xff\xd8jpeg"
    # 无可查依据（title/artist 都缺省）→ 404，不打外网
    assert client.get("/api/cover").status_code == 404


def test_put_settings_applies_templates_immediately(tmp_path: Path) -> None:
    """设置保存即时生效（FR-CFG-03）：PUT 后下载服务的模板与试听缓存同步刷新，不等重启。"""
    store = Store(tmp_path / "app.db")
    events = EventBus()
    downloads = DownloadService(
        store,
        None,
        events,
        tmp_path / "temp",
        TemplateConfig(save_path=tmp_path / "library"),  # type: ignore[arg-type]
    )
    preview = PreviewService(store, None, events, tmp_path / "temp" / "preview")  # type: ignore[arg-type]
    app = create_app(
        store,
        events,
        downloads,
        SourceService(store, None),  # type: ignore[arg-type]
        SearchService(store, None),  # type: ignore[arg-type]
        preview,
        TelegramManager(SecretConfig(), tmp_path / "sessions"),
        base_dir=tmp_path,
        web_host="127.0.0.1",
        web_login_secret="",
    )
    client = TestClient(app)
    resp = client.put(
        "/api/settings",
        json={
            "values": {
                "dir_template": "",
                "file_template": "{title}",
                "preview_cache_max_bytes": "64",
            }
        },
    )
    assert resp.status_code == 200
    # 目录模板空值 = 平铺：写库的空值原样进服务，不再被默认顶掉
    assert downloads.cfg.dir_template == ""
    assert downloads.cfg.file_template == "{title}"
    assert preview.max_bytes == 64


def test_cache_usage_and_clear(client: TestClient, tmp_path: Path) -> None:
    """设置页的缓存占用/清理（FR-PLAY-02）：占用按磁盘字节报，清理后文件与占用都归零。"""
    preview = tmp_path / "temp" / "preview"
    preview.mkdir(parents=True)
    (preview / "cover_deadbeef.jpg").write_bytes(b"\xff\xd8jpeg")  # 6 字节
    (preview / "preview_1_2.bin").write_bytes(b"y" * 90)
    (preview / "preview_1_2.part").write_bytes(b"z" * 999)  # 中转文件不算占用

    resp = client.get("/api/settings/cache")
    assert resp.status_code == 200
    assert resp.json() == {
        "total_bytes": 96,
        "max_bytes": 512 * 1024 * 1024,
        "preview_bytes": 90,
        "preview_count": 1,
        "cover_bytes": 6,
        "cover_count": 1,
    }

    cleared = client.post("/api/settings/cache/clear")
    assert cleared.status_code == 200
    assert cleared.json()["total_bytes"] == 0
    assert list(preview.iterdir()) == []  # 中转文件也一并清掉


def test_preview_path_empty_dir_template_is_flat(tmp_path: Path) -> None:
    """空目录模板的预览：路径 = 落盘根 + 文件名，没有中间子目录。"""
    store = Store(tmp_path / "app.db")
    events = EventBus()
    downloads = DownloadService(
        store,
        None,
        events,
        tmp_path / "temp",
        TemplateConfig(save_path=tmp_path / "library"),  # type: ignore[arg-type]
    )
    preview = PreviewService(store, None, events, tmp_path / "temp" / "preview")  # type: ignore[arg-type]
    app = create_app(
        store,
        events,
        downloads,
        SourceService(store, None),  # type: ignore[arg-type]
        SearchService(store, None),  # type: ignore[arg-type]
        preview,
        TelegramManager(SecretConfig(), tmp_path / "sessions"),
        base_dir=tmp_path,
        web_host="127.0.0.1",
        web_login_secret="",
    )
    client = TestClient(app)
    resp = client.post(
        "/api/settings/preview-path",
        json={"dir_template": "", "file_template": "{track:02d} {title}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["relative"] == "03 晴天.mp3"


# ---- 歌词路由（/api/lyrics）：播放器歌词面板的数据源 ----


def test_lyrics_without_params_returns_empty_200(client: TestClient) -> None:
    """title/artist 全缺省：无可查依据，直接 200 空体（APlayer 解析为空列表）。"""
    r = client.get("/api/lyrics")
    assert r.status_code == 200
    assert r.text == ""


def test_lyrics_title_only_no_artist_ok(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """只给 title（artist 缺省）也不 500：查询参数为 None 要安全回退空串。"""
    lrc = "[00:01.00]line\n"
    monkeypatch.setattr(lyrics_route, "_fetch_lyrics", lambda title, artist: lrc)
    r = client.get("/api/lyrics", params={"title": "晴天"})
    assert r.status_code == 200
    assert r.text == lrc


def test_lyrics_found_returns_lrc_text(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """命中：原样回 LRC 文本（text/plain），恒 200 是给 APlayer 的约定。"""
    lrc = "[00:01.00]test line\n[00:03.00]another\n"
    monkeypatch.setattr(lyrics_route, "_fetch_lyrics", lambda title, artist: lrc)
    r = client.get("/api/lyrics", params={"title": "晴天", "artist": "周杰伦"})
    assert r.status_code == 200
    assert "text/plain" in r.headers["content-type"]
    assert r.text == lrc


def test_lyrics_not_found_returns_empty_200(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """查不到（含超时/出错）：空体而不是 404——APlayer 对非 2xx 会弹英文 notice。"""
    monkeypatch.setattr(lyrics_route, "_fetch_lyrics", lambda title, artist: None)
    r = client.get("/api/lyrics", params={"title": "不存在", "artist": "没有人"})
    assert r.status_code == 200
    assert r.text == ""
