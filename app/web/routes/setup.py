"""初始化向导路由（FR-OPS-02）：status / secrets / reset。"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI

from app.web.routes import schemas
from app.web.routes.context import RouteContext


def register(app: FastAPI, ctx: RouteContext) -> None:
    """注册初始化向导路由。"""
    tg = ctx.tg

    @app.get("/api/setup/status")
    async def setup_status(_: None = Depends(ctx.check_session)) -> schemas.SetupStatusResponse:
        """初始化状态：放行 = 密钥齐备 + 已登录（音乐源可选，向导第 3 步）。"""
        from app.services.setup import setup_complete  # noqa: PLC0415

        secrets = tg.secrets
        proxy = secrets.proxy
        info = await tg.me()
        return schemas.SetupStatusResponse(
            complete=setup_complete(secrets, tg.authorized),
            has_api_id=secrets.api_id != 0,
            has_api_hash=bool(secrets.api_hash),
            has_bot_token=bool(secrets.bot_token),
            # 搜索模式（FR-SEARCH-01）：向导据此决定第 3 步要不要摆音乐源配置，
            # 控制台据此决定导航里有没有「音乐源」。取运行中服务的值 = 唯一事实源。
            search_mode=ctx.search.settings.mode,
            # 在线源（SDD §2.7）：Key 只回「有没有」不回明文（NFR-02）；开关取运行中
            # 服务的值 = 唯一事实源，前端据此决定搜索页要不要摆在线源药丸。
            chksz_enabled=bool(ctx.registry.online_sources()),
            has_chksz_key=bool(secrets.chksz_api_key),
            proxy=(
                None
                if proxy is None
                else schemas.ProxySpec(
                    scheme=proxy.scheme,
                    hostname=proxy.hostname,
                    port=proxy.port,
                )
            ),
            connected=tg.authorized,
            display_name=(info or {}).get("display_name"),
            username=(info or {}).get("username"),
        )

    @app.post("/api/setup/secrets")
    async def setup_secrets(
        req: schemas.SetupSecretsRequest, _: None = Depends(ctx.check_session)
    ) -> dict[str, Any]:
        """保存密钥到 config.yaml（不入库，NFR-02）。

        校验看合并后的内容（前端校验只是即时反馈）；已连上的客户端不热换密钥，
        故返回 ``restart_required`` 让界面说明重启后生效。
        """
        from app.services.setup import save_secrets, setup_complete  # noqa: PLC0415

        updated = save_secrets(ctx.base_dir, req.model_dump(exclude_unset=True))
        tg.secrets = updated
        alive = tg.user is not None or tg.bot is not None
        return {
            "ok": True,
            "complete": setup_complete(updated, tg.authorized),
            "restart_required": alive,
        }

    @app.post("/api/setup/reset")
    async def setup_reset(_: None = Depends(ctx.check_session)) -> schemas.ResetResponse:
        """重新执行初始化（FR-OPS-02）：清密钥段 + 退出登录 + 删会话，回到向导第 1 步。

        设置页的入口在这里；做完 ``complete`` 变 false，前端闸门随即把人送回向导。
        Bot 会话一并删：重新初始化后 bot_token 多半要换，旧会话文件会让新 token 复用旧身份。
        下载文件、历史与音乐源不动（FR-AUTH-02）。
        """
        from app.services.setup import RESET_KEYS, clear_secrets  # noqa: PLC0415

        removed = await tg.logout(purge_bot_session=True)
        tg.secrets = clear_secrets(ctx.base_dir)
        return schemas.ResetResponse(cleared_keys=list(RESET_KEYS), removed_sessions=removed)
