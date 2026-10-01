"""浏览器下载 API 测试（FR-DL-08）：进度流 → 可用性探测 → 附件流。

这一层锁的是「浏览器拿得到什么、用户看得到什么」：``Content-Disposition: attachment``
决定文件落在用户下载目录；SSE 的进度事件与失败终态决定界面能不能说清「在取、取到哪、
为什么失败」；而「不入队、不写历史」是它与 ``POST /api/downloads`` 的分界。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import SecretConfig
from app.db.store import Store
from app.domain import TemplateConfig, meta_from_dict
from app.events import EventBus
from app.services.browser_download import SUBDIR, BrowserDownloadService
from app.services.download import DownloadService
from app.services.preview import PreviewService
from app.services.search import SearchService
from app.services.source import SourceService
from app.telegram.manager import TelegramManager
from app.telegram.unconnected import UnconnectedTelegramClient
from app.web.routes import create_app
from app.web.routes.browser_download import _prepare_stream
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


class SlowClient(FakeUserClient):
    """取数途中真有等待的假客户端：进度事件才有机会被推出来。

    ``FakeUserClient`` 一瞬写完，整条流只剩终态——那种「快到没有中间态」的情形
    在生产的母带 / 慢源上不成立，故这里补一个按拍子走的版本。
    """

    async def download_media(  # type: ignore[override]
        self, message_ref: dict[str, Any], file_name: str, progress: Any = None
    ) -> str | None:
        self.download_calls += 1
        size = len(self.content)
        if progress is not None:
            progress(0, size)
        for step in range(1, 4):
            await asyncio.sleep(0.25)
            if progress is not None:
                progress(size * step // 3, size)
        Path(file_name).write_bytes(self.content)  # noqa: ASYNC240  假客户端允许直接写文件
        return file_name


def sse_events(resp: Any) -> list[dict[str, Any]]:
    """把 SSE 响应体拆成事件列表（每条 `data: {...}` 一行）。"""
    return [
        json.loads(line[len("data:") :].strip())
        for line in resp.text.splitlines()
        if line.startswith("data:")
    ]


def final_event(resp: Any) -> dict[str, Any]:
    """终态事件（``ready`` / ``failed``）：流的最后一条。"""
    events = sse_events(resp)
    assert events, "流里一条事件都没有"
    return events[-1]


def build_client(tmp_path: Path, tg_client: FakeUserClient) -> TestClient:
    """与 tests/api/test_api.py 同构的装配，只有一个差别：来源索引里有一首真能取到的歌。"""
    store = Store(tmp_path / "app.db")
    events = EventBus()
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


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    return build_client(tmp_path, FakeUserClient([make_audio_message(1)], content=CONTENT))


@pytest.fixture()
def slow_client(tmp_path: Path) -> TestClient:
    return build_client(tmp_path, SlowClient([make_audio_message(1)], content=CONTENT))


def test_prepare_streams_progress_then_ready(slow_client: TestClient) -> None:
    """取数途中推字节进度，结束推终态（FR-DL-08）。

    这是「取回中」那块的唯一数据来源：没有进度事件，前端只能给一个转圈。
    """
    resp = slow_client.post("/api/search/browser-download", json={"message_refs": [REF]})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = sse_events(resp)
    progress = [e for e in events if e["state"] == "preparing"]
    assert progress, "取数耗时超过一拍，必须推过进度事件"
    # 分母来自卡片声明的 file_size：真实百分比要的就是它
    assert progress[-1]["total"] == len(CONTENT)
    assert 0 < progress[-1]["loaded"] <= len(CONTENT)

    body = final_event(resp)
    assert body["state"] == "ready"
    # 扩展名从 mime 推定（卡片 ext 为 None、Telegram 源不报 ext）：没有这一档，
    # 浏览器保存下来的就是一个没有扩展名的文件
    assert body["file_name"] == "周杰伦 - 晴天.flac"
    assert body["size"] == len(CONTENT)


def test_prepare_then_stream_as_attachment(client: TestClient, tmp_path: Path) -> None:
    body = final_event(client.post("/api/search/browser-download", json={"message_refs": [REF]}))
    assert body["state"] == "ready"

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
    """同一首歌再点一次：回来的是**同一个地址**——服务端复用已取回的那份。

    用户视角这就是「重复下载同一首歌不必再等一遍」；上游只被取一次（服务层用例锁住了
    取数次数，这里锁住对外的契约：token 与 url 都不变）。
    """
    first = final_event(client.post("/api/search/browser-download", json={"message_refs": [REF]}))
    second = final_event(client.post("/api/search/browser-download", json={"message_refs": [REF]}))

    assert second["token"] == first["token"]
    assert second["url"] == first["url"]
    assert second["file_name"] == first["file_name"] == "周杰伦 - 晴天.flac"


def test_head_probe_reports_availability(client: TestClient) -> None:
    """导航前的可用性探测（FR-DL-08）：只有它能让「临时文件过期」不把整页带走。

    ``window.location.assign`` 之后前端读不到响应体，404 会变成浏览器的错误页——
    故这一问必须在导航之前，且必须不读文件体。
    """
    body = final_event(client.post("/api/search/browser-download", json={"message_refs": [REF]}))
    probe = client.head(body["url"])
    assert probe.status_code == 204
    assert probe.content == b""

    assert client.head("/api/search/browser-download/deadbeef").status_code == 404


def test_unknown_token_is_not_found(client: TestClient) -> None:
    assert client.get("/api/search/browser-download/deadbeef").status_code == 404


def test_cache_card_reports_and_clears_the_temp_dir(client: TestClient) -> None:
    """设置页那张缓存卡要看得见、也清得掉浏览器下载的临时区（FR-DL-08）。

    它**不并进** `total_bytes`：那个数对着试听/封面那份字节预算，混进来就成了
    「占用超上限却没有东西被淘汰」的假象。
    """
    body = final_event(client.post("/api/search/browser-download", json={"message_refs": [REF]}))
    assert client.get(body["url"]).status_code == 200

    stats = client.get("/api/settings/cache").json()
    assert stats["browser_bytes"] == len(CONTENT)
    assert stats["browser_count"] == 1
    assert stats["total_bytes"] == 0  # 试听/封面那份预算没被它污染

    cleared = client.post("/api/settings/cache/clear").json()
    assert cleared["browser_bytes"] == 0 and cleared["browser_count"] == 0
    assert client.get(body["url"]).status_code == 404  # 登记一并作废
    # 清掉之后探测也要说「取不了了」：否则前端会把用户导航到 404 错误页
    assert client.head(body["url"]).status_code == 404


def test_browser_download_does_not_queue_or_record(client: TestClient, tmp_path: Path) -> None:
    """分界：不进下载队列、不写历史、不落 save_path（FR-DL-08 与 FR-DL-01 的分工）。"""
    body = final_event(client.post("/api/search/browser-download", json={"message_refs": [REF]}))
    assert client.get(body["url"]).status_code == 200

    assert client.get("/api/downloads").json() == []
    assert client.get("/api/history").json() == []
    assert not (tmp_path / "library").exists()


def test_prepare_requires_message_refs(client: TestClient) -> None:
    assert client.post("/api/search/browser-download", json={}).status_code == 422


def test_prepare_reports_failure_in_stream(client: TestClient) -> None:
    """认不出的来源按标准错误包络的形状回在流里：前端把 message 显示在行内。

    状态码这里**只能是 200**：失败要到取数途中才知道，那时响应头早就发出去了。
    形状不变（``{"error": {"code", "message"}}``）是刻意的——前端只认一种错误形状。
    """
    resp = client.post(
        "/api/search/browser-download",
        json={"message_refs": [{**REF, "provider": "nope"}]},
    )
    assert resp.status_code == 200
    body = final_event(resp)
    assert body["state"] == "failed"
    assert body["error"]["code"] == "browser_download_failed"
    assert "没有可用的取数来源" in body["error"]["message"]


async def test_closing_the_stream_cancels_the_fetch(tmp_path: Path) -> None:
    """客户端断开（用户点了「取消」）→ 取数被停掉、半截分片被清（FR-DL-08）。

    断流在 Starlette 那边就是「把生成器关掉」，故这里直接 ``aclose()`` 复现它——
    真去断一条 HTTP 连接要靠竞态，测不稳。取消要一路传到取数层：界面上的「取消」
    承诺的不只是「卡片消失」，还有「别再替我把这首歌拉完」。
    """
    client = SlowClient([make_audio_message(1)], content=CONTENT)
    store = Store(tmp_path / "app.db")
    registry = fake_registry(store, client)
    browser = BrowserDownloadService(
        registry,
        tmp_path / "temp",
        TemplateConfig(file_template="{artist} - {title}", save_path=tmp_path / "library"),
    )
    stream = _prepare_stream(browser, meta_from_dict(REF), None)
    try:
        first = await anext(stream)
        assert json.loads(first[len("data:") :].strip())["state"] == "preparing"
    finally:
        await stream.aclose()
    assert client.download_calls == 1, "取数应当已经开跑（否则这条用例什么也没测到）"
    assert browser.stats().count == 0, "取消后不该留下半截分片"
    store.close()
