"""`utils.fs.move_into_place` 单测：同盘 rename、跨盘退化复制、错误不外溢成半成品文件。

真实场景（FR-CFG-02 部署布局）：temp 与 save_path 是两个 bind 挂载点，Linux 的
rename(2) 跨 mount 一律 EXDEV，同盘/跨盘两条路径都得走通。
"""

from __future__ import annotations

import errno
import os
import shutil
from pathlib import Path

import pytest

from app.utils.fs import STAGING_SUFFIX, is_cross_device_error, move_into_place


def _exdev_on(src: Path):
    """造一个只在 `src` 这条路径上报 EXDEV 的 os.replace（其余调用原样放行）。"""
    real = os.replace

    def fake(*args: object, **kwargs: object) -> None:
        if Path(str(args[0])) == src:
            raise OSError(errno.EXDEV, "Invalid cross-device link", str(args[0]), str(args[1]))
        real(*args, **kwargs)  # type: ignore[arg-type]

    return fake


def test_same_device_renames(tmp_path: Path) -> None:
    src = tmp_path / "temp" / "chunk"
    dst = tmp_path / "library" / "song.flac"
    src.parent.mkdir()
    dst.parent.mkdir()
    src.write_bytes(b"abc")

    move_into_place(src, dst)

    assert dst.read_bytes() == b"abc"
    assert not src.exists()


def test_replaces_existing_target(tmp_path: Path) -> None:
    # 落盘语义是「覆盖」而不是「合并进目录」：重下同名文件不该变成报错
    src = tmp_path / "chunk"
    dst = tmp_path / "song.flac"
    src.write_bytes(b"new")
    dst.write_bytes(b"old")

    move_into_place(src, dst)

    assert dst.read_bytes() == b"new"


def test_cross_device_falls_back_to_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / "data" / "temp" / "task_1_1733523"
    dst = tmp_path / "downloads" / "Marika" / "Lil' Goldfish.flac"
    src.parent.mkdir(parents=True)
    dst.parent.mkdir(parents=True)
    src.write_bytes(b"flac-bytes")
    os.utime(src, (1_700_000_000, 1_700_000_000))
    monkeypatch.setattr(os, "replace", _exdev_on(src))

    move_into_place(src, dst)

    assert dst.read_bytes() == b"flac-bytes"
    assert not src.exists()
    # mtime 一起搬过来：曲库扫描按 (mtime, size) 判断有没有变
    assert int(dst.stat().st_mtime) == 1_700_000_000
    # 中转文件不留残骸
    assert [p.name for p in dst.parent.iterdir()] == ["Lil' Goldfish.flac"]


def test_cross_device_creates_missing_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "temp" / "chunk"
    dst = tmp_path / "library" / "Marika" / "song.flac"
    src.parent.mkdir()
    src.write_bytes(b"x")
    monkeypatch.setattr(os, "replace", _exdev_on(src))

    move_into_place(src, dst)

    assert dst.read_bytes() == b"x"


def test_cross_device_failure_keeps_target_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 复制中途失败（写满盘/断开）：正式路径上必须仍然没有文件，源分片留给重试
    src = tmp_path / "temp" / "chunk"
    dst = tmp_path / "library" / "song.flac"
    src.parent.mkdir()
    dst.parent.mkdir()
    src.write_bytes(b"payload")
    real_copy2 = shutil.copy2

    def half_copy(a: object, b: object, **kw: object) -> None:
        real_copy2(a, b, **kw)
        Path(str(b)).write_bytes(b"")  # 半截
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(os, "replace", _exdev_on(src))
    monkeypatch.setattr(shutil, "copy2", half_copy)

    with pytest.raises(OSError, match="No space left"):
        move_into_place(src, dst)

    assert not dst.exists()
    assert src.read_bytes() == b"payload"
    assert not list(dst.parent.glob(f"*{STAGING_SUFFIX}*"))


def test_other_oserror_is_not_swallowed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 权限类错误不触发复制兜底：复制也一样失败，只会把真实原因盖掉
    src = tmp_path / "data" / "chunk"
    dst = tmp_path / "library" / "song.flac"
    src.parent.mkdir()
    dst.parent.mkdir()
    src.write_bytes(b"x")

    def denied(*args: object, **kwargs: object) -> None:
        raise OSError(errno.EACCES, "Permission denied", str(args[0]), str(args[1]))

    monkeypatch.setattr(os, "replace", denied)

    with pytest.raises(OSError, match="Permission denied"):
        move_into_place(src, dst)
    assert not dst.exists()


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (OSError(errno.EXDEV, "x"), True),
        (OSError(errno.EACCES, "x"), False),
    ],
)
def test_is_cross_device_error(exc: OSError, expected: bool) -> None:
    assert is_cross_device_error(exc) is expected


def test_windows_not_same_device_is_cross_device() -> None:
    # Windows 跨盘重命名：WinError 17，errno 映射不保证是 EXDEV
    exc = OSError(errno.EACCES, "The system cannot move the file to a different disk drive")
    exc.winerror = 17  # type: ignore[attr-defined]
    assert is_cross_device_error(exc) is True
