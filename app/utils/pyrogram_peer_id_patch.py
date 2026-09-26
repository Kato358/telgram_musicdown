"""Pyrogram 2.0.106 对「大 id 频道」的 peer 类型误判修复（可用性 + NFR-10 可观测性）。

现象：收到这类频道的 update 时 ``Client.handle_updates`` 抛
``ValueError: Peer id invalid: -1002702673866``，以 "Task exception was never
retrieved" 形式刷日志，且该批 update 一并丢失。

根因：``pyrogram.utils.get_peer_type`` 用硬编码区间判定 peer 类型，其中
``MIN_CHANNEL_ID = -1002147483647`` 只覆盖原始 channel id < 2^31 的频道。而标记 id
恒为 ``MAX_CHANNEL_ID - channel_id`` = ``-(10**12 + channel_id)``（``get_peer_id`` /
``get_channel_id``，本次不改），Telegram 又早已发放 ≥ 2^31 的 channel id：原始 id 一大，
标记 id 就落到 MIN_CHANNEL_ID 之外，合法频道被判成非法 peer。``get_peer_type`` 的唯一
调用点是 ``resolve_peer``，因此这条路径同时影响：收到频道 update（handle_updates 取频道
差分前先 resolve）、以及我们自己按 id 取频道/取消息（同样报 PeerIdInvalid）。

修复：把判定区间放宽到标记 id 的完整取值域——id 上限与同文件 ``MAX_USER_ID``
（999999999999）同量级，故下界取 ``-(10**12 + 999999999999)``。只放宽、不收紧，
幂等，可在启动阶段与其它兼容补丁一起调用。
"""

from __future__ import annotations

from pyrogram import utils

# 标记 id 下界：-(10**12 + raw_id)，raw_id 上限 999999999999
MIN_CHANNEL_ID = -1_999_999_999_999
MIN_CHAT_ID = -999_999_999_999


def widen_peer_id_ranges() -> None:
    """放宽 pyrogram 的 peer id 判定区间（幂等：只放宽、不收紧）。"""
    utils.MIN_CHANNEL_ID = min(utils.MIN_CHANNEL_ID, MIN_CHANNEL_ID)

    utils.MIN_CHAT_ID = min(utils.MIN_CHAT_ID, MIN_CHAT_ID)
