"""浏览器下载路由（FR-DL-08）：进度流 + 可用性探测 + 附件流。

三步是为了一件事：**进度要看得见、失败要说得清**。

1. ``POST`` 边走边推：服务端把这首取回临时区，同时按 ``text/event-stream`` 推字节进度
   （若干条 ``preparing`` → 一条 ``ready`` 或 ``failed``）。取数可能长达几分钟（母带 / 慢源），
   没有这一步，用户看到的就是「点了没反应」。
2. ``HEAD`` 只回答「这一份还能取吗」：``window.location.assign`` 之后响应体读不到了，
   token 过期时 GET 回的是 404 JSON，浏览器会把**整页**导航过去——用户丢掉整个界面、
   只剩一个错误页。前端在导航前先探一次，404 就地改说行内原因，不导航。
3. ``GET`` 只负责把一个**已经在本地**的文件按附件发出去。

第 3 步不删文件、也不作废 token：``FileResponse`` 自带 Range（206），浏览器与下载管理器
会分段取、暂停后续传——只允许取一次的话，它们拿到的是片段、第二次直接 404，
用户看到的是「下载到一半坏了」。生命周期交给服务层的 TTL 与并存上限。

认证与其它接口一致（cookie 会话）：浏览器顶层导航会带上 ``tgm_session``，
故第 3 步不需要前端拿 blob 再造 URL——那会让大文件在内存里过一遍。

**失败为什么不是 400**：``POST`` 的响应头一发出去，状态码就定死了，而失败要到**取数途中**
才可能知道（上游报错 / FloodWait / 平台下架）。故失败走流内终态，形状仍是标准错误包络的
``{"error": {"code", "message"}}``（SDD §4.1）——前端读的还是同一套字段。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, Response, StreamingResponse

from app.domain import TrackMeta, meta_from_dict
from app.errors import AppError
from app.services.browser_download import BrowserDownloadService
from app.web.routes import schemas
from app.web.routes.context import RouteContext

logger = logging.getLogger(__name__)

#: 进度事件的推送间隔（秒）：够「进度条在动」的手感，又不至于把一条流推成刷屏。
#: 取数结束（成 / 败）时立刻推终态，不等这一拍。
PROGRESS_TICK_SEC = 0.4

#: SSE 响应头。``X-Accel-Buffering`` 是给「用户自己在前头挂了反代」的场合兜底：
#: nginx 默认会把 SSE 攒起来再发，那样进度条会一直不动直到取完。
#: 应用自身的 GZipMiddleware 本来就把 ``text/event-stream`` 排除在压缩之外（见 routes/__init__）。
SSE_HEADERS = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}


def _event(payload: dict[str, Any]) -> str:
    """一条 SSE 事件：``data: {json}\\n\\n``（不写 ``event:``——前端只切 data 行，少一层分岔）。"""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _prepare_stream(
    browser: BrowserDownloadService, meta: TrackMeta, quality: str | None
) -> AsyncIterator[str]:
    """取回这一首并把进度推出去（FR-DL-08）。

    取数在**独立 task** 里跑，本生成器只负责按拍子读它兜出来的进度——这样「客户端断开」
    与「取数继续」是两件独立的事，能在断开时精确地只停该停的那个。
    """
    #: 取数层回调落在这里（Telethon 在事件循环里同步回调：只写两个标量，不做 await）
    latest: dict[str, int | None] = {"loaded": 0, "total": None}

    def on_progress(loaded: int, total: int | None) -> None:
        latest["loaded"] = loaded
        latest["total"] = total

    task = asyncio.create_task(browser.prepare(meta, quality, on_progress))
    try:
        while True:
            try:
                # shield：这一拍超时只是「还没好」，不能把取数 task 一起取消掉
                prepared = await asyncio.wait_for(asyncio.shield(task), timeout=PROGRESS_TICK_SEC)
                break
            except TimeoutError:
                yield _event({"state": "preparing", **latest})
        yield _event(
            {
                "state": "ready",
                "token": prepared.token,
                "file_name": prepared.file_name,
                "size": prepared.size,
                "url": f"/api/search/browser-download/{prepared.token}",
            }
        )
    except AppError as e:
        yield _event({"state": "failed", "error": {"code": e.code, "message": e.message}})
    except asyncio.CancelledError:
        # 客户端断开（用户点了「取消」/ 关了页签 / 导航走了）：把取数一并停掉，
        # 别让上游还在替我们拉文件。半截分片由服务层的 prepare 自己收拾
        # （它在 CancelledError 分支里 unlink）。
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
        raise
    except Exception:  # noqa: BLE001  兜底：流已经开了，改不了状态码，只能给同形状的终态
        logger.exception("browser download stream failed")
        yield _event(
            {
                "state": "failed",
                "error": {"code": "internal_error", "message": "服务端异常，请重新点一次"},
            }
        )
    finally:
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


def register(app: FastAPI, ctx: RouteContext) -> None:
    """注册浏览器下载路由。"""
    browser = ctx.browser_downloads

    @app.post("/api/search/browser-download")
    async def prepare_browser_download(
        req: schemas.DownloadsRequest, _: None = Depends(ctx.check_session)
    ) -> StreamingResponse:
        """把一首取回服务端临时区，边取边推字节进度（FR-DL-08）。

        入参与 ``POST /api/downloads`` / ``POST /api/preview`` 同形（``message_refs``
        的第一条），``quality`` 仍在 ref 字典里——搜索行不必为这条链路再学一套字段。
        """
        refs = req.message_refs or []
        if not refs:
            raise HTTPException(status_code=422, detail="message_refs required")
        ref = refs[0]
        return StreamingResponse(
            _prepare_stream(browser, meta_from_dict(ref), ref.get("quality")),
            media_type="text/event-stream",
            headers=SSE_HEADERS,
        )

    @app.head("/api/search/browser-download/{token}")
    async def probe_browser_download(token: str, _: None = Depends(ctx.check_session)) -> Response:
        """可用性探测（FR-DL-08）：只回答「这一份还在不在」，不读文件体。

        前端在顶层导航之前问一句：404 就地改说行内原因，用户不会因为一份过期的临时文件
        被整页带到浏览器的错误页上。
        """
        if browser.resolve(token) is None:
            raise HTTPException(status_code=404, detail="browser download not found")
        return Response(status_code=204)

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


__all__ = ["PROGRESS_TICK_SEC", "SSE_HEADERS", "register"]
