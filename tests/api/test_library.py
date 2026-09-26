"""本地曲库 API 测试（FR-LIB）：列表分页 / 筛选排序 / 流与封面 / 重扫触发 / 记录删除。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import SecretConfig
from app.db.models import History, Task
from app.db.store import Store
from app.domain import TemplateConfig
from app.events import EventBus
from app.services.download import DownloadService
from app.services.local_library import LocalLibraryService
from app.services.preview import PreviewService
from app.services.source import SearchService, SourceService
from app.services.tags import TagService
from app.telegram.manager import TelegramManager
from app.web.routes import create_app
from tests.service.test_tags_preview import make_silent_mp3

API_HASH = "0123456789abcdef0123456789abcdef"


def make_client(tmp_path: Path) -> tuple[TestClient, Store, LocalLibraryService]:
    store = Store(tmp_path / "app.db")
    events = EventBus()
    tags = TagService()
    library = LocalLibraryService(store, tmp_path / "downloads", tags, events)
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
    tg = TelegramManager(SecretConfig(), tmp_path / "sessions")
    app = create_app(
        store,
        events,
        downloads,
        sources,
        search,
        preview,
        tg,
        library=library,
        base_dir=tmp_path,
        web_host="127.0.0.1",
        web_login_secret="",
    )
    return TestClient(app), store, library


def seed_audio(tmp_path: Path, rel: str) -> Path:
    root = tmp_path / "downloads"
    root.mkdir(parents=True, exist_ok=True)
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    return make_silent_mp3(target.parent, target.name)


def test_library_list_empty(tmp_path: Path) -> None:
    client, _, _ = make_client(tmp_path)
    data = client.get("/api/local-library").json()
    assert data["items"] == []
    assert data["total"] == 0
    assert data["counts"] == {"present": 0, "missing": 0, "all": 0}
    assert data["bytes"] == 0


def test_library_lists_scanned_files_with_history_link(tmp_path: Path) -> None:
    client, store, library = make_client(tmp_path)
    target = seed_audio(tmp_path, "周杰伦/晴天.mp3")
    history_id = store.upsert_history(
        History(
            id=None, chat_id=-1009, message_id=7, title="晴天", save_path=str(target),
            status="success",
        )
    )
    library.scan_sync()

    data = client.get("/api/local-library").json()
    assert data["total"] == 1
    row = data["items"][0]
    assert row["rel_path"] == "周杰伦/晴天.mp3"
    assert row["title"] == "晴天"
    assert row["artist"] == "周杰伦"
    assert row["missing"] is False
    assert row["history_id"] == history_id
    assert row["chat_id"] == -1009
    assert data["counts"]["present"] == 1
    assert data["bytes"] == target.stat().st_size


def test_library_sort_and_search(tmp_path: Path) -> None:
    client, _, library = make_client(tmp_path)
    seed_audio(tmp_path, "A/backward.mp3")
    seed_audio(tmp_path, "B/ahead.mp3")
    library.scan_sync()

    asc = client.get("/api/local-library", params={"sort": "title", "order": "asc"}).json()
    assert [row["title"] for row in asc["items"]] == ["ahead", "backward"]
    hits = client.get("/api/local-library", params={"q": "ahead"}).json()
    assert hits["total"] == 1 and hits["items"][0]["title"] == "ahead"
    assert hits["counts"] == {"present": 1, "missing": 0, "all": 1}  # 计数跟随关键词


def test_library_artist_filter_and_facets(tmp_path: Path) -> None:
    client, _, library = make_client(tmp_path)
    seed_audio(tmp_path, "周杰伦/晴天.mp3")
    seed_audio(tmp_path, "周深/大鱼.flac")
    library.scan_sync()

    artists = client.get("/api/local-library/artists").json()
    assert {item["artist"]: item["count"] for item in artists} == {"周深": 1, "周杰伦": 1}
    rows = client.get("/api/local-library", params={"artist": "周深"}).json()
    assert [row["title"] for row in rows["items"]] == ["大鱼"]


def test_library_pagination_lazily_serves_pages(tmp_path: Path) -> None:
    client, _, library = make_client(tmp_path)
    for index in range(5):
        seed_audio(tmp_path, f"A/track{index}.mp3")
    library.scan_sync()

    page1 = client.get("/api/local-library", params={"limit": 2, "offset": 0}).json()
    page2 = client.get("/api/local-library", params={"limit": 2, "offset": 2}).json()
    assert len(page1["items"]) == 2 and len(page2["items"]) == 2
    assert page1["total"] == 5
    assert {row["id"] for row in page1["items"]}.isdisjoint(
        {row["id"] for row in page2["items"]}
    )


def test_library_missing_rows_stay_visible_and_stream_404s(tmp_path: Path) -> None:
    # 文件删除后：曲库仍列出（missing=1）、播放流 404、计数搬到「已删除」
    client, _, library = make_client(tmp_path)
    target = seed_audio(tmp_path, "周杰伦/晴天.mp3")
    library.scan_sync()
    track_id = client.get("/api/local-library").json()["items"][0]["id"]
    assert client.get(f"/api/local-library/{track_id}/stream").status_code == 200

    target.unlink()
    library.scan_sync()

    data = client.get("/api/local-library").json()
    assert data["counts"] == {"present": 0, "missing": 1, "all": 1}
    assert client.get("/api/local-library?missing=1").json()["total"] == 1
    assert client.get("/api/local-library?missing=0").json()["total"] == 0
    assert client.get(f"/api/local-library/{track_id}/stream").status_code == 404


def test_library_delete_record_keeps_file_and_rescan_restores(tmp_path: Path) -> None:
    client, _, library = make_client(tmp_path)
    target = seed_audio(tmp_path, "周杰伦/晴天.mp3")
    library.scan_sync()
    track_id = client.get("/api/local-library").json()["items"][0]["id"]

    assert client.delete(f"/api/local-library/{track_id}").status_code == 200
    assert client.get("/api/local-library").json()["total"] == 0
    assert target.exists()  # 磁盘文件不动

    library.scan_sync()  # 文件还在 → 下次扫描重新入库
    assert client.get("/api/local-library").json()["total"] == 1


def test_library_scan_endpoint_fills_index(tmp_path: Path) -> None:
    client, _, _ = make_client(tmp_path)
    seed_audio(tmp_path, "周杰伦/晴天.mp3")
    seed_audio(tmp_path, "周深/大鱼.flac")
    # 后台任务：轮询等它扫完（TestClient 上下文管理器保活事件循环）
    with client:
        assert client.post("/api/local-library/scan").json() == {"ok": True}
        for _ in range(100):
            if client.get("/api/local-library").json()["total"] == 2:
                break
            time.sleep(0.05)
    assert client.get("/api/local-library").json()["total"] == 2


def test_library_unknown_track_404(tmp_path: Path) -> None:
    client, _, _ = make_client(tmp_path)
    assert client.get("/api/local-library/999/stream").status_code == 404
    assert client.delete("/api/local-library/999").status_code == 404


def test_library_task_links_not_required(tmp_path: Path) -> None:
    # 曲库行不依赖任务台账：没有 tasks 行也照常工作
    client, store, library = make_client(tmp_path)
    target = seed_audio(tmp_path, "A/x.mp3")
    history_id = store.upsert_history(
        History(id=None, chat_id=-1, message_id=1, title="x", save_path=str(target),
                status="success")
    )
    store.create_task(Task(id=None, type="link", payload_json="{}", history_id=history_id))
    library.scan_sync()
    row = client.get("/api/local-library").json()["items"][0]
    assert row["history_id"] == history_id


@pytest.mark.parametrize(
    "sort", ["title", "artist", "album", "duration", "size", "bitrate", "created"]
)
def test_library_all_sort_keys_accepted(tmp_path: Path, sort: str) -> None:
    client, _, library = make_client(tmp_path)
    seed_audio(tmp_path, "A/one.mp3")
    library.scan_sync()
    resp = client.get("/api/local-library", params={"sort": sort, "order": "asc"})
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 1
