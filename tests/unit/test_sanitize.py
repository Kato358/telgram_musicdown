"""sanitize 单测：非法字符、保留名、截断（FR-DL-03）。"""

from __future__ import annotations

import pytest

from app.utils.sanitize import MAX_PATH_BYTES, is_reserved, sanitize_segment, truncate_to_bytes


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("晴天", "晴天"),
        ('a<b>c:d"e', "a_b_c_d_e"),
        ("my:file?.mp3", "my_file_.mp3"),
        ("trailing . ", "trailing"),
        ("CON", "_CON"),
        ("con.txt", "_con.txt"),
        ("LPT1", "_LPT1"),
        ("", "_"),
    ],
)
def test_sanitize_segment_replaces_illegal_and_reserved(raw: str, expected: str) -> None:
    assert sanitize_segment(raw) == expected


@pytest.mark.parametrize(
    "name", ["CON", "PRN", "AUX", "NUL", "COM1", "COM9", "LPT1", "LPT9", "nul.wav"]
)
def test_is_reserved_detects_windows_names(name: str) -> None:
    assert is_reserved(name)


@pytest.mark.parametrize("name", ["normal", "conan", "com10", "auxiliary"])
def test_is_reserved_allows_non_reserved(name: str) -> None:
    assert not is_reserved(name)


def test_truncate_to_bytes_short_path_unchanged() -> None:
    assert truncate_to_bytes("a/b/c.mp3") == "a/b/c.mp3"


def test_truncate_to_bytes_long_name_keeps_extension() -> None:
    name = "曲" * 300 + ".mp3"
    out = truncate_to_bytes(f"D:/Music/{name}")
    assert len(out.encode("utf-8")) <= MAX_PATH_BYTES
    assert out.endswith(".mp3")


def test_truncate_to_bytes_no_extension() -> None:
    name = "曲" * 300
    out = truncate_to_bytes(f"D:/Music/{name}")
    assert len(out.encode("utf-8")) <= MAX_PATH_BYTES
