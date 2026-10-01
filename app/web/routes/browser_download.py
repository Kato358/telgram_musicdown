"""浏览器下载路由（FR-DL-08）：一次性准备 + 附件流。

两步是为了一件事：**失败要能在界面里说清楚**。浏览器顶层导航没法读响应体，若把取数
放在 GET 里，上游报错（无权限 / FloodWait / 平台下架）只会把用户带到一个 JSON 错误页，
行内提示、其它行状态全丢。故：

1. ``POST`` 先在服务端把这首取回临时区；失败按标准错误包络返回 400，前端行内展示。
2. ``GET`` 只负责把一个**已经在本地**的文件按附件发出去。

第 2 步不删文件、也不作废 token：`FileResponse` 自带 Range（206），浏览器与下载管理器
会分段取、暂停后续传——只允许取一次的话，它们拿到的是片段、第二次直接 404，
用户看到的是「下载到一半坏了」。生命周期交给服务层的 TTL 与并存上限。

认证与其它接口一致（cookie 会话）：浏览器顶层导航会带上 ``tgm_session``，
故第 2 步不需要前端拿 blob 再造 URL——那会让大文件在内存里过一遍。
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse

from app.domain import meta_from_dict
from app.web.routes import schemas
from app.web.routes.context import RouteContext


def register(app: FastAPI, ctx: RouteContext) -> None:
    """注册浏览器下载路由。"""
    browser = ctx.browser_downloads

    @app.post("/api/search/browser-download")
    async def prepare_browser_download(
        req: schemas.DownloadsRequest, _: None = Depends(ctx.check_session)
    ) -> dict[str, Any]:
        """把一首取回服务端临时区，返回下载地址（FR-DL-08）。

        入参与 ``POST /api/downloads`` / ``POST /api/preview`` 同形（``message_refs``
        的第一条），``quality`` 仍在 ref 字典里——搜索行不必为这条链路再学一套字段。
        """
        refs = req.message_refs or []
        if not refs:
            raise HTTPException(status_code=422, detail="message_refs required")
        ref = refs[0]
        prepared = await browser.prepare(meta_from_dict(ref), ref.get("quality"))
        return {
            "token": prepared.token,
            "file_name": prepared.file_name,
            "size": prepared.size,
            "url": f"/api/search/browser-download/{prepared.token}",
        }

    @app.get("/api/search/browser-download/{token}")
    async def stream_browser_download(
        token: str, _: None = Depends(ctx.check_session)
    ) -> FileResponse:
        """附件流（FR-DL-08）：`filename` 决定浏览器保存的名字。

        token 在 TTL（默认 2 小时）内**可重复取**：浏览器对同一地址发 Range 分段取或
        续传时，命中同一份临时文件即可继续；过期或被并存上限挤掉后是 404，
        用户重新点按钮（准备走同一条链路）。
        """
        taken = browser.resolve(token)
        if taken is None:
            raise HTTPException(status_code=404, detail="browser download not found")
        path, file_name = taken
        return FileResponse(path, media_type="application/octet-stream", filename=file_name)
