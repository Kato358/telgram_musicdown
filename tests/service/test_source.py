"""服务层测试：候选源发现（FR-SRC-05）与添加源的错误翻译（FR-SRC-01）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.db.models import Source
from app.db.store import Store
from app.errors import AppError, SourceUnreachableError
from app.services.source import SourceService
from app.telegram.unconnected import UnconnectedTelegramClient
from app.utils.linkparse import parse_link
from tests.fakes import FakeUserClient

DIALOGS = [
    {
        "chat_id": -1001,
        "title": "Music Library",
        "username": "music_library",
        "type": "channel",
        "members": 128400,
    },
    {
        "chat_id": -1002,
        "title": "无损音乐分享",
        "username": "lossless_music",
        "type": "channel",
        "members": 4200,
    },
    {
        "chat_id": -1003,
        "title": "家庭相册",
        "username": "family_photos",
        "type": "group",
        "members": 12,
    },
    {"chat_id": -1004, "title": "歌单仓库", "username": None, "type": "group", "members": 900},
]


def _service(tmp_path: Path) -> tuple[SourceService, Store]:
    store = Store(tmp_path / "app.db")
    return SourceService(store, FakeUserClient([], dialogs=DIALOGS)), store


async def test_discover_returns_only_music_candidates(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    items = await service.discover_candidates()
    assert {c.title for c in items} == {"Music Library", "无损音乐分享", "歌单仓库"}
    assert all(c.tags for c in items)


async def test_discover_excludes_already_added(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    store.upsert_source(
        Source(id=None, telegram_chat_id=-1001, title="Music Library", type="channel")
    )
    items = await service.discover_candidates()
    assert -1001 not in {c.chat_id for c in items}


async def test_discover_sorts_by_size_then_title(tmp_path: Path) -> None:
    # 成员数多的先出（成员数缺失按 0 处理，不假装知道）
    service, _ = _service(tmp_path)
    items = await service.discover_candidates()
    assert [c.chat_id for c in items] == [-1001, -1002, -1004]


async def test_discover_marks_missing_member_count(tmp_path: Path) -> None:
    store = Store(tmp_path / "app.db")
    client = FakeUserClient(
        [],
        dialogs=[{"chat_id": -1005, "title": "FLAC Music", "username": "flac", "type": "channel"}],
    )
    service = SourceService(store, client)
    items = await service.discover_candidates()
    assert items[0].members is None


class _ExplodingDialogs:
    """get_chat 抛认不出的底层异常（模拟上游库的陌生错误）。"""

    async def get_chat(self, entity: str | int) -> dict[str, Any]:
        raise RuntimeError("boom")

    async def list_dialogs(self, limit: int) -> list[dict[str, Any]]:
        return []


async def test_add_source_keeps_client_error_code(tmp_path: Path) -> None:
    """客户端报的领域错误原样透出：没登录（not_connected）不是「源没加入」。

    两者在界面上给的修复方向不同（去初始化向导 vs 换链接/先用账号加入），
    把前者改写成 ``not_joined`` 就是把人指向错的下一步。
    """
    store = Store(tmp_path / "app.db")
    service = SourceService(store, UnconnectedTelegramClient())

    with pytest.raises(AppError) as exc:
        await service.add_source(parse_link("@music_library"))

    assert exc.value.code == "not_connected"


async def test_add_source_maps_unknown_failure_to_not_joined(tmp_path: Path) -> None:
    """认不出的底层异常仍翻译成「源不可达」（编码规范 §2.4：底层异常不越过 telegram 层）。"""
    store = Store(tmp_path / "app.db")
    service = SourceService(store, _ExplodingDialogs())

    with pytest.raises(SourceUnreachableError) as exc:
        await service.add_source(parse_link("@music_library"))

    assert exc.value.reason == "not_joined"
