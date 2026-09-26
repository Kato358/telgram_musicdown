"""Pyrogram peer id 区间修复单测：频道 id ≥ 2^31 不再被判成非法 peer（回归自线上日志
``ValueError: Peer id invalid: -1002702673866``）。"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from pyrogram import raw, utils
from pyrogram.methods.advanced.resolve_peer import ResolvePeer

from app.utils.pyrogram_peer_id_patch import widen_peer_id_ranges

BIG_CHANNEL = -1002702673866  # 线上真实出现的标记 id
BIG_CHANNEL_RAW = 2702673866  # 对应原始 channel id（> 2^31）
UPSTREAM_MIN_CHANNEL_ID = -1002147483647  # pyrogram 2.0.106 原值
UPSTREAM_MIN_CHAT_ID = -2147483647


@pytest.fixture(autouse=True)
def _restore_upstream_ranges() -> Iterator[None]:
    """补丁改的是 pyrogram 模块级常量：每个用例结束后还原，避免串味。"""
    saved = (utils.MIN_CHANNEL_ID, utils.MIN_CHAT_ID)
    yield
    utils.MIN_CHANNEL_ID, utils.MIN_CHAT_ID = saved


def _use_upstream_ranges(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(utils, "MIN_CHANNEL_ID", UPSTREAM_MIN_CHANNEL_ID)
    monkeypatch.setattr(utils, "MIN_CHAT_ID", UPSTREAM_MIN_CHAT_ID)


class _StubStorage:
    def __init__(self) -> None:
        self.peers: dict[int, Any] = {}

    async def get_peer_by_id(self, peer_id: int) -> Any:
        if peer_id in self.peers:
            return self.peers[peer_id]
        raise KeyError(f"ID not found: {peer_id}")


class _StubClient:
    """只提供 resolve_peer 所需的面；invoke 模拟 Client.invoke 把响应 peer 落库。"""

    def __init__(self) -> None:
        self.is_connected = True
        self.storage = _StubStorage()
        self.requested_channel_ids: list[int] = []

    async def invoke(self, query: raw.base.TLObject) -> None:
        assert isinstance(query, raw.functions.channels.GetChannels)
        channel_id = query.id[0].channel_id
        self.requested_channel_ids.append(channel_id)
        self.storage.peers[utils.get_channel_id(channel_id)] = raw.types.InputPeerChannel(
            channel_id=channel_id, access_hash=0
        )


def test_upstream_range_rejects_big_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    """回归：pyrogram 原区间把该频道判成非法 peer（线上报错即这条）。"""
    _use_upstream_ranges(monkeypatch)
    with pytest.raises(ValueError, match=str(BIG_CHANNEL)):
        utils.get_peer_type(BIG_CHANNEL)


def test_patch_accepts_big_channel_and_widest_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    """补丁后：大 id 频道判为 channel，且标记 chat id 覆盖到 MAX_USER_ID 量级。"""
    _use_upstream_ranges(monkeypatch)
    widen_peer_id_ranges()

    assert utils.get_peer_type(BIG_CHANNEL) == "channel"
    assert utils.get_channel_id(BIG_CHANNEL) == BIG_CHANNEL_RAW
    assert utils.get_peer_type(-utils.MAX_USER_ID) == "chat"
    # 越界 id 仍须报错，补丁只放宽不放开
    with pytest.raises(ValueError):
        utils.get_peer_type(-(10**13))


async def test_resolve_peer_fails_before_patch(monkeypatch: pytest.MonkeyPatch) -> None:
    """回归（整条路径）：resolve_peer 在大 id 频道上直接抛 ValueError。"""
    _use_upstream_ranges(monkeypatch)
    with pytest.raises(ValueError, match=str(BIG_CHANNEL)):
        await ResolvePeer.resolve_peer(_StubClient(), BIG_CHANNEL)


async def test_resolve_peer_requests_raw_channel_id_after_patch() -> None:
    """补丁后：resolve_peer 用原始 channel id 取频道并返回 InputPeer（不再抛错）。"""
    widen_peer_id_ranges()
    client = _StubClient()

    peer = await ResolvePeer.resolve_peer(client, BIG_CHANNEL)

    assert client.requested_channel_ids == [BIG_CHANNEL_RAW]
    assert isinstance(peer, raw.types.InputPeerChannel)
    assert peer.channel_id == BIG_CHANNEL_RAW


def test_patch_is_idempotent() -> None:
    """重复调用结果不变。"""
    widen_peer_id_ranges()
    first = (utils.MIN_CHANNEL_ID, utils.MIN_CHAT_ID)
    widen_peer_id_ranges()
    again = (utils.MIN_CHANNEL_ID, utils.MIN_CHAT_ID)
    assert again == first


def test_patch_never_narrows_wider_upstream_ranges() -> None:
    """上游将来自己放宽（更小下界）时不被收紧回去。"""
    very_wide = -(10**15)
    utils.MIN_CHANNEL_ID = very_wide

    widen_peer_id_ranges()

    after = utils.MIN_CHANNEL_ID
    assert after == very_wide
