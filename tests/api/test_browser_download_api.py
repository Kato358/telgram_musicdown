"""浏览器下载 API 测试（FR-DL-08）：准备 → 附件流 → 一次性与错误包络。

这一层锁的是「浏览器拿得到什么」：``Content-Disposition: attachment`` 决定文件落在
用户下载目录，而「不入队、不写历史」是它与 ``POST /api/downloads`` 的分界。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import SecretConfig
from app.db.store import Store
from app.domain import TemplateConfig
from app.events import EventBus
from app.services.browser_download import SUBDIR
from app.services.download import DownloadService
from app.services.preview import PreviewService
from app.services.search import SearchService
from app.services.source import SourceService
from app.telegram.manager import TelegramManager
from app.telegram.unconnected import UnconnectedTelegramClient
from app.web.routes import create_app
from tests.fakes import FakeUserClient, fake_registry, make_audio_message

CONTENT = b"x" * 100

REF = {
    "chat_id": -100123,
    "message_id": 1,
    "title": "晴天",
    "artist": "周杰伦",
    # 与前端真实载荷一致：ext / mime 是落盘名的来源（Telegram 源取数不回报 ext）。
    # 这一条刻意**只给 mime**——它就是「频道里的 flac」那天的真实样子。
    "ext": None,
    "mime": "audio/flac",
    "file_size": len(CONTENT),
}


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    """与 tests/api/test_api.py 同构的装配，只有一个差别：来源索引里有一首真能取到的歌。"""
    store = Store(tmp_path / "app.db")
    events = EventBus()
    tg_client = FakeUserClient([make_audio_message(1)], content=CONTENT)
    registry = fake_registry(store, tg_client)
    sources = SourceService(store, UnconnectedTelegramClient())
    search = SearchService(store, UnconnectedTelegramClient(), registry=registry)
    downloads = DownloadService(
        store,
        UnconnectedTelegramClient(),
        events,
        tmp_path / "temp",
        TemplateConfig(file_template="{artist} - {title}", save_path=tmp_path / "library"),
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
    app.state.store = store
    return TestClient(app)


def test_prepare_then_stream_as_attachment(client: TestClient, tmp_path: Path) -> None:
    prepared = client.post("/api/search/browser-download", json={"message_refs": [REF]})
    assert prepared.status_code == 200
    body = prepared.json()
    # 扩展名从 mime 推定（卡片 ext 为 None、Telegram 源不报 ext）：没有这一档，
    # 浏览器保存下来的就是一个没有扩展名的文件
    assert body["file_name"] == "周杰伦 - 晴天.flac"
    assert body["size"] == len(CONTENT)

    stream = client.get(body["url"])
    assert stream.status_code == 200
    assert stream.content == CONTENT
    disposition = stream.headers["content-disposition"]
    assert disposition.startswith("attachment")
    # 非 ASCII 文件名走 RFC 5987：浏览器保存成「周杰伦 - 晴天.mp3」
    assert "utf-8''" in disposition

    # TTL 内可重复取：浏览器对大文件会分段取（Range/206），下载管理器暂停后还要续传——
    # 「取走即 404」会让这类下载停在半路。分片段取同样命中同一份文件。
    ranged = client.get(body["url"], headers={"Range": "bytes=0-9"})
    assert ranged.status_code == 206 and len(ranged.content) == 10
    assert client.get(body["url"]).status_code == 200

    # 临时文件按 TTL / 并存上限回收，不随某一次响应删（否则续传必失败）
    assert len(list((tmp_path / "temp" / SUBDIR).iterdir())) == 1


def test_repeat_request_returns_the_same_prepared_file(client: TestClient) -> None:
    """同一首歌再点一次：POST 回来的是**同一个地址**——服务端复用已取回的那份。

    用户视角这就是「重复下载同一首歌不必再等一遍」；上游只被取一次（服务层用例锁住了
    取数次数，这里锁住对外的契约：token 与 url 都不变）。
    """
    first = client.post("/api/search/browser-download", json={"message_refs": [REF]}).json()
    second = client.post("/api/search/browser-download", json={"message_refs": [REF]}).json()

    assert second["token"] == first["token"]
    assert second["url"] == first["url"]
    assert second["file_name"] == first["file_name"] == "周杰伦 - 晴天.flac"


def test_unknown_token_is_not_found(client: TestClient) -> None:
    assert client.get("/api/search/browser-download/deadbeef").status_code == 404


def test_cache_card_reports_and_clears_the_temp_dir(client: TestClient) -> None:
    """设置页那张缓存卡要看得见、也清得掉浏览器下载的临时区（FR-DL-08）。

    它**不并进** `total_bytes`：那个数对着试听/封面那份字节预算，混进来就成了
    「占用超上限却没有东西被淘汰」的假象。
    """
    body = client.post("/api/search/browser-download", json={"message_refs": [REF]}).json()
    assert client.get(body["url"]).status_code == 200

    stats = client.get("/api/settings/cache").json()
    assert stats["browser_bytes"] == len(CONTENT)
    assert stats["browser_count"] == 1
    assert stats["total_bytes"] == 0  # 试听/封面那份预算没被它污染

    cleared = client.post("/api/settings/cache/clear").json()
    assert cleared["browser_bytes"] == 0 and cleared["browser_count"] == 0
    assert client.get(body["url"]).status_code == 404  # 登记一并作废


def test_browser_download_does_not_queue_or_record(client: TestClient, tmp_path: Path) -> None:
    """分界：不进下载队列、不写历史、不落 save_path（FR-DL-08 与 FR-DL-01 的分工）。"""
    body = client.post("/api/search/browser-download", json={"message_refs": [REF]}).json()
    assert client.get(body["url"]).status_code == 200

    assert client.get("/api/downloads").json() == []
    assert client.get("/api/history").json() == []
    assert not (tmp_path / "library").exists()


def test_prepare_requires_message_refs(client: TestClient) -> None:
    assert client.post("/api/search/browser-download", json={}).status_code == 422


def test_prepare_maps_failures_to_error_envelope(client: TestClient) -> None:
    """认不出的来源按标准错误包络返回 400：前端把 message 显示在行内。"""
    resp = client.post(
        "/api/search/browser-download",
        json={"message_refs": [{**REF, "provider": "nope"}]},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "browser_download_failed"
