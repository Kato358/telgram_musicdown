"""Web cookie 会话（FR-WEB-02，SDD §4.3）。

- ``web_login_secret`` 为空且绑定 127.0.0.1 时豁免认证。
- ``web_login_enabled=False``：显式关闭登录，任何绑定地址都放行（用户自担暴露面）。
- 监听 0.0.0.0 且未设密码（且未关登录）→ 启动直接报错退出（强制密码）。
- cookie：HttpOnly; SameSite=Lax。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from app.errors import WebAuthConfigError

SESSION_COOKIE = "tgm_session"

# 会话有效期：与 cookie max-age 一致（30 天）。口令轮换/登出即失效。
SESSION_MAX_AGE_SEC = 30 * 24 * 3600


def check_auth_config(web_host: str, web_login_secret: str, web_login_enabled: bool = True) -> None:
    """0.0.0.0 无密码启动报错退出（FR-WEB-02 验收）。

    ``web_login_enabled=False`` 是用户显式关闭登录：不再拦启动，否则这个开关
    在公网/局域网部署里永远开不下去。
    """
    if not web_login_enabled:
        return
    if web_host in ("0.0.0.0", "::") and not web_login_secret:  # noqa: S104  本行是拒绝而非绑定
        raise WebAuthConfigError()


def auth_required(web_host: str, web_login_secret: str, web_login_enabled: bool = True) -> bool:
    """是否需要认证：登录开关开着，且（secret 非空或绑定了非本机地址）。"""
    if not web_login_enabled:
        return False
    if web_login_secret:
        return True
    # noqa: S104 绑定非本机地址时需要认证，这是本函数语义而非漏洞
    return web_host not in ("127.0.0.1", "localhost")  # noqa: S104


def make_session_token(web_login_secret: str) -> str:
    """会话 token：HMAC(secret, 随机数)；无 secret 时随机串（本机豁免模式仅防误触）。"""
    nonce = secrets.token_hex(16)
    if not web_login_secret:
        return nonce
    digest = hmac.new(
        web_login_secret.encode("utf-8"), nonce.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{nonce}.{digest}"


def verify_session_token(token: str, web_login_secret: str) -> bool:
    if not token:
        return False
    if not web_login_secret:
        return True  # 本机豁免模式
    nonce, _, digest = token.partition(".")
    if not nonce or not digest:
        return False
    expect = hmac.new(
        web_login_secret.encode("utf-8"), nonce.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(digest, expect)
