"""Web cookie 会话（FR-WEB-02，SDD §4.3）。

- ``web_login_secret`` 为空且绑定 127.0.0.1 时豁免认证。
- ``web_login_enabled=False``：显式关闭登录，任何绑定地址都放行（用户自担暴露面）。
- 监听 0.0.0.0 且未设密码（且未关登录）→ 启动直接报错退出（强制密码）。
- cookie：HttpOnly; SameSite=Lax。
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from pathlib import Path

from app.errors import WebAuthConfigError

logger = logging.getLogger(__name__)

SESSION_COOKIE = "tgm_session"

# 会话有效期：与 cookie max-age 一致（30 天）。口令轮换/登出即失效。
SESSION_MAX_AGE_SEC = 30 * 24 * 3600

# 对外绑定地址：这些地址上的无口令控制台等于公开面板，故一律不放过。
# 这里只是比较用的常量，不是真的去 bind 通配地址。
_PUBLIC_BINDS = ("0.0.0.0", "::")  # noqa: S104


def check_auth_config(web_host: str, web_login_secret: str, web_login_enabled: bool = True) -> None:
    """0.0.0.0 无密码启动报错退出（FR-WEB-02 验收）。

    ``web_login_enabled=False`` 是用户显式关闭登录：不再拦启动，否则这个开关
    在公网/局域网部署里永远开不下去——但那时 0.0.0.0 上就是一块敞开的面板，
    所以留一条 warning：日志里能看见这次暴露是有意为之，而不是忘了配口令。
    """
    if not web_login_enabled:
        if web_host in _PUBLIC_BINDS:
            logger.warning(
                "控制台登录已显式关闭（TGM_WEB_LOGIN_ENABLED=false）且监听 %s："
                "任何能访问该端口的人都能操作下载与 Telegram 账号",
                web_host,
            )
        return
    if web_host in _PUBLIC_BINDS and not web_login_secret:
        raise WebAuthConfigError()


def refused_hint(config_path: Path) -> str:
    """启动被拒后该做什么（FR-WEB-02）：日志直接说清改哪里，不要只丢一句「拒绝」。

    只报错不给出路的日志，在 Docker 里等于让人去翻源码。这里点名配置文件路径（容器内
    路径；compose 默认把宿主机 ``./data`` 挂成 ``/data/data``，宿主位置一并写出）、
    等价的环境变量与显式放行的开关——三条都是单行改动。
    """
    return (
        "绑 0.0.0.0 的部署必须先设控制台口令，三条路任选其一：\n"
        f"  1) 在 {config_path} 里加 web_login_secret: <口令>（向导首次保存也写这个文件，"
        "compose 部署即宿主机 ./data/config.yaml）\n"
        "  2) 加环境变量 TGM_WEB_LOGIN_SECRET=<口令>（优先级更高，compose 里 environment 加一行）\n"
        "  3) 确实要无口令公开访问：TGM_WEB_LOGIN_ENABLED=false（自行承担暴露面）"
    )


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
