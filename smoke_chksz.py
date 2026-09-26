"""冒烟脚本：拿真 Key 跑一遍在线源链路（搜索 → 解析 → 下载 → 落盘）。

不进测试套：它要真 Key、要真网络，跑一次是一次。验的是单测覆盖不到的那部分——
上游的真实响应形状能不能被归一、真实音质能不能落盘。

    set TGM_CHKSZ_API_KEY=chksz_xxx
    python smoke_chksz.py [关键词] [音质档位]
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

from app.chksz.client import ChkszClient
from app.chksz.quality import label_of, native_value
from app.chksz.source import ChkszSource
from app.db.store import Store
from app.domain import PROVIDER_SCOPES, DownloadRequest, TemplateConfig, TrackMeta
from app.events import EventBus
from app.ports.music import FetchRef
from app.registry import SourceRegistry
from app.services.download import DownloadService

KEYWORD = sys.argv[1] if len(sys.argv) > 1 else "周杰伦 晴天"
TIER = sys.argv[2] if len(sys.argv) > 2 else "hires"
EXIT_USAGE = 2


def ref_of(provider: str, ref: str, tier: str) -> FetchRef:
    return FetchRef(provider, PROVIDER_SCOPES[provider], ref=ref, quality=tier)


async def probe_search(client: ChkszClient, provider: str) -> int:
    """搜 → 解析 → 下载一个片段，返回失败数。"""
    source = ChkszSource(provider, client, default_quality=TIER)
    print(f"\n=== {source.label} ===")
    try:
        window = await source.search(KEYWORD, 5)
    except Exception as e:  # noqa: BLE001  冒烟脚本要看到原始错误
        print(f"  搜索失败：{e}")
        return 1
    print(f"  搜到 {len(window.items)} 条（has_more={window.has_more}）")
    for card in window.items[:3]:
        dur = "时长未知" if card.duration_sec is None else f"{card.duration_sec}s"
        print(f"    · {card.title} — {card.artist or '?'} [{dur}] ref={card.ref}")
    if not window.items:
        return 1

    card = window.items[0]
    dest = Path(tempfile.mkdtemp(prefix="tgm-smoke-")) / "audio.bin"
    try:
        result = await source.fetch(ref_of(provider, card.ref, TIER), dest)
    except Exception as e:  # noqa: BLE001
        print(f"  解析/下载失败：{e}")
        return 1
    got = label_of(result.level or "")
    print(
        f"  下载 OK：{result.file_size} 字节 · 容器 {result.ext} · 码率 {result.bitrate} · "
        f"实际档位「{got}」（请求「{label_of(TIER)}」，发给上游的是 "
        f"{native_value(provider, TIER)}）"
    )
    if got != label_of(TIER):
        print("  ↑ 上游降级了：这首歌没有所选那一档，落盘与历史行记的是实际档位")
    return 0


async def probe_download(client: ChkszClient) -> int:
    """走完整条队列：入队 → worker → 文件与历史行。"""
    print("\n=== 落盘链路（网易云）===")
    base = Path(tempfile.mkdtemp(prefix="tgm-smoke-dl-"))
    store = Store(base / "app.db")
    tg = _NoTelegram()
    registry = SourceRegistry(store, tg, tg, chksz_client=client)  # type: ignore[arg-type]
    registry.apply_chksz(True, TIER)
    service = DownloadService(
        store,
        tg,  # type: ignore[arg-type]
        EventBus(),
        base / "temp",
        TemplateConfig(
            dir_template="", file_template="{artist} - {title}", save_path=base / "library"
        ),
        registry=registry,
        default_quality=TIER,
    )
    window = await ChkszSource("163", client, default_quality=TIER).search(KEYWORD, 1)
    if not window.items:
        print("  没有可下的结果，跳过")
        return 1
    card = window.items[0]
    task_id = await service.enqueue(
        DownloadRequest(
            meta=TrackMeta(
                chat_id=card.chat_id,
                message_id=card.message_id,
                title=card.title,
                artist=card.artist,
                provider=card.provider,
                ref=card.ref,
                unique_id=card.file_unique_id,
            ),
            quality=TIER,
        )
    )
    if task_id is None:
        print("  入队被去重挡掉了")
        return 1
    task = store.get_task(task_id)
    assert task is not None
    await service._run_task(  # noqa: SLF001
        {
            "id": task_id,
            "type": "link",
            "payload_json": task.payload_json,
            "history_id": task.history_id,
        }
    )
    final = store.get_task(task_id)
    print(f"  任务状态：{final.status if final else '?'}（{final.error if final else ''}）")  # type: ignore[union-attr]
    saved = [p for p in (base / "library").glob("**/*") if p.is_file()]
    for path in saved:
        print(f"  落盘：{path.relative_to(base)}（{path.stat().st_size} 字节）")
    if not saved or not final or final.status != "success":  # type: ignore[union-attr]
        return 1
    history = store.get_history(final.history_id)  # type: ignore[union-attr]
    if history is not None:
        print(f"  历史行：ext={history.ext} 大小={history.file_size} 码率={history.bitrate}")
    return 0


class _NoTelegram:
    """只为了满足装配签名：在线源这条路上一次 Telegram 都不该被调到。"""

    def __getattr__(self, name: str):  # noqa: ANN204
        raise AssertionError(f"在线源链路不该碰 Telegram 客户端（调到了 {name}）")


async def main() -> int:
    key = os.environ.get("TGM_CHKSZ_API_KEY", "")
    if not key:
        print("需要 TGM_CHKSZ_API_KEY（chksz_ 开头）：去 api.chksz.com 账户页复制")
        return EXIT_USAGE
    client = ChkszClient(os.environ.get("TGM_CHKSZ_BASE_URL", "https://api.chksz.com"), key)
    failures = 0
    try:
        for provider in PROVIDER_SCOPES:
            failures += await probe_search(client, provider)
        failures += await probe_download(client)
    finally:
        await client.aclose()
    print("\n结果：" + ("全部通过" if failures == 0 else f"{failures} 项失败"))
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
