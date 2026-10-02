"""落盘移动：把「临时分片 → 正式文件」包成一个跨挂载点安全的动作。

为什么需要它：`temp` 与 `save_path` 在真实部署里常常是两个挂载点。docker-compose 把
宿主机的 `./data` 与 `./downloads` 分别 bind 到 `/data/data` 与 `/data/downloads`，
而 Linux 的 rename(2) 不允许跨 mount 改名——即使两个挂载点背后是同一块盘。于是
NFR-01 校验通过后那句 `os.replace(temp, target)` 直接炸出
`[Errno 18] Invalid cross-device link`，任务反复重试、音频反复重下，永远落不了盘。

策略：同盘仍走一次原子 rename；跨盘退化成「复制到目标同目录 → 同目录原子 rename →
删源」。复制到目标同目录是关键——正式路径上要么没有文件、要么就是完整文件，
中途失败只留下 `.xdev` 中转名（非音频扩展名，曲库扫描不认它），不会把半截文件
放进 save_path 让曲库/试听读到。
"""

from __future__ import annotations

import errno
import logging
import os
import shutil
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

#: 跨设备复制时放在目标目录里的中转名后缀（配合前导点：既非音频扩展名，也不刺眼）
STAGING_SUFFIX = ".xdev"

#: Windows 跨盘重命名带的是 WinError 17（ERROR_NOT_SAME_DEVICE）；它映射到哪个 errno
#: 在各版本 CPython 里不一致，故 winerror 单独认一次。
_WIN_NOT_SAME_DEVICE = 17


def is_cross_device_error(exc: OSError) -> bool:
    """这个 OSError 是不是「源和目标不在同一设备/挂载点」。"""
    return exc.errno == errno.EXDEV or getattr(exc, "winerror", None) == _WIN_NOT_SAME_DEVICE


def move_into_place(src: Path, dst: Path) -> None:
    """把 `src` 变成 `dst`：同盘 rename（原子），跨盘复制后原子替换。

    同步函数：调用方用 `asyncio.to_thread` 包（复制大文件会占住线程很久）。
    失败时抛原异常，且不会在 `dst` 留下不完整文件。
    """
    try:
        src.replace(dst)
        return
    except OSError as exc:
        if not is_cross_device_error(exc):
            raise
        logger.warning("cross-device move %s -> %s: falling back to copy", src, dst)
    _copy_then_replace(src, dst)


def _copy_then_replace(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    staging = dst.parent / f".{dst.name}{STAGING_SUFFIX}.{os.getpid()}.{uuid.uuid4().hex[:8]}"
    try:
        # copy2 连 mtime 一起搬：曲库扫描按 (mtime, size) 判有没有变
        shutil.copy2(src, staging)
        _fsync(staging)
        staging.replace(dst)
    except BaseException:
        _unlink_quietly(staging)
        raise
    # 目标已经就位，源分片只是白占磁盘：删不掉（别处正打开它）也只记日志，
    # 不把一次成功的落盘报成失败。
    try:
        src.unlink(missing_ok=True)
    except OSError:
        logger.warning("cross-device move left temp chunk behind: %s", src)


def _fsync(path: Path) -> None:
    """把复制完的字节真正刷到盘上，再允许它出现在正式路径（掉电/重启不留空壳文件）。"""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return  # 刚被并发清理收掉：交给上层的 replace 去报
    try:
        os.fsync(fd)
    except OSError as exc:
        # 某些网络/虚拟文件系统不支持 fsync：数据已经写完，不该因此判落盘失败
        logger.debug("fsync unsupported for %s: %s", path, exc)
    finally:
        os.close(fd)


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("could not remove staging file %s", path)
