"""浏览器下载服务单测（FR-DL-08）：准备落盘、命名、TTL 内可重复取用与清扫。

对应需求：浏览器下载只把这一首取回临时区交给浏览器——不入队、不写历史、不落
``save_path``；失败按 ``AppError`` 抛给路由层（前端行内展示，不把用户带到错误页）。
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

from app.db.store import Store
from app.domain import TemplateConfig, TrackMeta
from app.errors import AppError
from app.services.browser_download import (
    MAX_PREPARED,
    SUBDIR,
    BrowserCacheStats,
    BrowserDownloadService,
)
from tests.fakes import (
    FakeChkszClient,
    FakeUserClient,
    fake_registry,
    make_audio_message,
)

CONTENT = b"x" * 100


class CancellingClient(FakeUserClient):
    """取到一半被取消：磁盘上留下半截分片，服务层必须收拾干净。"""

    async def download_media(  # type: ignore[override]
        self, message_ref: dict, file_name: str, progress: object = None
    ) -> None:
        # ASYNC240 豁免：假客户端允许直接写文件（与 tests/fakes 同款）
        Path(file_name).write_bytes(self.content[: len(self.content) // 2])  # noqa: ASYNC240
        raise asyncio.CancelledError


class VanishingClient(FakeUserClient):
    """取数期间被「清理缓存」删掉分片：`stat` 拿不到文件，必须是业务错误而不是裸 500。"""

    async def download_media(  # type: ignore[override]
        self, message_ref: dict, file_name: str, progress: object = None
    ) -> None:
        path = Path(file_name)
        path.write_bytes(self.content)  # noqa: ASYNC240  假客户端允许直接写文件
        path.unlink()  # noqa: ASYNC240  模拟 clear() 在取数途中把这份删了


def make_service(
    tmp_path: Path,
    client: FakeUserClient,
    *,
    ttl_sec: int = 3600,
    chksz: FakeChkszClient | None = None,
) -> tuple[BrowserDownloadService, Store]:
    """按生产同构装配：真 Store + 真来源索引 + 试听/下载之外的第三个服务。"""
    store = Store(tmp_path / "app.db")
    cfg = TemplateConfig(file_template="{artist} - {title}", save_path=tmp_path / "library")
    registry = fake_registry(store, client, chksz)
    if chksz is not None:
        registry.apply_chksz(("163",), "hires")
    service = BrowserDownloadService(registry, tmp_path / "temp", cfg, ttl_sec=ttl_sec)
    return service, store


def make_meta(message_id: int = 1, **overrides: object) -> TrackMeta:
    fields: dict[str, object] = {
        "chat_id": -100123,
        "message_id": message_id,
        "title": "晴天",
        "artist": "周杰伦",
        "ext": "mp3",
        # 卡片声明的字节数（前端 refOf 会带上）：服务端据此对账，防止半截文件交给浏览器
        "file_size": len(CONTENT),
    }
    fields.update(overrides)
    return TrackMeta(**fields)  # type: ignore[arg-type]


async def test_prepare_lands_temp_file_and_serves_repeatedly(tmp_path: Path) -> None:
    client = FakeUserClient([make_audio_message(1)], content=CONTENT)
    service, store = make_service(tmp_path, client)
    try:
        prepared = await service.prepare(make_meta())

        # 命名走曲库那份模板 + 卡片上的扩展名（Telegram 源不报 ext，卡片上那份就是事实）
        assert prepared.file_name == "周杰伦 - 晴天.mp3"
        assert prepared.size == len(CONTENT)
        temp = tmp_path / "temp" / SUBDIR / f"{prepared.token}.part"
        assert temp.read_bytes() == CONTENT
        # 没有落 save_path，也没写任何历史：这正是「浏览器下载」与正式下载的分界
        assert not (tmp_path / "library").exists()
        assert store.list_history() == []

        # TTL 内可重复取：浏览器分段取（Range）与暂停后续传都要靠同一份文件
        assert service.resolve(prepared.token) == (temp, "周杰伦 - 晴天.mp3")
        assert service.resolve(prepared.token) == (temp, "周杰伦 - 晴天.mp3")
    finally:
        store.close()


async def test_resolve_unknown_token_returns_none(tmp_path: Path) -> None:
    service, store = make_service(tmp_path, FakeUserClient([]))
    try:
        assert service.resolve("nope") is None
    finally:
        store.close()


async def test_resolve_returns_none_when_file_vanished(tmp_path: Path) -> None:
    """登记还在但文件没了（被别处删掉）：按 404 处理，不让 FileResponse 去撞空路径。"""
    client = FakeUserClient([make_audio_message(1)], content=CONTENT)
    service, store = make_service(tmp_path, client)
    try:
        prepared = await service.prepare(make_meta())
        (tmp_path / "temp" / SUBDIR / f"{prepared.token}.part").unlink()
        assert service.resolve(prepared.token) is None
    finally:
        store.close()


async def test_unknown_provider_is_an_app_error(tmp_path: Path) -> None:
    service, store = make_service(tmp_path, FakeUserClient([]))
    try:
        with pytest.raises(AppError) as err:
            await service.prepare(make_meta(provider="nope"))
        assert err.value.code == "browser_download_failed"
    finally:
        store.close()


async def test_fetch_failure_leaves_no_temp_file(tmp_path: Path) -> None:
    """上游取数炸了：翻译成 AppError，且不留半截临时文件（崩在半路也不污染临时区）。"""
    client = FakeUserClient([make_audio_message(1)], content=CONTENT)
    client.flood_queue = [30]  # 取数时抛限流
    service, store = make_service(tmp_path, client)
    try:
        with pytest.raises(AppError) as err:
            await service.prepare(make_meta())
        assert err.value.code == "browser_download_failed"
        assert list((tmp_path / "temp" / SUBDIR).iterdir()) == []
    finally:
        store.close()


async def test_cancelled_fetch_leaves_no_temp_file(tmp_path: Path) -> None:
    """取消（前端断开 / 进程收尾）同样不留半截分片——不是所有失败都会抛 AppError。"""
    service, store = make_service(tmp_path, CancellingClient([make_audio_message(1)]))
    try:
        with pytest.raises(asyncio.CancelledError):
            await service.prepare(make_meta())
        assert list((tmp_path / "temp" / SUBDIR).iterdir()) == []
    finally:
        store.close()


async def test_size_mismatch_is_rejected(tmp_path: Path) -> None:
    """声明大小对不上就不交出去（FR-DL-03）：半截文件宁可重下，也不塞进用户下载目录。"""
    client = FakeUserClient([make_audio_message(1)], content=CONTENT)
    client.size_override = 40  # 上游只给回一半
    service, store = make_service(tmp_path, client)
    try:
        with pytest.raises(AppError) as err:
            await service.prepare(make_meta())
        assert "大小不符" in err.value.message
        assert list((tmp_path / "temp" / SUBDIR).iterdir()) == []
    finally:
        store.close()


async def test_mime_fills_missing_extension(tmp_path: Path) -> None:
    """Telegram 源不回报 ext、卡片文件名也可能没有点——这时按 mime 定扩展名。

    没有这一档兜底，用户拿到的是一个没有扩展名的文件（浏览器不会替他补），
    双击打不开。真实一例：频道里的 flac，卡片 ext 为 None、mime 是 audio/flac。
    """
    client = FakeUserClient([make_audio_message(1)], content=CONTENT)
    service, store = make_service(tmp_path, client)
    try:
        prepared = await service.prepare(make_meta(ext=None, mime="audio/flac"))
        assert prepared.file_name == "周杰伦 - 晴天.flac"
    finally:
        store.close()


async def test_fetched_extension_wins_over_card(tmp_path: Path) -> None:
    """卡片说 mp3、上游实际给 flac：文件名按**实测**来（在线源会静默升降档）。

    在线源声明不出大小，故这一例的 file_size 留空（那一档校验由适配器自己兜）。
    """
    chksz = FakeChkszClient(detail={"url": "https://cdn.example/track.flac", "level": "hires"})
    service, store = make_service(tmp_path, FakeUserClient([]), chksz=chksz)
    try:
        prepared = await service.prepare(
            make_meta(
                ext="mp3", mime="audio/mpeg", provider="163", ref="1315196858", file_size=None
            )
        )
        assert prepared.file_name == "周杰伦 - 晴天.flac"
    finally:
        store.close()


async def test_client_supplied_extension_is_normalized_and_whitelisted(tmp_path: Path) -> None:
    """卡片的 ext 是客户端可控字段：归一（`FLAC` → `flac`）+ 按 `AUDIO_EXTS` 白名单收口。

    认不出的值（`../evil`、`.`）**整段丢掉**——不给扩展名，而不是把怪字符拼进文件名。
    每个变体用不同的 message_id：同一首歌会被复用（见下面复用的用例），拿不到各自的名字。
    """
    client = FakeUserClient([make_audio_message(i) for i in range(1, 8)], content=CONTENT)
    service, store = make_service(tmp_path, client)
    try:
        upper = await service.prepare(make_meta(1, ext="FLAC"))
        assert upper.file_name == "周杰伦 - 晴天.flac"

        for index, weird in enumerate(("../evil", ".", " ", "xyz", ".flac"), start=2):
            prepared = await service.prepare(make_meta(index, ext=weird))
            expected = "周杰伦 - 晴天.flac" if weird == ".flac" else "周杰伦 - 晴天"
            assert prepared.file_name == expected, weird
            assert "/" not in prepared.file_name and "\\" not in prepared.file_name
    finally:
        store.close()


async def test_card_extension_wins_over_mime(tmp_path: Path) -> None:
    """三档优先级的中间一档：卡片既有 ext 又有 mime 时用 ext（mime 只是兜底）。"""
    client = FakeUserClient([make_audio_message(1)], content=CONTENT)
    service, store = make_service(tmp_path, client)
    try:
        prepared = await service.prepare(make_meta(ext="mp3", mime="audio/flac"))
        assert prepared.file_name == "周杰伦 - 晴天.mp3"
    finally:
        store.close()


@pytest.mark.parametrize(
    ("mime", "expected"),
    [
        ("audio/mpeg; codecs=mp3", "周杰伦 - 晴天.mp3"),  # 带参数：按分号前那截查表
        ("AUDIO/FLAC", "周杰伦 - 晴天.flac"),  # 大小写
        ("  audio/x-m4a  ", "周杰伦 - 晴天.m4a"),  # 前后空白
        ("application/octet-stream", "周杰伦 - 晴天"),  # 认不出：宁可不给
        (None, "周杰伦 - 晴天"),
    ],
)
async def test_mime_fallback_branches(
    tmp_path: Path, mime: str | None, expected: str
) -> None:
    """mime 兜底的三条分支：带参数 / 大小写 / 空白要认得出，认不出就不给扩展名。"""
    client = FakeUserClient([make_audio_message(1)], content=CONTENT)
    service, store = make_service(tmp_path, client)
    try:
        prepared = await service.prepare(make_meta(ext=None, mime=mime))
        assert prepared.file_name == expected
    finally:
        store.close()


async def test_cleared_during_fetch_is_an_app_error(tmp_path: Path) -> None:
    """清理缓存与在途取数撞上：`stat` 失败要按业务错误回，别漏成裸 500。"""
    service, store = make_service(tmp_path, VanishingClient([make_audio_message(1)]))
    try:
        with pytest.raises(AppError) as err:
            await service.prepare(make_meta())
        assert err.value.code == "browser_download_failed"
        assert "清理" in err.value.message
    finally:
        store.close()


async def test_stats_and_clear_cover_the_temp_dir(tmp_path: Path) -> None:
    """设置页那张缓存卡的两件事：占用按磁盘实际值报，清理缓存把它连登记一起清掉。

    清理后旧 token 必须失效（登记留着只会指向已删文件、给出 404 之外的怪状态）。
    """
    client = FakeUserClient([make_audio_message(1)], content=CONTENT)
    service, store = make_service(tmp_path, client)
    try:
        prepared = await service.prepare(make_meta())
        assert service.stats() == BrowserCacheStats(bytes=len(CONTENT), count=1)

        service.clear()

        assert service.stats() == BrowserCacheStats(bytes=0, count=0)
        assert service.resolve(prepared.token) is None
        assert list((tmp_path / "temp" / SUBDIR).iterdir()) == []
    finally:
        store.close()


async def test_clear_keeps_download_shards(tmp_path: Path) -> None:
    """清理缓存只管自己的子目录：下载服务的 ``task_*`` 分片同区放着，一个都不动。"""
    client = FakeUserClient([make_audio_message(1)], content=CONTENT)
    service, store = make_service(tmp_path, client)
    try:
        await service.prepare(make_meta())
        shard = tmp_path / "temp" / "task_7_1"
        shard.write_bytes(b"downloading")

        service.clear()

        assert shard.read_bytes() == b"downloading"
    finally:
        store.close()


async def test_repeat_request_reuses_the_prepared_file(tmp_path: Path) -> None:
    """同一首歌短时间内再点：复用已有那份——不重打上游、不新写文件、连 token 都不变。

    这是「重复下载同一首歌」该有的代价：第一次是真取数，后面每次都是毫秒级的响应。
    """
    client = FakeUserClient([make_audio_message(1)], content=CONTENT)
    service, store = make_service(tmp_path, client)
    try:
        first = await service.prepare(make_meta())
        second = await service.prepare(make_meta())

        assert second == first
        assert client.download_calls == 1  # 上游只被取了一次
        assert len(list((tmp_path / "temp" / SUBDIR).iterdir())) == 1
    finally:
        store.close()


async def test_reuse_key_covers_song_and_quality(tmp_path: Path) -> None:
    """复用键的边界：同一首歌的不同档位、不同的歌，都必须各取各的（不能串）。"""
    client = FakeUserClient([make_audio_message(1), make_audio_message(2)], content=CONTENT)
    service, store = make_service(tmp_path, client)
    try:
        tier_a = await service.prepare(make_meta(1), "320k")
        tier_b = await service.prepare(make_meta(1), "flac")
        other = await service.prepare(make_meta(2))

        assert len({tier_a.token, tier_b.token, other.token}) == 3
        assert client.download_calls == 3
    finally:
        store.close()


async def test_reuse_falls_back_to_a_fresh_fetch(tmp_path: Path) -> None:
    """文件没了就老老实实重取：复用只省重复的活，不改「取不到就报错」的语义。"""
    client = FakeUserClient([make_audio_message(1)], content=CONTENT)
    service, store = make_service(tmp_path, client)
    try:
        first = await service.prepare(make_meta())
        (tmp_path / "temp" / SUBDIR / f"{first.token}.part").unlink()

        second = await service.prepare(make_meta())

        assert second.token != first.token
        assert client.download_calls == 2
        assert (tmp_path / "temp" / SUBDIR / f"{second.token}.part").exists()
    finally:
        store.close()


async def test_reuse_expires_with_ttl(tmp_path: Path) -> None:
    """超 TTL 的准备不再复用（``ttl_sec=0`` 把时间压缩掉）：过期就重取。"""
    client = FakeUserClient([make_audio_message(1)], content=CONTENT)
    service, store = make_service(tmp_path, client, ttl_sec=0)
    try:
        first = await service.prepare(make_meta())
        second = await service.prepare(make_meta())

        assert second.token != first.token
        assert client.download_calls == 2
    finally:
        store.close()


async def test_stale_preparation_is_swept(tmp_path: Path) -> None:
    """超 TTL 未被取走的准备（用户关页签）在下一次准备时被清掉。

    ``ttl_sec=0`` 把时间压缩掉：内存登记与磁盘文件两条清扫路径都得命中。
    """
    client = FakeUserClient([make_audio_message(1), make_audio_message(2)], content=CONTENT)
    service, store = make_service(tmp_path, client, ttl_sec=0)
    try:
        first = await service.prepare(make_meta(1))
        second = await service.prepare(make_meta(2))

        assert service.resolve(first.token) is None
        assert not (tmp_path / "temp" / SUBDIR / f"{first.token}.part").exists()
        assert service.resolve(second.token) is not None  # 刚准备的这份还在
    finally:
        store.close()


async def test_sweep_collects_orphans_but_never_touches_download_shards(tmp_path: Path) -> None:
    """磁盘上比 TTL 更旧的**孤儿**文件（进程崩在半路，登记已随进程消失）也要收。

    同时钉住边界：清扫只看自己的 `temp/browser/`，下载服务的 `task_*` 分片同区放着，
    一个字节都不该被动到。
    """
    client = FakeUserClient([make_audio_message(1)], content=CONTENT)
    service, store = make_service(tmp_path, client)
    try:
        browser_dir = tmp_path / "temp" / SUBDIR
        browser_dir.mkdir(parents=True, exist_ok=True)
        old = time.time() - 7200
        orphan = browser_dir / "deadbeef.part"  # 无登记、mtime 两小时前
        orphan.write_bytes(b"old")
        os.utime(orphan, (old, old))
        shard = tmp_path / "temp" / "task_7_1"  # 下载服务的在传分片
        shard.write_bytes(b"downloading")
        os.utime(shard, (old, old))

        await service.prepare(make_meta())

        assert not orphan.exists()
        assert shard.read_bytes() == b"downloading"
    finally:
        store.close()


async def test_overflow_evicts_oldest_and_keeps_disk_bounded(tmp_path: Path) -> None:
    """并存上限（FR-DL-08）：TTL 内可重复取，但临时文件不能无界增长。"""
    ids = list(range(1, MAX_PREPARED + 2))
    client = FakeUserClient([make_audio_message(i) for i in ids], content=CONTENT)
    service, store = make_service(tmp_path, client)
    try:
        prepared = [await service.prepare(make_meta(i)) for i in ids]

        assert service.resolve(prepared[0].token) is None  # 最旧的被挤掉
        assert not (tmp_path / "temp" / SUBDIR / f"{prepared[0].token}.part").exists()
        assert service.resolve(prepared[-1].token) is not None  # 最新的还在
        assert len(list((tmp_path / "temp" / SUBDIR).iterdir())) == MAX_PREPARED
    finally:
        store.close()
