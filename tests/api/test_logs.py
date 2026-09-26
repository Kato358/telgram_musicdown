"""日志路由测试：尾部解析、续行合并、文件白名单、下载与清空（FR-WEB-06）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import SecretConfig
from app.db.store import Store
from app.domain import TemplateConfig
from app.events import EventBus
from app.services.download import DownloadService
from app.services.preview import PreviewService
from app.services.search import SearchService
from app.services.source import SourceService
from app.telegram.manager import TelegramManager
from app.telegram.unconnected import UnconnectedTelegramClient
from app.web.routes import create_app
from tests.fakes import fake_registry

SAMPLE = (
    "2026-09-25 12:00:00,001 INFO app.services.download started task 1\n"
    "2026-09-25 12:00:01,002 ERROR app.services.download download failed\n"
    "Traceback (most recent call last):\n"
    '  raise ValueError("bad")\n'
    "ValueError: bad\n"
    "2026-09-25 12:00:02,003 WARNING app.telegram.flood flood wait 3s\n"
)


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    store = Store(tmp_path / "app.db")
    events = EventBus()
    registry = fake_registry(store)
    sources = SourceService(store, UnconnectedTelegramClient())
    search = SearchService(store, UnconnectedTelegramClient(), registry=registry)
    downloads = DownloadService(
        store,
        UnconnectedTelegramClient(),
        events,
        tmp_path / "temp",
        TemplateConfig(save_path=tmp_path / "library"),
        registry=registry,
    )
    preview = PreviewService(store, registry, events, tmp_path / "temp" / "preview")
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
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(exist_ok=True)
    (logs_dir / "app.log").write_text(SAMPLE, encoding="utf-8")
    (logs_dir / "app.log.1").write_text(
        "2026-09-25 11:00:00,000 INFO app old line\n", encoding="utf-8"
    )
    return TestClient(app)


def test_logs_parse_and_merge_traceback(client: TestClient) -> None:
    body = client.get("/api/logs").json()
    levels = [e["level"] for e in body["entries"]]
    assert levels == ["INFO", "ERROR", "WARNING"]
    assert body["entries"][1]["message"].endswith("ValueError: bad")
    assert "Traceback (most recent call last):" in body["entries"][1]["message"]
    assert body["entries"][0]["logger"] == "app.services.download"
    assert body["active_file"] == "app.log"
    assert body["truncated"] is False


def test_logs_lists_current_file_first(client: TestClient) -> None:
    names = [f["name"] for f in client.get("/api/logs").json()["files"]]
    assert names[0] == "app.log"
    assert "app.log.1" in names


def test_logs_rejects_path_traversal(client: TestClient) -> None:
    assert client.get("/api/logs", params={"file": "../config.yaml"}).status_code == 404
    assert client.get("/api/logs", params={"file": "app.log.9"}).status_code == 404


def test_logs_reads_rotated_backup(client: TestClient) -> None:
    body = client.get("/api/logs", params={"file": "app.log.1"}).json()
    assert [e["message"] for e in body["entries"]] == ["old line"]


def test_logs_download_returns_file_content(client: TestClient) -> None:
    r = client.get("/api/logs/download")
    assert r.status_code == 200
    assert "started task 1" in r.text
    assert "attachment" in r.headers["content-disposition"]


def test_logs_clear_truncates_current_file_only(client: TestClient) -> None:
    assert client.delete("/api/logs").json() == {"ok": True}
    assert client.get("/api/logs").json()["entries"] == []
    # 旋转备份不动：清空只针对当前文件
    assert client.get("/api/logs", params={"file": "app.log.1"}).json()["entries"]


def test_log_tail_window_reports_truncation(client: TestClient, tmp_path: Path) -> None:
    # 窗口截掉文件开头时如实标记，前端据此提示「更早日志请下载」
    logs_dir = tmp_path / "logs"
    (logs_dir / "app.log").write_text(SAMPLE, encoding="utf-8")
    r = client.get("/api/logs", params={"window": 48})
    assert r.json()["truncated"] is True
