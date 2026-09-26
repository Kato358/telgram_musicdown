"""认证路由（FR-AUTH-01/02、FR-WEB-02）：Web 控制台登录 + Telegram 登录 + me。

两类认证刻意分开：
- **Web 会话**（``/api/auth/login``、``/api/auth/session``）：口令换 cookie，保护控制台本身；
- **Telegram 账号**（``send-code``/``sign-in``/``logout``）：登录被下载/搜索使用的账号。

前者是 HTTP 层准入，后者是业务身份；退出 Telegram 账号不动 Web 会话 cookie（SRS §14）。
"""

from __future__ import annotations

import hmac
import re
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response

from app.errors import AuthError
from app.web import auth as web_auth
from app.web.routes import schemas
from app.web.routes.context import RouteContext

_PHONE_RE = re.compile(r"^\+?\d{6,15}$")


def _normalize_phone(raw: str) -> str:
    """手机号归一：去掉空格/连字符/括号（用户从通讯录复制时常带这些）。"""
    return re.sub(r"[\s\-()]", "", raw or "")


def register(app: FastAPI, ctx: RouteContext) -> None:
    """注册认证路由。"""
    tg = ctx.tg

    @app.post("/api/auth/login")
    async def web_login(req: schemas.LoginRequest, response: Response) -> schemas.LoginResponse:
        """Web 控制台登录（FR-WEB-02）：校验 web_login_secret，通过则签发会话 cookie。

        豁免模式（未设口令且仅监听本机）直接放行，不签 cookie——前端据此跳过登录页。
        """
        if not web_auth.auth_required(ctx.web_host, ctx.web_login_secret):
            return schemas.LoginResponse()
        if not hmac.compare_digest(req.secret, ctx.web_login_secret):
            # 不区分「口令错」与「不该访问」之外的细节，避免给爆破者额外信息
            raise HTTPException(status_code=401, detail="invalid credentials")
        token = web_auth.make_session_token(ctx.web_login_secret)
        response.set_cookie(
            web_auth.SESSION_COOKIE,
            token,
            httponly=True,
            samesite="lax",
            secure=False,  # 本应用常以 http 提供（局域网/反代终止 TLS），故不强制 secure
            max_age=web_auth.SESSION_MAX_AGE_SEC,
            path="/",
        )
        return schemas.LoginResponse()

    @app.post("/api/auth/session/logout")
    async def web_logout(response: Response) -> schemas.LoginResponse:
        """退出 Web 控制台（清 cookie）。与 Telegram 账号登出互不影响。"""
        response.delete_cookie(web_auth.SESSION_COOKIE, path="/")
        return schemas.LoginResponse()

    @app.get("/api/auth/session")
    async def web_session(request: Request) -> dict[str, bool]:
        """当前 Web 会话是否有效（前端登录闸门用；豁免模式恒 true）。"""
        if not web_auth.auth_required(ctx.web_host, ctx.web_login_secret):
            return {"required": False, "authenticated": True}
        token = request.cookies.get(web_auth.SESSION_COOKIE, "")
        ok = web_auth.verify_session_token(token, ctx.web_login_secret)
        return {"required": True, "authenticated": ok}

    @app.post("/api/auth/telegram/send-code")
    async def send_code(
        req: schemas.SendCodeRequest, _: None = Depends(ctx.check_session)
    ) -> dict[str, Any]:
        """发验证码（FR-AUTH-01）：连接 UserClient；已有有效会话则直接报已登录。"""
        if not tg.secrets.has_credentials:
            raise AuthError("secrets_missing", "先在第 1 步保存 api_id / api_hash，才能登录")
        phone = _normalize_phone(req.phone)
        if not _PHONE_RE.match(phone):
            raise AuthError("phone_invalid", "手机号要带国家码，例如 +8613800000000")
        await tg.connect_user()  # 用当前 config.yaml 的密钥建连（向导第 1 步保存的值）
        if tg.authorized:
            return {"code_hash": "", "authorized": True, "me": await tg.me()}
        if tg.user is None:  # pragma: no cover - connect_user 必建 client
            raise AuthError("not_authorized", "Telegram 客户端未就绪，请重试")
        code_hash = await tg.user.send_code(phone)
        return {"code_hash": code_hash, "authorized": False, "me": None}

    @app.post("/api/auth/telegram/sign-in")
    async def sign_in(
        req: schemas.SignInRequest, _: None = Depends(ctx.check_session)
    ) -> dict[str, Any]:
        """登录（FR-AUTH-01）：会话文件落 sessions/；两步验证缺密码报 password_required。"""
        if tg.user is None:
            raise AuthError("not_authorized", "先发送验证码，再提交验证码")
        await tg.user.sign_in(req.phone, req.code, req.code_hash, req.password)
        tg.mark_authorized()
        await tg.start_bot_configured()  # bot_token 已配置时按新会话启动 Bot（FR-AUTH-04）
        me: dict[str, Any] = await tg.user.get_me()
        return me

    @app.post("/api/auth/logout")
    async def logout(_: None = Depends(ctx.check_session)) -> schemas.LogoutResponse:
        """退出登录（FR-AUTH-02）：断开 User Client 并删除会话文件，下载文件与数据库不动。

        删会话文件才算真退出——留着它下次启动会判「已有有效会话」直接放行。
        这里是退出 Telegram 账号，不是退出 Web 控制台，故不动会话 cookie。
        """
        removed = await tg.logout()
        return schemas.LogoutResponse(removed_sessions=removed)

    @app.get("/api/me")
    async def me(_: None = Depends(ctx.check_session)) -> schemas.MeResponse:
        """账号信息 + 连接状态（FR-AUTH-01/02）。"""
        info = await tg.me()
        if info is None:
            return schemas.MeResponse(connected=False)
        return schemas.MeResponse(
            display_name=info.get("display_name"),
            username=info.get("username"),
            premium=bool(info.get("premium", False)),
            connected=True,
        )
