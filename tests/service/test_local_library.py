"""本地曲库扫描测试（FR-LIB）：索引、去重收敛、missing 对账、标签回退、事件发布。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.db.models import History
from app.db.store import Store
from app.domain import TrackMeta
from app.events import EventBus
from app.services.local_library import LocalLibraryService
from app.services.tags import TagService
from tests.service.test_tags_preview import make_silent_flac, make_silent_mp3


def make_tagged_mp3(
    base: Path, rel: str, title: str, artist: str, album: str | None = None
) -> Path:
    """在曲库根下按相对路径造一个带 ID3 标签的最小 MP3。"""
    root = base / "downloads"
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    p = make_silent_mp3(target.parent, target.name)
    TagService().write_tags(
        p, TrackMeta(chat_id=-100, message_id=1, title=title, artist=artist, album=album)
    )
    return target


def make_service(base: Path, store: Store, events: EventBus | None = None) -> LocalLibraryService:
    return LocalLibraryService(store, base / "downloads", TagService(), events)


def test_scan_indexes_tagged_file(tmp_path: Path) -> None:
    store = Store(tmp_path / "app.db")
    target = make_tagged_mp3(tmp_path, "周杰伦/晴天.mp3", "晴天", "周杰伦", "叶惠美")
    result = make_service(tmp_path, store).scan_sync()

    assert result["added"] == 1
    rows = store.list_local_tracks()
    assert len(rows) == 1
    row = rows[0]
    assert row.title == "晴天"
    assert row.artist == "周杰伦"
    assert row.album == "叶惠美"
    assert row.rel_path == "周杰伦/晴天.mp3"
    assert row.file_name == "晴天.mp3"
    assert row.ext == "mp3"
    assert row.file_size == target.stat().st_size
    assert row.missing == 0


def test_rescan_converges_without_duplicates(tmp_path: Path) -> None:
    # 同一文件再扫：mtime/size 没变，不重读标签也不新增行
    store = Store(tmp_path / "app.db")
    make_tagged_mp3(tmp_path, "周杰伦/晴天.mp3", "晴天", "周杰伦")
    svc = make_service(tmp_path, store)
    assert svc.scan_sync()["added"] == 1

    result = svc.scan_sync()
    assert result == {"added": 0, "updated": 0, "missing": 0, "total": 1}
    assert len(store.list_local_tracks()) == 1


def test_deleted_file_stays_listed_as_missing(tmp_path: Path) -> None:
    # 「删除后曲库还能看到」：文件删了记录保留，只是 missing=1
    store = Store(tmp_path / "app.db")
    target = make_tagged_mp3(tmp_path, "周杰伦/晴天.mp3", "晴天", "周杰伦")
    svc = make_service(tmp_path, store)
    svc.scan_sync()

    target.unlink()
    result = svc.scan_sync()
    assert result["missing"] == 1
    assert len(store.list_local_tracks()) == 1  # 记录还在
    gone = store.list_local_tracks(missing=True)
    assert [row.title for row in gone] == ["晴天"]
    assert store.list_local_tracks(missing=False) == []


def test_restored_file_comes_back(tmp_path: Path) -> None:
    # 删掉再放回：missing 翻回 0（新 mtime 走更新路径，结论一样）
    store = Store(tmp_path / "app.db")
    target = make_tagged_mp3(tmp_path, "周杰伦/晴天.mp3", "晴天", "周杰伦")
    svc = make_service(tmp_path, store)
    svc.scan_sync()
    target.unlink()
    svc.scan_sync()

    make_tagged_mp3(tmp_path, "周杰伦/晴天.mp3", "晴天", "周杰伦")
    svc.scan_sync()
    assert [row.missing for row in store.list_local_tracks()] == [0]


def test_tagless_file_falls_back_to_names(tmp_path: Path) -> None:
    # 无标签文件：标题回退文件名、歌手回退父目录（本项目落盘默认按歌手建目录）
    store = Store(tmp_path / "app.db")
    root = tmp_path / "downloads"
    target = root / "周深" / "大鱼.flac"
    target.parent.mkdir(parents=True, exist_ok=True)
    make_silent_flac(target.parent, target.name)

    make_service(tmp_path, store).scan_sync()
    row = store.list_local_tracks()[0]
    assert row.title == "大鱼"
    assert row.artist == "周深"
    assert row.ext == "flac"


def test_root_file_has_no_artist_fallback(tmp_path: Path) -> None:
    # 根直下的文件不硬编目录名当歌手（目录就是曲库根本身）
    store = Store(tmp_path / "app.db")
    root = tmp_path / "downloads"
    root.mkdir(parents=True, exist_ok=True)
    make_silent_mp3(root, "loose.mp3")
    make_service(tmp_path, store).scan_sync()
    row = store.list_local_tracks()[0]
    assert row.title == "loose"
    assert row.artist is None


def test_scan_links_history_row(tmp_path: Path) -> None:
    # 已下载过的文件：台账行挂回那条 history（重新下载要用它的 chat/message）
    store = Store(tmp_path / "app.db")
    target = make_tagged_mp3(tmp_path, "周杰伦/晴天.mp3", "晴天", "周杰伦")
    history_id = store.upsert_history(
        History(
            id=None,
            chat_id=-1009,
            message_id=42,
            title="晴天",
            save_path=str(target),
            status="success",
        )
    )
    make_service(tmp_path, store).scan_sync()
    row = store.list_local_tracks()[0]
    assert row.history_id == history_id


def test_history_link_follows_the_current_record(tmp_path: Path) -> None:
    # 链接是「这份文件现在属于哪次下载」，重扫负责把它对齐到当下的事实：
    # 记录被删（外键 SET NULL）后不能留在行上；同一路径重新下载后（文件一个字节没变、
    # 重扫因此跳过它）也必须重新挂回新记录——前端按 history id 认「正在播的同一首歌」。
    store = Store(tmp_path / "app.db")
    sunny = make_tagged_mp3(tmp_path, "周杰伦/晴天.mp3", "晴天", "周杰伦")
    seven = make_tagged_mp3(tmp_path, "周杰伦/七里香.mp3", "七里香", "周杰伦")

    def add_history(target: Path, message_id: int) -> int:
        return store.upsert_history(
            History(
                id=None,
                chat_id=-1009,
                message_id=message_id,
                title=target.stem,
                save_path=str(target),
                status="success",
            )
        )

    sunny_id = add_history(sunny, 42)
    seven_id = add_history(seven, 43)
    svc = make_service(tmp_path, store)
    svc.scan_sync()
    assert {row.rel_path: row.history_id for row in store.list_local_tracks()} == {
        "周杰伦/晴天.mp3": sunny_id,
        "周杰伦/七里香.mp3": seven_id,
    }

    # 删掉七里香那条记录（表里 id 最大的一条，下一个下载会拿到同一个 id）
    assert store.delete_history(seven_id)
    svc.scan_sync()
    links = {row.rel_path: row.history_id for row in store.list_local_tracks()}
    assert links["周杰伦/七里香.mp3"] is None, "记录没了，链接不能留在行上"
    assert links["周杰伦/晴天.mp3"] == sunny_id

    # 新下的一首拿到复用的 id：七里香不能因此挂到别人的记录上
    other = make_tagged_mp3(tmp_path, "周深/大鱼.mp3", "大鱼", "周深")
    assert add_history(other, 44) == seven_id
    svc.scan_sync()
    links = {row.rel_path: row.history_id for row in store.list_local_tracks()}
    assert links["周杰伦/七里香.mp3"] is None
    assert links["周深/大鱼.mp3"] == seven_id

    # 七里香重新下载（同一条路径）：链接跟着走回新记录
    again = add_history(seven, 45)
    svc.scan_sync()
    links = {row.rel_path: row.history_id for row in store.list_local_tracks()}
    assert links["周杰伦/七里香.mp3"] == again
    assert again != seven_id


async def test_rescan_publishes_event(tmp_path: Path) -> None:
    store = Store(tmp_path / "app.db")
    make_tagged_mp3(tmp_path, "周杰伦/晴天.mp3", "晴天", "周杰伦")
    bus = EventBus()
    queue = await bus.subscribe()
    svc = make_service(tmp_path, store, bus)

    await svc.rescan()
    event = await asyncio.wait_for(queue.get(), 2)
    assert event.type == "library.scan"
    assert event.payload["done"] is True
    assert event.payload["added"] == 1
