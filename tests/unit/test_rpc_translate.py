"""底层异常 → 领域 reason（编码规范 §2.4；FR-SEARCH-01「FloodWait 要可见」）。

搜索页按 `meta.unreachable[].reason` 选文案（`flood_wait` → 「Telegram 限流：稍后再搜」，
其余 → 「源无法访问：先用该账号加入频道」）。reason 分错档，用户就会照着一句错的修复
指引去折腾权限，所以这一层值得钉住。
"""

from __future__ import annotations

from pathlib import Path

from pyrogram.errors import ChatAdminRequired, FloodWait, RPCError

from app.db.models import Source
from app.db.store import Store
from app.errors import SourceUnreachableError
from app.services.source import SearchService
from app.telegram import user_client


def test_floodwait_is_reported_as_rate_limit() -> None:
    # 限流不是权限：以前它落进兜底的 not_joined，界面于是说「先用该账号加入频道」
    err = user_client._translate_rpc(FloodWait(value=30))
    assert err.reason == "flood_wait"
    assert "30" in err.message


def test_rpc_reasons_keep_their_own_bucket() -> None:
    assert user_client._translate_rpc(ChatAdminRequired()).reason == "banned"
    assert user_client._translate_rpc(RPCError()).reason == "not_joined"


class _RateLimitedClient:
    """搜索时抛限流（FR-SEARCH-01）：整请求不该倒，只把那个源标出来。"""

    async def search_messages(self, chat_id: int, query: str, limit: int, offset: int) -> list:
        raise SourceUnreachableError("flood_wait", "FloodWait 30s")


async def test_search_surfaces_rate_limit_per_source(tmp_path: Path) -> None:
    store = Store(tmp_path / "app.db")
    source_id = store.upsert_source(
        Source(id=None, telegram_chat_id=-100123, title="Music Channel", type="channel")
    )
    service = SearchService(store, _RateLimitedClient())  # type: ignore[arg-type]

    resp = await service.search("晴天")

    assert resp.results == []
    assert resp.meta["unreachable"] == [{"source_id": source_id, "reason": "flood_wait"}]
