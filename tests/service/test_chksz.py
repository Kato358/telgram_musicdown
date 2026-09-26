"""服务层测试：ChKSz 在线源（SDD §2.7）。

覆盖三类各异的响应形状归一、音质阶梯、错误映射，以及在线源与音乐源频道在
搜索/下载/试听三条链路上的合流。FakeChkszClient 注入依赖，不打真实网络。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.chksz.client import ChkszClient, ChkszError
from app.chksz.quality import ladder, native_value, normalize, tier_of
from app.chksz.source import ChkszSource
from app.db.models import Source
from app.db.store import Store, utcnow
from app.domain import (
    PROVIDER_SCOPES,
    DownloadRequest,
    TemplateConfig,
    TrackMeta,
    chksz_unique_id,
)
from app.errors import SourceUnreachableError
from app.events import EventBus
from app.ports.music import FetchRef
from app.services.download import DownloadService
from app.services.preview import PreviewService
from app.services.source import SearchService
from tests.fakes import (
    FakeChkszClient,
    FakeUserClient,
    fake_registry,
    make_audio_message,
)

# 下面几段是 2026-09-27 对 api.chksz.com **实测抓下来的原始响应体**，不是照文档抄的。
# 文档没写的两处正是这里与文档对不上、代码栽了的地方：
#   1. 网易搜索把列表嵌在 ``data.songs``（``data.total`` 才是总数），不是 ``data`` 本身；
#   2. 网易的 ``duration`` 是**毫秒**（278961 ≈ 4'39"），不是秒。
# 这两个形状 FakeChkszClient 兜不住（它直接给行，绕过了真客户端的拆包），
# 所以另有一组用例拿 httpx MockTransport 打真 ChkszClient。
REAL_163_SEARCH_BODY = {
    "code": 200,
    "msg": "success",
    "data": {
        "songs": [
            {
                "id": 2652820720,
                "name": "晴天(深情版)",
                "artists": "Lucky小爱",
                "album": "晴天(深情版)",
                "picUrl": "http://p3.music.126.net/F0fTkmBTVykCa2o7Vgu1rQ==/109951173569626660.jpg",
                "duration": 278961,
            },
            {
                "id": 2668397359,
                "name": "晴天 (原唱 周杰伦)",
                "artists": "RyaVocal",
                "album": "晴天",
                "picUrl": "http://p3.music.126.net/-NVLOT5vt9I91LRiZV1TCQ==/109951170413587092.jpg",
                "duration": 270738,
            },
        ],
        "total": 331,
    },
}

REAL_163_DETAIL_BODY = {
    "code": 200,
    "msg": "success",
    "data": {
        "id": 1945894789,
        "url": "https://m801.music.126.net/2026/obj/wo3DlMOGwrbDjj7DisKw/b365.flac?vuutv=abc",
        "br": 1096330,
        "level": "hires",
        "size": 32629294,
        "md5": "62ee25911885c5aa5c6330f5b9434175",
        "name": "晴天 (钢琴版) [原唱: 周杰伦]",
        "artist": "纪钧瀚 (Bryan Chi)",
        "album": "钢琴放松轻听 流行轻音乐 华语经典",
        "picUrl": "https://p3.music.126.net/1Bvyzvt4qnU_8cf4Gxl/109951167394412301.jpg",
    },
}


def client_over(handler: Callable[[httpx.Request], httpx.Response]) -> ChkszClient:
    """拿 MockTransport 装一个真 ChkszClient：走真的拆包与错误映射，只换掉网络。"""
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return ChkszClient("https://api.chksz.com", "chksz_test", client=http)


# 以下给 FakeChkszClient 的行（它直接给行，绕过真客户端的拆包）：
# 网易用 data/artists/br/level，QQ 用 mid/pay/interval 文本，酷狗用 id/duration 秒数。
ROWS_163 = [{"id": 1315196858, "name": "海底", "artists": "一支榴莲", "album": "独"}]
ROWS_QQ = [
    {
        "n": 1,
        "name": "晴天",
        "singer": "周杰伦",
        "album": "叶惠美",
        "pay": "[收费]",
        "mid": "0039MnYb0qxYhV",
    }
]
ROWS_KUGO = [
    {
        "n": 1,
        "id": "48C685F679FFC7CF08B8A8341CA9DB44",
        "name": "晴天",
        "singer": "周杰伦",
        "album": "叶惠美",
        "duration": 269,
    }
]

DETAIL_163 = {
    "url": "https://m801.music.126.net/2026/x/song.flac",
    "br": 999000,
    "level": "hires",
    "name": "海底",
    "artist": "一支榴莲",
}
DETAIL_QQ = {
    "url": "https://y.qq.com/x/song.mp3",
    "interval": "3:28",
    "bitrate": "320k",
    "format": "mp3",
    "mid": "0039MnYb0qxYhV",
}


# ---- 真客户端拆包（对着实测响应体打）----


async def test_163_search_unwraps_data_songs_not_data() -> None:
    """网易把列表嵌在 ``data.songs``；按 ``data`` 取会静默拿到 0 条。

    这是 2026-09-27 实测发现的：文档只写「data 字段是结果」，真接口多嵌了一层。
    静默返回空列表最难查——不报错、只是搜不到歌。
    """
    client = client_over(lambda r: httpx.Response(200, json=REAL_163_SEARCH_BODY))
    rows = await client.search_163("周杰伦 晴天", 5, 0)
    assert [r["id"] for r in rows] == [2652820720, 2668397359]


async def test_163_duration_millis_become_seconds() -> None:
    """网易的 duration 是毫秒。不换算就会把「278961s」摆到界面上。"""
    client = client_over(lambda r: httpx.Response(200, json=REAL_163_SEARCH_BODY))
    source = ChkszSource("163", client)  # type: ignore[arg-type]
    (card, other) = (await source.search("周杰伦 晴天", 5)).items
    assert card.duration_sec == 278
    assert other.duration_sec == 270


async def test_163_resolve_unwraps_data_and_reads_actual_level() -> None:
    """解析结果同样嵌在 data 里；回报的 level 是上游实际给的那档。"""
    client = client_over(lambda r: httpx.Response(200, json=REAL_163_DETAIL_BODY))
    detail = await client.resolve_163("1945894789", "hires")
    assert detail["level"] == "hires"
    assert detail["br"] == 1096330
    assert detail["url"].endswith(".flac?vuutv=abc")


async def test_business_failure_inside_http_200_raises_with_server_msg() -> None:
    """实测 qq/kugo 挂掉时返回 503 + 中文 msg：原样转述，不吞成「搜不到」。"""
    body = {"code": 503, "msg": "上游连续失败，链路正在熔断恢复"}
    client = client_over(lambda r: httpx.Response(503, json=body))
    with pytest.raises(ChkszError) as exc:
        await client.search_qq("晴天", 5)
    assert exc.value.status == 503
    assert "熔断" in exc.value.message


# ---- 音质阶梯 ----


def test_ladder_lists_each_platform_s_own_levels_in_display_order() -> None:
    """阶梯发的是语义档位；每家只列自己真有的那一套。

    网易七档（standard/exhigh/lossless/hires/jymaster/jyeffect/sky），QQ 与酷狗五档
    （它们没有那两套混音）。母带仍是保真度顶档——两套混音排在阶梯之下，不跟它抢「最高」。
    """
    assert [o.tier for o in ladder("163")] == [
        "master",
        "hires",
        "lossless",
        "320k",
        "128k",
        "sky",
        "jyeffect",
    ]
    for provider in ("qq", "kugo"):
        assert [o.tier for o in ladder(provider)] == ["master", "hires", "lossless", "320k", "128k"]
    assert [o.tier for o in ladder("163") if o.best] == ["master"]
    assert [o.tier for o in ladder("qq") if o.best] == ["master"]


def test_mix_tiers_reach_163_under_their_native_names() -> None:
    """两套混音要真发得出去：sky/jyeffect 原样就是上游值。"""
    assert native_value("163", "sky") == "sky"
    assert native_value("163", "jyeffect") == "jyeffect"
    assert tier_of("163", "sky") == "sky"
    assert tier_of("163", "jyeffect") == "jyeffect"


def test_163_only_tier_falls_back_when_platform_lacks_it() -> None:
    """在 QQ 上选不了 sky：落回设置里的缺省档，而不是把 sky 当 flac 发出去。"""
    assert native_value("qq", "sky") == "sky"  # 没有映射就是原样
    assert normalize("qq", "sky", "320k") == "320k"
    assert normalize("kugo", "jyeffect", "master") == "master"


def test_normalize_falls_back_to_top_fidelity_tier_not_last_display_row() -> None:
    """兜底取保真度顶档：末两行是混音，配置写错不该把用户发去要 Atmos。"""
    assert normalize("163", "不存在的档", "也不存在") == "master"
    assert normalize("qq", None, "也不存在") == "master"


def test_native_value_translates_per_platform() -> None:
    """同一档在各家叫法不同，映射只此一份：设置写 320k，网易那边要 exhigh。"""
    assert native_value("163", "320k") == "exhigh"
    assert native_value("163", "master") == "jymaster"
    assert native_value("qq", "320k") == "320k"
    assert native_value("kugo", "lossless") == "flac"
    # 上游回报的原生档位能翻回语义档位，下载页显示的才是人能读的那一档
    assert tier_of("163", "jymaster") == "master"
    assert tier_of("qq", "flac") == "lossless"
    assert tier_of("qq", "weird") is None  # 认不出就别硬套一个错的档


def test_normalize_falls_back_instead_of_passing_unknown_level() -> None:
    """上游不做别名/降级映射，传它不认的值等于白跑一趟：收敛到设置里的默认档。"""
    assert normalize("163", "臻品音效", "hires") == "hires"  # 认不出的字面量才回退
    assert normalize("163", None, "flac") == "master"  # flac 不是语义档位，取最高档
    assert normalize("qq", "320k", "hires") == "320k"
    # 有人把上游原生名写进设置（flac）也认得出来，那是同一档的另一种叫法
    assert normalize("kugo", "flac", "320k") == "lossless"
    assert normalize("qq", "exhigh", "nonsense") == "master"  # 全认不出 → 最高档


@pytest.mark.parametrize("tier", ["sky", "jyeffect"])
async def test_mix_tier_falls_back_to_flac_when_url_has_no_suffix(
    tier: str, tmp_path: Path
) -> None:
    """实测 sky/jyeffect 都在 flac 容器上；URL 偶尔不带后缀时按档位兜住，别落成无扩展名。"""
    client = FakeChkszClient(detail={"url": "https://cdn.example/track", "level": tier})
    result = await ChkszSource("163", client).fetch(
        FetchRef("163", -1, ref="1315196858", quality=tier), tmp_path / "out.bin"
    )
    assert result.ext == "flac"
    assert result.level == tier


# ---- 卡片归一 ----


async def test_cards_carry_provider_ref_and_reserved_chat_id() -> None:
    """在线源卡片的身份：保留负号 chat_id + 曲目 id 哈希 + 带前缀的 file_unique_id。"""
    source = ChkszSource("163", FakeChkszClient(rows={"163": ROWS_163}, detail=DETAIL_163))
    (card,) = (await source.search("海底", 10)).items
    assert card.provider == "163"
    assert card.ref == "1315196858"
    assert card.chat_id == PROVIDER_SCOPES["163"]
    assert card.file_unique_id == chksz_unique_id("163", "1315196858")
    assert card.title == "海底"
    assert card.artist == "一支榴莲"  # 网易的键是复数 artists
    assert card.channel_title == "网易云"


async def test_qq_card_reads_mid_and_keeps_duration_unknown() -> None:
    """QQ 搜索结果没有时长：不编 0，留 None（界面显示「—」）。"""
    source = ChkszSource("qq", FakeChkszClient(rows={"qq": ROWS_QQ}, detail=DETAIL_QQ))
    (card,) = (await source.search("晴天", 10)).items
    assert card.ref == "0039MnYb0qxYhV"  # QQ 用 mid，不是 id
    assert card.duration_sec is None
    assert card.artist == "周杰伦"  # QQ 的键是 singer
    assert card.caption == "[收费]"  # 会员曲标出来，别让人下到一半才发现


async def test_kugo_card_reads_duration_seconds() -> None:
    source = ChkszSource("kugo", FakeChkszClient(rows={"kugo": ROWS_KUGO}))
    (card,) = (await source.search("晴天", 10)).items
    assert card.ref == "48C685F679FFC7CF08B8A8341CA9DB44"
    assert card.duration_sec == 269  # 酷狗给的是秒数
    assert card.chat_id == PROVIDER_SCOPES["kugo"]


async def test_row_without_id_or_title_is_dropped() -> None:
    """认不出的行进列表也是错的：缺 id 或缺歌名的直接丢。"""
    client = FakeChkszClient(
        rows={"163": [{"name": "无 id"}, {"id": 1}, {"id": 2, "name": "好"}]},
    )
    window = await ChkszSource("163", client).search("x", 10)
    assert [c.title for c in window.items] == ["好"]


# ---- 错误映射 ----


@pytest.mark.parametrize(
    ("status", "reason"),
    [(401, "invalid_key"), (403, "invalid_key"), (402, "quota_exhausted"), (429, "flood_wait")],
)
async def test_http_status_maps_to_actionable_reason(status: int, reason: str) -> None:
    """界面对每种失败要给不同的修复动作，reason 认错了就只会显示「出错了」。"""

    class Boom(FakeChkszClient):
        async def search_163(self, keyword: str, limit: int, offset: int) -> list[dict[str, Any]]:
            raise ChkszError(status, "服务端原话")

    with pytest.raises(SourceUnreachableError) as exc:
        await ChkszSource("163", Boom()).search("x", 10)
    assert exc.value.reason == reason
    assert "服务端原话" in exc.value.message  # 原样转述，不自己编


# ---- 取音频 ----


async def test_fetch_reports_actual_level_not_requested(tmp_path: Path) -> None:
    """母带请求被降级成 Hi-Res 时，如实报实际档位——谎报成所选档等于骗人。"""
    client = FakeChkszClient(detail=DETAIL_163, content=b"a" * 40)
    result = await ChkszSource("163", client, default_quality="hires").fetch(
        FetchRef("163", -1, ref="1315196858", quality="jymaster"), tmp_path / "out.flac"
    )
    assert client.resolved == [("163", "1315196858", "jymaster")]  # 语义档位翻成网易的原生值
    assert result.level == "hires"  # 上游实际给的（母带被降级），翻回语义档位
    assert result.bitrate == 999  # 网易的 br 是 bps，折成 kbps
    assert result.ext == "flac"  # 不带点：落盘模板自己补
    assert result.file_size == 40


async def test_qq_bitrate_string_is_not_reported_as_kbps(tmp_path: Path) -> None:
    """QQ/酷狗的 bitrate 是档位名（"320k"）不是 kbps，折成数字就是假读数。"""
    result = await ChkszSource("qq", FakeChkszClient(detail=DETAIL_QQ)).fetch(
        FetchRef("qq", -2, ref="0039MnYb0qxYhV", quality="320k"), tmp_path / "o.mp3"
    )
    assert result.bitrate is None
    assert result.level == "320k"
    assert result.ext == "mp3"


async def test_missing_url_reports_unsupported_level(tmp_path: Path) -> None:
    """解析不出地址不是网络问题，是这首歌没这一档：错误要说清，不然用户以为是 Key 坏了。"""
    with pytest.raises(ChkszError) as exc:
        await ChkszSource("qq", FakeChkszClient(detail={"name": "晴天"})).fetch(
            FetchRef("qq", -2, ref="m1", quality="master"), tmp_path / "o.flac"
        )
    assert exc.value.code == "chksz_no_url"
    # 说的是人话（「母带」）而不是内部档位值（master）
    assert "母带" in exc.value.message


# ---- 与音乐源合流 ----


def add_channel(store: Store, title: str = "Music Channel") -> int:
    row = Source(
        id=None,
        telegram_chat_id=-100123,
        username="music",
        title=title,
        type="channel",
        enabled=True,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    return int(store.upsert_source(row))  # type: ignore[arg-type]


def search_over(store: Store, tg: FakeUserClient, chksz: FakeChkszClient) -> SearchService:
    """在线源已启用的搜索服务。"""
    registry = fake_registry(store, tg, chksz)
    registry.apply_chksz(True, "hires")
    return SearchService(store, tg, registry=registry)


async def test_search_fans_out_to_channels_and_online_sources(tmp_path: Path) -> None:
    """一次搜索同时打音乐源频道与三个在线源，结果合成一页。"""
    store = Store(tmp_path / "app.db")
    add_channel(store)
    tg = FakeUserClient([make_audio_message(1, title="晴天")])
    chksz = FakeChkszClient(
        rows={"163": ROWS_163, "qq": ROWS_QQ, "kugo": ROWS_KUGO},
        detail=DETAIL_163,
    )

    resp = await search_over(store, tg, chksz).search("晴天", page_size=50)

    assert {c.provider for c in resp.results} == {"telegram", "163", "qq", "kugo"}
    assert {call[0] for call in chksz.search_calls} == {"163", "qq", "kugo"}
    assert resp.meta["unreachable"] == []
    assert resp.meta["cache"] == {"hits": 0, "misses": 4}


async def test_online_results_share_the_paging_pipeline(tmp_path: Path) -> None:
    """在线源与频道走同一条切页链路：续页不重不漏。"""
    store = Store(tmp_path / "app.db")
    add_channel(store)
    tg = FakeUserClient([make_audio_message(1, title="晴天")])
    service = search_over(store, tg, FakeChkszClient(rows={"163": ROWS_163 * 3}))

    first = await service.search("晴天", page=0, page_size=1)
    second = await service.search("晴天", page=1, page_size=1)

    assert first.results and second.results
    assert first.results[0].file_unique_id != second.results[0].file_unique_id


async def test_source_ids_filter_selects_negative_online_scopes(tmp_path: Path) -> None:
    """在线源的 scope 是负号；前端把「网易云」这一颗药丸当成普通源来勾选即可。"""
    store = Store(tmp_path / "app.db")
    add_channel(store)
    tg = FakeUserClient([make_audio_message(1, title="晴天")])
    service = search_over(store, tg, FakeChkszClient(rows={"163": ROWS_163}))

    resp = await service.search("晴天", source_ids=[PROVIDER_SCOPES["163"]], page_size=50)

    assert {c.provider for c in resp.results} == {"163"}


async def test_online_source_stays_out_when_disabled(tmp_path: Path) -> None:
    """没开在线源就一个在线源都不该出现（也不该白打三个平台的额度）。"""
    store = Store(tmp_path / "app.db")
    add_channel(store)
    tg = FakeUserClient([make_audio_message(1, title="晴天")])
    chksz = FakeChkszClient(rows={"163": ROWS_163})
    service = SearchService(store, tg, registry=fake_registry(store, tg, chksz))

    resp = await service.search("晴天", page_size=50)

    assert {c.provider for c in resp.results} == {"telegram"}
    assert chksz.search_calls == []


async def test_bad_key_marks_source_unreachable_not_whole_search(tmp_path: Path) -> None:
    """Key 失效只让在线源缺席，频道结果照常返回。"""
    store = Store(tmp_path / "app.db")
    add_channel(store)
    tg = FakeUserClient([make_audio_message(1, title="晴天")])

    class NoKey(FakeChkszClient):
        async def search_163(self, keyword: str, limit: int, offset: int) -> list[dict[str, Any]]:
            raise ChkszError(401, "apikey 无效或已失效")

    service = search_over(store, tg, NoKey())

    resp = await service.search("晴天", page_size=50)

    assert {c.provider for c in resp.results} == {"telegram"}
    assert {u["source_id"] for u in resp.meta["unreachable"]} == {PROVIDER_SCOPES["163"]}
    assert resp.meta["unreachable"][0]["reason"] == "invalid_key"


# ---- 下载 ----


@pytest.fixture()
def downloads(tmp_path: Path) -> Callable[..., tuple[DownloadService, Store]]:
    """在线源已启用的下载服务（落盘根指向 tmp_path/library）。"""

    def build(chksz: FakeChkszClient, quality: str = "hires") -> tuple[DownloadService, Store]:
        store = Store(tmp_path / "app.db")
        registry = fake_registry(store, FakeUserClient([]), chksz)
        registry.apply_chksz(True, quality)
        service = DownloadService(
            store,
            FakeUserClient([]),
            EventBus(),
            tmp_path / "temp",
            TemplateConfig(
                dir_template="", file_template="{title}", save_path=tmp_path / "library"
            ),
            registry=registry,
            default_quality=quality,
        )
        return service, store

    return build


def worker_row(store: Store, task_id: int) -> dict[str, Any]:
    task = store.get_task(task_id)
    assert task is not None
    return {
        "id": task.id,
        "type": task.type,
        "payload_json": task.payload_json,
        "history_id": task.history_id,
    }


async def test_download_uses_requested_quality_and_saves_real_format(
    downloads: Callable[..., tuple[DownloadService, Store]], tmp_path: Path
) -> None:
    """选哪档就问上游要哪档；落盘扩展名用实际拿到的容器。"""
    chksz = FakeChkszClient(detail=DETAIL_163, content=b"a" * 40)
    service, store = downloads(chksz)

    task_id = await service.enqueue(
        DownloadRequest(
            meta=TrackMeta(
                chat_id=PROVIDER_SCOPES["163"],
                message_id=1,
                title="海底",
                provider="163",
                ref="1315196858",
            ),
            quality="lossless",
        )
    )
    assert task_id is not None
    await service._run_task(worker_row(store, task_id))

    assert chksz.resolved == [("163", "1315196858", "lossless")]  # 网易的无损就叫 lossless
    assert store.get_task(task_id).status == "success"  # type: ignore[union-attr]
    assert len(list((tmp_path / "library").glob("**/*.flac"))) == 1
    history = store.get_history(store.get_task(task_id).history_id)  # type: ignore[union-attr]
    assert history is not None
    assert history.ext == "flac"
    assert history.file_size == 40


async def test_download_falls_back_to_settings_quality(
    downloads: Callable[..., tuple[DownloadService, Store]],
) -> None:
    """没指定音质就用设置页的默认档，不用前端再传一遍。"""
    chksz = FakeChkszClient(detail=DETAIL_QQ, content=b"b" * 20)
    service, store = downloads(chksz, quality="hires")

    task_id = await service.enqueue(
        DownloadRequest(
            meta=TrackMeta(
                chat_id=PROVIDER_SCOPES["qq"],
                message_id=2,
                title="晴天",
                provider="qq",
                ref="0039MnYb0qxYhV",
            )
        )
    )
    assert task_id is not None
    await service._run_task(worker_row(store, task_id))

    assert chksz.resolved == [("qq", "0039MnYb0qxYhV", "hires")]


async def test_quality_survives_in_task_payload(
    downloads: Callable[..., tuple[DownloadService, Store]],
) -> None:
    """音质随任务落盘：重启后恢复的队列仍按当初那档去取。"""
    service, store = downloads(FakeChkszClient(detail=DETAIL_163))

    task_id = await service.enqueue(
        DownloadRequest(
            meta=TrackMeta(
                chat_id=PROVIDER_SCOPES["163"],
                message_id=9,
                title="海底",
                provider="163",
                ref="1315196858",
            ),
            quality="jymaster",
        )
    )
    assert task_id is not None
    task = store.get_task(task_id)
    assert task is not None
    assert json.loads(task.payload_json)["quality"] == "jymaster"


async def test_repeat_download_of_same_track_is_deduped(
    downloads: Callable[..., tuple[DownloadService, Store]],
) -> None:
    """同一首在线源曲目下两次：第二次走去重，不重复花额度。"""
    service, store = downloads(FakeChkszClient(detail=DETAIL_163, content=b"a" * 40))
    meta = TrackMeta(
        chat_id=PROVIDER_SCOPES["163"],
        message_id=3,
        title="海底",
        provider="163",
        ref="1315196858",
        unique_id=chksz_unique_id("163", "1315196858"),
    )

    first = await service.enqueue(DownloadRequest(meta=meta))
    assert first is not None
    await service._run_task(worker_row(store, first))

    assert await service.enqueue(DownloadRequest(meta=meta)) is None


# ---- 试听 ----


async def test_preview_uses_its_own_quality(tmp_path: Path) -> None:
    """试听档与下载档各走各的：试听 320k 不该把下载的母带设置改掉。"""
    store = Store(tmp_path / "app.db")
    chksz = FakeChkszClient(detail=DETAIL_163, content=b"c" * 30)
    registry = fake_registry(store, FakeUserClient([]), chksz)
    registry.apply_chksz(True, "hires")
    preview = PreviewService(
        store, registry, EventBus(), tmp_path / "preview", preview_quality="320k"
    )

    pid = await preview.request_preview(
        PROVIDER_SCOPES["163"], 7, provider="163", ref="1315196858"
    )

    assert pid is not None
    assert chksz.resolved == [("163", "1315196858", "exhigh")]  # 设置写 320k，网易那边叫 exhigh
    assert store.get_preview(PROVIDER_SCOPES["163"], 7) is not None


async def test_preview_cache_key_does_not_collide_with_telegram(tmp_path: Path) -> None:
    """保留负号 chat_id 让在线源与频道的试听缓存各存各的。"""
    store = Store(tmp_path / "app.db")
    chksz = FakeChkszClient(detail=DETAIL_163, content=b"c" * 30)
    tg = FakeUserClient([make_audio_message(7)], content=b"t" * 10)
    registry = fake_registry(store, tg, chksz)
    registry.apply_chksz(True, "flac")
    preview = PreviewService(store, registry, EventBus(), tmp_path / "preview")

    online = await preview.request_preview(PROVIDER_SCOPES["163"], 7, provider="163", ref="r1")
    channel = await preview.request_preview(-100123, 7)

    assert online != channel
    assert store.get_preview(PROVIDER_SCOPES["163"], 7) is not None
    assert store.get_preview(-100123, 7) is not None


# ---- 注册表 ----


def test_registry_returns_detached_telegram_source_for_deleted_rows(tmp_path: Path) -> None:
    """源删了也要能下：音频定位在 (chat_id, message_id)，不依赖 sources 行还在。"""
    store = Store(tmp_path / "app.db")
    hit = fake_registry(store, FakeUserClient([])).by_meta("telegram", None)
    assert hit is not None
    assert hit.provider == "telegram"


def test_registry_rejects_unknown_provider(tmp_path: Path) -> None:
    store = Store(tmp_path / "app.db")
    assert fake_registry(store, FakeUserClient([])).by_meta("spotify", None) is None
