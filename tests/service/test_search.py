"""搜索链路测试（FR-SEARCH-01~03，SDD §2.6）。

覆盖四件事：并发扇出与上限、二级缓存命中（含 L2 跨实例）、超窗转后台补齐、
正确分页（旧实现把上游 offset 用两遍，翻页必然丢结果）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from app.appsettings import SearchCacheSettings, SearchSettings
from app.db.models import Source
from app.db.store import Store
from app.errors import SourceUnreachableError
from app.services.search_cache import SearchCache
from app.services.source import SearchService
from tests.fakes import make_audio_message


class RecordingClient:
    """按源记账的假客户端：可注入每源延迟与异常，用于并发/缓存/补齐断言。"""

    def __init__(
        self,
        results: dict[int, list[dict[str, Any]]],
        *,
        delays: dict[int, float] | None = None,
        errors: dict[int, Exception] | None = None,
        global_results: list[dict[str, Any]] | None = None,
        global_error: Exception | None = None,
    ) -> None:
        self.results = results
        self.delays = delays or {}
        self.errors = errors or {}
        self.calls: list[int] = []  # 每次上游调用的 chat_id（按序列，便于断言「没再打」）
        self.active = 0
        self.max_active = 0
        # 全局搜索（FR-SEARCH-01 global 模式）：脚本化的跨对话命中集
        self.global_results = global_results or []
        self.global_error = global_error
        self.global_calls: list[str] = []  # 每次 searchGlobal 的关键词

    def calls_for(self, chat_id: int) -> int:
        return self.calls.count(chat_id)

    async def search_messages(
        self, chat_id: int, query: str, limit: int, offset: int
    ) -> list[dict[str, Any]]:
        self.calls.append(chat_id)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if chat_id in self.errors:
                raise self.errors[chat_id]
            delay = self.delays.get(chat_id, 0.0)
            if delay:
                await asyncio.sleep(delay)
            return self.results.get(chat_id, [])[offset : offset + limit]
        finally:
            self.active -= 1

    async def search_global(self, query: str, limit: int) -> list[dict[str, Any]]:
        """searchGlobal：一次调用覆盖全部对话（上游已按 Audio 过滤，这里按标题模拟服务端匹配）。"""
        self.global_calls.append(query)
        if self.global_error is not None:
            raise self.global_error
        matched = [
            m
            for m in self.global_results
            if query.lower() in str(m.get("audio", {}).get("title", "")).lower()
        ]
        return matched[:limit]


def _new_store(tmp_path: Path, *chat_ids: int) -> tuple[Store, list[int]]:
    store = Store(tmp_path / "app.db")
    ids = [
        store.upsert_source(
            Source(id=None, telegram_chat_id=chat, title=f"频道{chat}", type="channel")
        )
        for chat in chat_ids
    ]
    return store, ids


def _service(
    store: Store,
    client: RecordingClient,
    *,
    fanout: int = 4,
    window: float = 1.0,
    cache: SearchCacheSettings | None = None,
    mode: str = "sources",
) -> SearchService:
    settings = SearchSettings(
        cache=cache or SearchCacheSettings(),
        fanout=fanout,
        sync_window_sec=window,
        mode=mode,
    )
    return SearchService(store, client, SearchCache(store, settings.cache), settings)


def _tracks(chat_id: int, count: int, *, title: str = "晴天") -> list[dict[str, Any]]:
    return [make_audio_message(chat_id * 1000 + i, title=title) for i in range(count)]


def _dated(message_id: int, date: str, *, title: str = "晴天") -> dict[str, Any]:
    msg = make_audio_message(message_id, title=title)
    msg["message_date"] = date
    return msg


# ---- 并发扇出 ----


async def test_sources_are_searched_concurrently(tmp_path: Path) -> None:
    # 串行实现下 max_active 恒为 1；并发扇出后 3 个源同时在飞
    chats = (-1001, -1002, -1003)
    store, _ = _new_store(tmp_path, *chats)
    client = RecordingClient(
        {chat: _tracks(chat, 2) for chat in chats},
        delays=dict.fromkeys(chats, 0.05),  # 有延迟才有在飞窗口可观测
    )
    service = _service(store, client)

    resp = await service.search("晴天")

    assert len(resp.results) == 6
    assert client.max_active == 3
    await service.aclose()


async def test_fanout_caps_concurrency(tmp_path: Path) -> None:
    # 扇出上限是「别把账号打进 FloodWait」的闸门，不是吞吐旋钮
    chats = (-1001, -1002, -1003, -1004, -1005)
    store, _ = _new_store(tmp_path, *chats)
    client = RecordingClient(
        {chat: _tracks(chat, 1) for chat in chats}, delays=dict.fromkeys(chats, 0.05)
    )
    service = _service(store, client, fanout=2)

    await service.search("晴天")

    assert client.max_active == 2
    await service.aclose()


async def test_source_failure_only_marks_that_source(tmp_path: Path) -> None:
    # FR-SEARCH-01：限流要可见；单源倒掉不连累其它源
    store, ids = _new_store(tmp_path, -1001, -1002)
    client = RecordingClient(
        {-1002: _tracks(-1002, 2)},
        errors={-1001: SourceUnreachableError("flood_wait", "FloodWait 30s")},
    )
    service = _service(store, client)

    resp = await service.search("晴天")

    assert len(resp.results) == 2
    assert resp.meta["unreachable"] == [{"source_id": ids[0], "reason": "flood_wait"}]
    await service.aclose()


# ---- 二级缓存 ----


async def test_second_search_hits_cache_without_upstream(tmp_path: Path) -> None:
    store, _ = _new_store(tmp_path, -1001)
    client = RecordingClient({-1001: _tracks(-1001, 3)})
    service = _service(store, client)

    first = await service.search("晴天")
    calls_after_first = len(client.calls)
    second = await service.search("晴天")

    assert len(client.calls) == calls_after_first  # 第二次一个上游请求都没发
    assert second.results == first.results
    assert second.meta["cache"] == {"hits": 1, "misses": 0}
    assert first.meta["cache"] == {"hits": 0, "misses": 1}
    await service.aclose()


async def test_cache_key_ignores_case_and_extra_spaces(tmp_path: Path) -> None:
    store, _ = _new_store(tmp_path, -1001)
    client = RecordingClient({-1001: _tracks(-1001, 2)})
    service = _service(store, client)

    await service.search("Jay  Chou")
    calls = len(client.calls)
    await service.search("  jay chou  ")

    assert len(client.calls) == calls
    await service.aclose()


async def test_refresh_bypasses_cache(tmp_path: Path) -> None:
    store, _ = _new_store(tmp_path, -1001)
    client = RecordingClient({-1001: _tracks(-1001, 2)})
    service = _service(store, client)

    await service.search("晴天")
    calls = len(client.calls)
    resp = await service.search("晴天", refresh=True)

    assert len(client.calls) == calls + 1
    assert resp.meta["cache"] == {"hits": 0, "misses": 1}
    await service.aclose()


async def test_l2_cache_survives_new_service_instance(tmp_path: Path) -> None:
    # L2 的意义：重启后同词仍然免外呼（L1 会随进程消失）
    store, _ = _new_store(tmp_path, -1001)
    client = RecordingClient({-1001: _tracks(-1001, 3)})
    first_service = _service(store, client)
    await first_service.search("晴天")
    await first_service.aclose()

    second_client = RecordingClient({-1001: _tracks(-1001, 3)})
    second_service = _service(store, second_client)
    resp = await second_service.search("晴天")

    assert second_client.calls == []
    assert len(resp.results) == 3
    assert resp.meta["cache"] == {"hits": 1, "misses": 0}
    await second_service.aclose()


async def test_empty_result_is_negative_cached(tmp_path: Path) -> None:
    # 空结果不落 L2（pansou 同款），但 L1 短负缓存要挡住同词突发重打
    store, _ = _new_store(tmp_path, -1001)
    client = RecordingClient({-1001: []})
    service = _service(store, client)

    await service.search("查无此歌")
    calls = len(client.calls)
    resp = await service.search("查无此歌")

    assert len(client.calls) == calls
    assert resp.results == []
    assert store.search_cache_totals() == (0, 0)
    await service.aclose()


async def test_source_delete_invalidates_its_cache(tmp_path: Path) -> None:
    store, ids = _new_store(tmp_path, -1001, -1002)
    client = RecordingClient({chat: _tracks(chat, 1) for chat in (-1001, -1002)})
    service = _service(store, client)

    await service.search("晴天")
    assert store.search_cache_totals()[1] == 2

    await service.invalidate_source(ids[0])

    assert store.search_cache_totals()[1] == 1
    await service.aclose()


# ---- singleflight ----


async def test_concurrent_identical_searches_share_one_upstream_call(tmp_path: Path) -> None:
    # 缓存只挡「先后」，singleflight 挡「同时」：同词并发只打一次上游
    store, _ = _new_store(tmp_path, -1001)
    client = RecordingClient({-1001: _tracks(-1001, 2)}, delays={-1001: 0.1})
    service = _service(store, client)

    responses = await asyncio.gather(*(service.search("晴天") for _ in range(4)))

    assert client.calls_for(-1001) == 1
    assert all(len(resp.results) == 2 for resp in responses)
    await service.aclose()


# ---- 同步窗口 + 后台补齐 ----


async def test_slow_source_returns_partial_then_fills_cache(tmp_path: Path) -> None:
    store, ids = _new_store(tmp_path, -1001, -1002)
    client = RecordingClient(
        {-1001: _tracks(-1001, 2), -1002: _tracks(-1002, 3)},
        delays={-1002: 0.3},
    )
    service = _service(store, client, window=0.05)

    partial = await service.search("晴天")

    assert len(partial.results) == 2  # 只等到快的那个源
    assert partial.meta["partial"] is True
    assert partial.meta["pending_sources"] == [ids[1]]
    assert partial.meta["unreachable"] == []

    # 后台补齐跑完（写缓存由它自己完成），第二次同词直接拿完整结果且不再外呼
    await asyncio.gather(*tuple(service._bg))
    complete = await service.search("晴天")

    assert complete.meta["partial"] is False
    assert len(complete.results) == 5
    assert client.calls_for(-1002) == 1
    await service.aclose()


async def test_background_fill_failure_is_not_fatal(tmp_path: Path) -> None:
    store, _ = _new_store(tmp_path, -1001, -1002)
    client = RecordingClient(
        {-1002: _tracks(-1002, 1)},
        delays={-1002: 0.2},
        errors={-1002: SourceUnreachableError("banned", "no access")},
    )
    service = _service(store, client, window=0.05)

    await service.search("晴天")
    await asyncio.gather(*tuple(service._bg), return_exceptions=True)

    # 补齐失败只记日志：后续搜索照常，且不会把失败当结果缓存下来
    again = await service.search("晴天", refresh=False)
    assert again.results == []
    await service.aclose()


# ---- 分页 ----


async def test_pagination_walks_all_results_without_overlap(tmp_path: Path) -> None:
    # 旧实现 page>0 恒空（上游 offset 与本地切片各减一次）
    store, _ = _new_store(tmp_path, -1001)
    client = RecordingClient({-1001: _tracks(-1001, 30)})
    service = _service(store, client)

    page0 = await service.search("晴天", page=0, page_size=20)
    page1 = await service.search("晴天", page=1, page_size=20)

    keys0 = {c.message_id for c in page0.results}
    keys1 = {c.message_id for c in page1.results}
    assert len(keys0) == 20
    assert len(keys1) == 10
    assert keys0.isdisjoint(keys1)
    assert len(keys0 | keys1) == 30
    assert page0.meta["has_more"] is True
    assert page1.meta["has_more"] is False
    await service.aclose()


async def test_pagination_keeps_results_from_all_sources(tmp_path: Path) -> None:
    # 旧实现把「每源一页」合并后切片，被挤出的结果永久丢失（上游 offset 已推过它们）
    store, _ = _new_store(tmp_path, -1001, -1002)
    client = RecordingClient({-1001: _tracks(-1001, 15), -1002: _tracks(-1002, 15)})
    service = _service(store, client)

    page0 = await service.search("晴天", page=0, page_size=20)
    page1 = await service.search("晴天", page=1, page_size=20)

    seen = [c.message_id for c in page0.results] + [c.message_id for c in page1.results]
    assert len(seen) == 30
    assert len(set(seen)) == 30
    await service.aclose()


async def test_pagination_is_stable_when_window_deepens(tmp_path: Path) -> None:
    # 上游的返回顺序不是我们的排序（日期）顺序，所以窗口加深后整体次序会变。
    # 实测复现：`q=周杰伦, page_size=5` 第 0/1 页都出现同一条 —— 直接切「当前窗口」的必然结果。
    store, _ = _new_store(tmp_path, -1001)
    client = RecordingClient(
        {
            -1001: [
                _dated(1, "2020-01-01T00:00:00"),
                _dated(2, "2020-02-01T00:00:00"),
                _dated(3, "2026-01-01T00:00:00"),
            ]
        }
    )
    service = _service(store, client)

    page0 = await service.search("晴天", page=0, page_size=2)
    page1 = await service.search("晴天", page=1, page_size=2)

    ids0 = [c.message_id for c in page0.results]
    ids1 = [c.message_id for c in page1.results]
    assert ids0 == [2, 1]  # need=2 的窗口里只有这两条（按日期新→旧）
    assert ids1 == [3]  # 加深后新发现的 3 追加在后：既不重复 1，也不把它弄丢
    assert set(ids0).isdisjoint(ids1)
    assert set(ids0) | set(ids1) == {1, 2, 3}
    assert page1.meta["has_more"] is False
    await service.aclose()


async def test_dedupe_drops_same_file_from_other_source(tmp_path: Path) -> None:
    store, _ = _new_store(tmp_path, -1001, -1002)
    # 同一个文件被两个频道转发：file_unique_id 相同，只该出一条
    forwarded = make_audio_message(7)
    client = RecordingClient({-1001: [forwarded], -1002: [dict(forwarded)]})
    service = _service(store, client)

    resp = await service.search("晴天")

    assert len(resp.results) == 1
    await service.aclose()


# ---- 排序与二次筛选（FR-SEARCH-03）----


async def test_sort_by_size_and_date_filter(tmp_path: Path) -> None:
    store, _ = _new_store(tmp_path, -1001)
    small = make_audio_message(1, size=100)
    big = make_audio_message(2, size=900)
    client = RecordingClient({-1001: [small, big]})
    service = _service(store, client)

    by_size = await service.search("晴天", sort="size")
    assert [c.message_id for c in by_size.results] == [2, 1]

    filtered = await service.search("晴天", filters={"size_min": 500})
    assert [c.message_id for c in filtered.results] == [2]
    assert filtered.meta["filters"] is True
    await service.aclose()


async def test_unknown_sort_falls_back_to_relevance(tmp_path: Path) -> None:
    store, _ = _new_store(tmp_path, -1001)
    # 标题整字段命中的排在只有说明命中的前面
    exact = make_audio_message(1, title="晴天")
    loose = make_audio_message(2, title="夜曲")
    client = RecordingClient({-1001: [loose, exact]})
    service = _service(store, client)

    resp = await service.search("晴天", sort="nonsense")

    assert resp.meta["sort"] == "relevance"
    assert [c.message_id for c in resp.results] == [1, 2]
    await service.aclose()


# ---- 全局模式（FR-SEARCH-01 global，v0.14）----


def _global_tracks(spec: list[tuple[int, int]], *, title: str = "晴天") -> list[dict[str, Any]]:
    """(chat_id, 条数) → 跨对话的命中集（频道名随 chat_id 生成，供归源断言）。"""
    return [
        make_audio_message(
            chat_id * 1000 + i,
            title=title,
            chat_id=chat_id,
            chat_title=f"频道{chat_id}",
        )
        for chat_id, count in spec
        for i in range(count)
    ]


async def test_global_mode_needs_no_sources(tmp_path: Path) -> None:
    # 全局模式的立身之本：一个源都没配也能搜（范围 = 账号加入的对话）
    store, _ = _new_store(tmp_path)  # 不添加任何源
    client = RecordingClient({}, global_results=_global_tracks([(-1001, 2), (-1002, 1)]))
    service = _service(store, client, mode="global")

    resp = await service.search("晴天")

    assert len(resp.results) == 3
    assert resp.meta["mode"] == "global"
    assert "reason" not in resp.meta  # 不是「没有启用源」那个早退
    assert client.calls == []  # 逐源链路一次都没走
    assert len(client.global_calls) == 1  # 一次请求覆盖全部对话
    assert {c.channel_title for c in resp.results} == {"频道-1001", "频道-1002"}
    await service.aclose()


async def test_global_mode_hits_cache_on_repeat(tmp_path: Path) -> None:
    store, _ = _new_store(tmp_path)
    client = RecordingClient({}, global_results=_global_tracks([(-1001, 3)]))
    service = _service(store, client, mode="global")

    first = await service.search("晴天")
    second = await service.search("晴天")

    assert len(client.global_calls) == 1
    assert second.results == first.results
    assert first.meta["cache"] == {"hits": 0, "misses": 1}
    assert second.meta["cache"] == {"hits": 1, "misses": 0}
    await service.aclose()


async def test_global_mode_flood_wait_is_visible(tmp_path: Path) -> None:
    # 全局模式没有「逐源不可达」这一档：限流就是整次失败，如实报出来，不静默给空结果
    store, _ = _new_store(tmp_path)
    client = RecordingClient(
        {}, global_error=SourceUnreachableError("flood_wait", "FloodWait 30s")
    )
    service = _service(store, client, mode="global")

    with pytest.raises(SourceUnreachableError) as exc:
        await service.search("晴天")

    assert exc.value.reason == "flood_wait"
    await service.aclose()


async def test_global_mode_source_ids_filter_by_chat(tmp_path: Path) -> None:
    # 全局结果按全局时间序、无法限定源集合：传了 source_ids 就在本地按 chat_id 过滤
    store, ids = _new_store(tmp_path, -1001)
    client = RecordingClient({}, global_results=_global_tracks([(-1001, 1), (-1002, 1)]))
    service = _service(store, client, mode="global")

    resp = await service.search("晴天", source_ids=[ids[0]])

    assert [c.chat_id for c in resp.results] == [-1001]
    await service.aclose()


async def test_global_mode_pagination_deepens_window(tmp_path: Path) -> None:
    store, _ = _new_store(tmp_path)
    client = RecordingClient({}, global_results=_global_tracks([(-1001, 30)]))
    service = _service(store, client, mode="global")

    page0 = await service.search("晴天", page=0, page_size=20)
    page1 = await service.search("晴天", page=1, page_size=20)

    ids0 = {c.message_id for c in page0.results}
    ids1 = {c.message_id for c in page1.results}
    assert len(ids0) == 20
    assert len(ids1) == 10
    assert ids0.isdisjoint(ids1)
    assert page0.meta["has_more"] is True
    assert page1.meta["has_more"] is False
    assert len(client.global_calls) == 2  # 深一页要把窗口从 20 提到 40
    await service.aclose()


async def test_apply_settings_switches_pipeline(tmp_path: Path) -> None:
    # 设置页保存搜索模式后即时生效（FR-CFG-03）：同一次运行内从逐源切到全局
    store, _ = _new_store(tmp_path, -1001)
    client = RecordingClient(
        {-1001: _tracks(-1001, 2)}, global_results=_global_tracks([(-1001, 5)])
    )
    service = _service(store, client)

    by_source = await service.search("晴天")
    assert len(by_source.results) == 2

    service.apply_settings(SearchSettings(mode="global"))
    by_global = await service.search("晴天")

    assert len(by_global.results) == 5
    assert by_global.meta["mode"] == "global"
    assert len(client.global_calls) == 1
    await service.aclose()
