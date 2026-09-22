"""t.me / tg:// 链接解析（FR-LINK-01，SDD §2.1）。

支持 4 类 URL：
- ``https://t.me/c/<internal>/<msg>``
- ``https://t.me/<username>/<msg>``
- ``https://t.me/<username>/<msg>?comment=<id>``
- ``tg://`` 等价形式

解析失败抛 LinkParseError（结构化错误，不入队）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.errors import LinkParseError


@dataclass(slots=True)
class ParsedLink:
    """解析结果：chat 标识（用户名或 -100 前缀内部 id）+ 消息 id。"""

    username: str | None = None
    chat_id: int | None = None
    message_id: int | None = None
    comment_id: int | None = None


_TG_HTTP = re.compile(
    r"^https?://t\.me/(?:(?P<c>c)/)?(?P<chat>[^/?#]+)(?:/(?P<msg>\d+))?",
    re.IGNORECASE,
)
_TG_SCHEME = re.compile(
    r"^tg://(resolve|join)?\?(?:.*&)?domain=(?P<chat>[^&#]+)(?:.*&)?post=(?P<msg>\d+)?",
    re.IGNORECASE,
)


def _parse_http(text: str, m: re.Match[str]) -> ParsedLink:
    """解析 t.me HTTP 形式。"""
    out = ParsedLink()
    chat = m.group("chat")
    msg = m.group("msg")
    if m.group("c"):
        # 内部 id：t.me/c/<internal>/<msg>，Telegram 侧实际 chat_id = -100<internal>
        if not msg:
            raise LinkParseError(code="invalid_link", message="internal link missing message id")
        out.chat_id = int(f"-100{chat}")
    elif chat.startswith("+"):
        raise LinkParseError(code="invalid_link", message="invite links need to be joined first")
    else:
        out.username = chat
    if msg:
        out.message_id = int(msg)
    cm = re.search(r"[?&]comment=(\d+)", text, re.IGNORECASE)
    if cm:
        out.comment_id = int(cm.group(1))
    if out.message_id is None and out.comment_id is None:
        raise LinkParseError(code="invalid_link", message="link missing message id")
    return out


def parse_link(url: str) -> ParsedLink:
    """解析单条链接；非法输入抛 LinkParseError。"""
    text = url.strip()
    if not text:
        raise LinkParseError(code="invalid_link", message="empty link")

    m = _TG_HTTP.match(text)
    if m:
        return _parse_http(text, m)

    m = _TG_SCHEME.match(text)
    if m:
        msg = m.group("msg")
        if not msg:
            raise LinkParseError(code="invalid_link", message="tg:// link missing post id")
        return ParsedLink(username=m.group("chat"), message_id=int(msg))

    # 纯数字：直接 chat_id
    if re.fullmatch(r"-?\d+", text):
        return ParsedLink(chat_id=int(text))

    # @username
    if re.fullmatch(r"@[\w]+", text):
        return ParsedLink(username=text[1:])

    raise LinkParseError(code="invalid_link", message=f"unrecognized link: {text[:60]}")
