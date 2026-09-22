"""linkparse 单测：4 类 URL + tg:// + 非法输入（FR-LINK-01）。"""

from __future__ import annotations

import pytest

from app.errors import LinkParseError
from app.utils.linkparse import parse_link


@pytest.mark.parametrize(
    ("url", "check"),
    [
        ("https://t.me/c/123456/789", lambda p: (p.chat_id, p.message_id) == (-100123456, 789)),
        (
            "https://t.me/somechannel/42",
            lambda p: (p.username, p.message_id) == ("somechannel", 42),
        ),
        (
            "https://t.me/somechannel/42?comment=99",
            lambda p: (p.message_id, p.comment_id) == (42, 99),
        ),
        ("tg://resolve?domain=chan&post=7", lambda p: (p.username, p.message_id) == ("chan", 7)),
    ],
)
def test_parse_link_supported_urls(url: str, check) -> None:
    assert check(parse_link(url))


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/x",
        "https://t.me/+invitehash",
        "https://t.me/somechannel",
        "not a link",
        "",
        "https://t.me/c/123456",
    ],
)
def test_parse_link_invalid_raises(url: str) -> None:
    with pytest.raises(LinkParseError):
        parse_link(url)


def test_parse_link_numeric_chat_id() -> None:
    p = parse_link("-100123456")
    assert p.chat_id == -100123456


def test_parse_link_at_username() -> None:
    p = parse_link("@channel")
    assert p.username == "channel"
