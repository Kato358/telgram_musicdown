"""FastAPI 应用装配：路由、认证依赖、错误包络、SSE（SDD §4）。"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.db.models import Task
from app.db.store import Store
from app.domain import TemplateConfig, TrackMeta, card_to_dict, meta_from_dict
from app.errors import AppError, AuthError
from app.events import Event, EventBus
from app.services.download import DownloadRequest, DownloadService
from app.services.path_builder import render_path
from app.services.preview import PreviewService
from app.services.source import SearchService, SourceService
from app.services.sync import INITIAL_IMPORT_LIMIT
from app.utils.linkparse import parse_link
from app.web import auth as web_auth
from app.web.routes import schemas

logger = logging.getLogger(__name__)

# 前端产物缺失时的首屏说明（首次部署的唯一出路就是把产物建出来）
_FRONTEND_MISSING = """前端产物缺失，暂时打不开界面。

在源码根构建一次再启动：
  cd web && npm install && npm run build
或用 ./run.sh（缺产物时会自动构建）。

Docker：镜像构建前要先有 web/dist（Dockerfile 会 COPY web/dist）。
产物在别处时用 TGM_STATIC_DIR 指过去。
"""


def _envelope(code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=400, content={"error": {"code": code, "message": message}})


_PHONE_RE = re.compile(r"^\+?\d{6,15}$")


def _normalize_phone(raw: str) -> str:
    """手机号归一：去掉空格/连字符/括号（用户从通讯录复制时常带这些）。"""
    return re.sub(r"[\s\-()]", "", raw or "")


def _enqueue_sync(store: Store, source_id: int, **payload: Any) -> int:
    """建一条 sync 任务（FR-SRC-04）：由下载 Worker 池执行，Web 请求不阻塞。"""
    return store.create_task(
        Task(
            id=None,
            type="sync",
            payload_json=json.dumps({"source_id": source_id, **payload}, ensure_ascii=False),
        )
    )


def _disk_bytes(paths: list[str]) -> int:
    """已入库文件的磁盘占用；文件不在磁盘上（被移走、被删）就不算——占用是事实，不是账目。"""
    total = 0
    for raw in paths:
        try:
            total += Path(raw).stat().st_size
        except OSError:
            continue
    return total


def create_app(  # noqa: PLR0915  路由工厂注册全部端点，语句数天然超限
    store: Store,
    events: EventBus,
    downloads: DownloadService,
    sources: SourceService,
    search: SearchService,
    preview: PreviewService,
    tg: Any,
    *,
    base_dir: Path,
    web_host: str = "127.0.0.1",
    web_login_secret: str = "",
    static_dir: Path | None = None,
) -> FastAPI:
    app = FastAPI(title="telegram-musicdown")
    need_auth = web_auth.auth_required(web_host, web_login_secret)
    # 运行时间的起点：装配即进程起服务，用单调钟免掉系统时间跳变（`GET /api/stats`）。
    started_at = time.monotonic()

    def check_session(request: Request) -> None:
        if not need_auth:
            return
        token = request.cookies.get(web_auth.SESSION_COOKIE, "")
        if not web_auth.verify_session_token(token, web_login_secret):
            raise HTTPException(status_code=401, detail="unauthorized")

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        payload: dict[str, Any] = {"code": exc.code, "message": exc.message}
        if hasattr(exc, "reason"):
            payload["reason"] = exc.reason
        return JSONResponse(status_code=400, content={"error": payload})

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        """Pydantic 校验失败也走同一错误包络（NFR-08/§4），前端只需认一种错误形状。"""
        first = exc.errors()[0] if exc.errors() else {}
        field = ".".join(str(p) for p in first.get("loc", ()) if p != "body")
        message = f"{field}: {first.get('msg', 'invalid request')}".lstrip(": ")
        return JSONResponse(
            status_code=400, content={"error": {"code": "validation_error", "message": message}}
        )

    # ---- auth ----

    @app.post("/api/auth/telegram/send-code")
    async def send_code(
        req: schemas.SendCodeRequest, _: None = Depends(check_session)
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
        req: schemas.SignInRequest, _: None = Depends(check_session)
    ) -> dict[str, Any]:
        """登录（FR-AUTH-01）：会话文件落 sessions/；两步验证缺密码报 password_required。"""
        if tg.user is None:
            raise AuthError("not_authorized", "先发送验证码，再提交验证码")
        await tg.user.sign_in(req.phone, req.code, req.code_hash, req.password)
        tg.mark_authorized()
        await tg.start_bot_configured()  # bot_token 已配置时按新会话启动 Bot（FR-AUTH-04）
        info: dict[str, Any] = await tg.user.get_me()
        return info

    @app.post("/api/auth/logout")
    async def logout(_: None = Depends(check_session)) -> schemas.LogoutResponse:
        """退出登录（FR-AUTH-02）：断开 User Client 并删除会话文件，下载文件与数据库不动。

        删会话文件才算真退出——留着它下次启动会判「已有有效会话」直接放行。
        这里是退出 Telegram 账号，不是退出 Web 控制台，故不动会话 cookie。
        """
        removed = await tg.logout()
        return schemas.LogoutResponse(removed_sessions=removed)

    @app.get("/api/me")
    async def me(_: None = Depends(check_session)) -> schemas.MeResponse:
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

    # ---- setup（首次部署初始化，FR-OPS-02）----

    @app.get("/api/setup/status")
    async def setup_status(_: None = Depends(check_session)) -> schemas.SetupStatusResponse:
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
        req: schemas.SetupSecretsRequest, _: None = Depends(check_session)
    ) -> dict[str, Any]:
        """保存密钥到 config.yaml（不入库，NFR-02）。

        校验看合并后的内容（前端校验只是即时反馈）；已连上的客户端不热换密钥，
        故返回 ``restart_required`` 让界面说明重启后生效。
        """
        from app.services.setup import save_secrets, setup_complete  # noqa: PLC0415

        updated = save_secrets(base_dir, req.model_dump(exclude_unset=True))
        tg.secrets = updated
        alive = tg.user is not None or tg.bot is not None
        return {
            "ok": True,
            "complete": setup_complete(updated, tg.authorized),
            "restart_required": alive,
        }

    @app.post("/api/setup/reset")
    async def setup_reset(_: None = Depends(check_session)) -> schemas.ResetResponse:
        """重新执行初始化（FR-OPS-02）：清密钥段 + 退出登录 + 删会话，回到向导第 1 步。

        设置页的入口在这里；做完 ``complete`` 变 false，前端闸门随即把人送回向导。
        Bot 会话一并删：重新初始化后 bot_token 多半要换，旧会话文件会让新 token 复用旧身份。
        下载文件、历史与音乐源不动（FR-AUTH-02）。
        """
        from app.services.setup import RESET_KEYS, clear_secrets  # noqa: PLC0415

        removed = await tg.logout(purge_bot_session=True)
        tg.secrets = clear_secrets(base_dir)
        return schemas.ResetResponse(cleared_keys=list(RESET_KEYS), removed_sessions=removed)

    # ---- stats ----

    @app.get("/api/stats")
    async def stats_ep(_: None = Depends(check_session)) -> schemas.StatsResponse:
        """控制台统计：统计卡与系统状态的唯一数据源（uptime 自应用装配起算）。

        曲库占用去磁盘上量：DB 里的 `file_size` 在链接/转发入队时经常是空的，
        拿它求和会让「占用」永远显示 0。逐个 stat 放线程里做，别堵事件循环。
        """
        counts = store.counts()
        return schemas.StatsResponse(
            tasks=schemas.TaskCounts(
                queued=counts["tasks_queued"],
                downloading=counts["tasks_downloading"],
                paused=counts["tasks_paused"],
                failed=counts["tasks_failed"],
                success=counts["tasks_success"],
            ),
            library=schemas.LibraryCounts(
                tracks=counts["library_tracks"],
                bytes=await asyncio.to_thread(_disk_bytes, store.library_paths()),
                failed=counts["library_failed"],
            ),
            sources=schemas.SourceCounts(
                total=counts["sources_total"], enabled=counts["sources_enabled"]
            ),
            uptime_sec=round(time.monotonic() - started_at, 1),
        )

    # ---- sources ----

    @app.get("/api/sources")
    async def list_sources(_: None = Depends(check_session)) -> list[dict[str, Any]]:
        return [_src_dict(s) for s in sources.list_sources()]

    @app.post("/api/sources")
    async def add_source(
        req: schemas.SourceUpsertRequest, _: None = Depends(check_session)
    ) -> dict[str, Any]:
        """添加源（FR-SRC-01）+ 一次初始导入（最近 200 条，向导第 3 步的承诺）。"""
        src = await sources.add_source(parse_link(req.link))
        task_id = _enqueue_sync(
            store,
            int(src.id or 0),
            direction="backward",
            limit=INITIAL_IMPORT_LIMIT,
            initial=True,
        )
        await events.publish(
            Event("task.status", {"task_id": task_id, "status": "queued", "error": None})
        )
        return {**_src_dict(src), "import_task_id": task_id}

    @app.put("/api/sources/{source_id}")
    async def update_source(
        source_id: int, req: schemas.SourceUpdateRequest, _: None = Depends(check_session)
    ) -> dict[str, Any]:
        existing = store.get_source(source_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="source not found")
        if req.enabled is not None:
            existing.enabled = int(req.enabled)
        if req.auto_sync is not None:
            existing.auto_sync = int(req.auto_sync)
        if req.sync_interval_sec is not None:
            existing.sync_interval_sec = req.sync_interval_sec
        if req.media_scope is not None:
            existing.media_scope = json.dumps(req.media_scope, ensure_ascii=False)
        if req.filters is not None:
            existing.filters_json = json.dumps(req.filters, ensure_ascii=False)
        existing.save_path_override = req.save_path_override
        existing.dir_template_override = req.dir_template_override
        existing.file_template_override = req.file_template_override
        existing.note = req.note
        return _src_dict(sources.update_source(existing))

    @app.delete("/api/sources/{source_id}")
    async def delete_source(
        source_id: int, with_history: bool = False, _: None = Depends(check_session)
    ) -> dict[str, bool]:
        sources.delete_source(source_id, with_history)
        return {"ok": True}

    @app.post("/api/sources/{source_id}/backfill")
    async def backfill(
        source_id: int, req: schemas.BackfillRequest, _: None = Depends(check_session)
    ) -> dict[str, int]:
        """历史回溯（FR-SRC-04）：交给 sync 任务；方向与锚点原样入 payload。"""
        if store.get_source(source_id) is None:
            raise HTTPException(status_code=404, detail="source not found")
        task_id = _enqueue_sync(
            store,
            source_id,
            direction=req.direction,
            limit=req.limit,
            to_message_id=req.to_message_id,
            to_date=req.to_date,
        )
        await events.publish(
            Event("task.status", {"task_id": task_id, "status": "queued", "error": None})
        )
        return {"task_id": task_id}

    @app.get("/api/sources/discover")
    async def discover(_: None = Depends(check_session)) -> dict[str, Any]:
        """候选源（FR-SRC-05）：账号对话中标题/用户名含音乐关键词、尚未添加的。"""
        if not tg.authorized:
            raise AuthError("not_connected", "先完成 Telegram 登录，才能扫你的会话")
        items = await sources.discover_candidates()
        return {"items": [_candidate_dict(c) for c in items]}

    # ---- search ----

    @app.post("/api/search")
    async def search_ep(
        req: schemas.SearchRequest, _: None = Depends(check_session)
    ) -> dict[str, Any]:
        resp = await search.search(req.q, req.source_ids, page=req.page)
        return {
            "results": [card_to_dict(c) for c in resp.results],
            "meta": resp.meta,
        }

    # ---- downloads ----

    @app.post("/api/downloads")
    async def create_downloads(
        req: schemas.DownloadsRequest, _: None = Depends(check_session)
    ) -> dict[str, Any]:
        """入队（FR-LINK-01/02、FR-DL-01）：urls 逐条解析，message_refs 直接建任务。

        message_refs 里带的元数据（title/artist/ext…）原样进 TrackMeta，
        搜索结果直接下载时模板才有真实字段可用。
        """
        out: list[dict[str, Any]] = []
        refs: list[dict[str, Any]] = []
        if req.message_refs:
            refs.extend(req.message_refs)
        if req.urls:
            for url in req.urls:
                try:
                    link = parse_link(url)
                except AppError as e:
                    out.append({"url": url, "error": e.message})
                    continue
                if link.chat_id is None or link.message_id is None:
                    out.append({"url": url, "error": "link needs chat and message id"})
                    continue
                refs.append({"chat_id": link.chat_id, "message_id": link.message_id})
        for ref in refs:
            meta = meta_from_dict(ref)
            task_id = await downloads.enqueue(DownloadRequest(meta=meta, force=req.force))
            out.append({"chat_id": meta.chat_id, "message_id": meta.message_id, "task_id": task_id})
        return {"items": out}

    @app.get("/api/downloads")
    async def list_downloads(
        status: str | None = None, page: int = 0, _: None = Depends(check_session)
    ) -> list[dict[str, Any]]:
        tasks = store.list_tasks(status=status, limit=50)
        out: list[dict[str, Any]] = []
        for task in tasks:
            history = store.get_history(task.history_id) if task.history_id else None
            out.append(_task_dict(task, history))
        return out

    @app.post("/api/downloads/{task_id}/cancel")
    async def cancel_download(task_id: int, _: None = Depends(check_session)) -> dict[str, bool]:
        await downloads.cancel_task(task_id)
        return {"ok": True}

    @app.post("/api/downloads/{task_id}/pause")
    async def pause_download(task_id: int, _: None = Depends(check_session)) -> dict[str, bool]:
        await downloads.pause_task(task_id)
        return {"ok": True}

    @app.post("/api/downloads/{task_id}/resume")
    async def resume_download(task_id: int, _: None = Depends(check_session)) -> dict[str, bool]:
        await downloads.resume_task(task_id)
        return {"ok": True}

    @app.post("/api/downloads/{task_id}/retry")
    async def retry_download(task_id: int, _: None = Depends(check_session)) -> dict[str, bool]:
        if store.get_task(task_id) is None:
            raise HTTPException(status_code=404, detail="task not found")
        await downloads.retry_task(task_id)
        return {"ok": True}

    @app.delete("/api/downloads/{task_id}")
    async def delete_download(task_id: int, _: None = Depends(check_session)) -> dict[str, bool]:
        if store.get_task(task_id) is None:
            raise HTTPException(status_code=404, detail="task not found")
        await downloads.delete_task(task_id)
        return {"ok": True}

    @app.post("/api/downloads/retry-failed")
    async def retry_failed(_: None = Depends(check_session)) -> dict[str, int]:
        return {"retried": await downloads.retry_failed()}

    # ---- history ----

    @app.get("/api/history")
    async def history_ep(
        status: str | None = None,
        source_id: int | None = None,
        q: str | None = None,
        page: int = 0,
        _: None = Depends(check_session),
    ) -> list[dict[str, Any]]:
        rows = store.list_history(
            status=status, source_id=source_id, q=q, limit=50, offset=page * 50
        )
        return [_history_dict(h) for h in rows]

    @app.get("/api/history/{history_id}/stream")
    async def history_stream(
        history_id: int, _: None = Depends(check_session)
    ) -> StreamingResponse:
        h = store.get_history(history_id)
        if h is None or not h.save_path:
            raise HTTPException(status_code=404, detail="history not found")
        p = Path(h.save_path)
        if not p.exists():  # noqa: ASYNC240  路由级存在性检查非热路径
            raise HTTPException(status_code=404, detail="file missing")
        # 流式句柄惰性打开，非阻塞下载（noqa 覆盖整语句）
        return StreamingResponse(
            p.open("rb"),  # noqa: ASYNC230, ASYNC240
            media_type="application/octet-stream",
        )

    # ---- preview ----

    @app.post("/api/preview")
    async def preview_ep(
        req: schemas.DownloadsRequest, _: None = Depends(check_session)
    ) -> dict[str, int]:
        refs = req.message_refs or []
        if not refs:
            raise HTTPException(status_code=422, detail="message_refs required")
        r = refs[0]
        pid = await preview.request_preview(
            int(r["chat_id"]), int(r["message_id"]), r.get("file_size")
        )
        return {"preview_id": pid}

    @app.get("/api/preview/{preview_id}/stream")
    async def preview_stream(
        preview_id: int, _: None = Depends(check_session)
    ) -> StreamingResponse:
        p = preview.stream_path(preview_id)
        # noqa 覆盖整语句：流式句柄非阻塞读
        return StreamingResponse(
            p.open("rb"),
            media_type="application/octet-stream",  # noqa: ASYNC230, ASYNC240
        )

    # ---- settings ----

    @app.get("/api/settings")
    async def get_settings(_: None = Depends(check_session)) -> dict[str, str]:
        return store.all_settings()

    @app.put("/api/settings")
    async def put_settings(
        req: schemas.SettingsUpdateRequest, _: None = Depends(check_session)
    ) -> dict[str, str]:
        for k, v in req.values.items():
            store.set_setting(k, json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v)
        return store.all_settings()

    @app.post("/api/settings/preview-path")
    async def preview_path(
        req: schemas.PreviewPathRequest, _: None = Depends(check_session)
    ) -> dict[str, str]:
        sample = req.sample or {}
        meta = TrackMeta(
            chat_id=int(sample.get("chat_id", -100)),
            message_id=int(sample.get("message_id", 1)),
            title=sample.get("title", "Sample Title"),
            artist=sample.get("artist", "Sample Artist"),
            album=sample.get("album"),
            track=sample.get("track"),
            ext=sample.get("ext", "mp3"),
        )
        cfg = TemplateConfig(dir_template=req.dir_template, file_template=req.file_template)
        return {"path": str(render_path(meta, cfg))}

    # ---- events (SSE) ----

    @app.get("/api/events")
    async def events_sse(_: None = Depends(check_session)) -> StreamingResponse:
        async def gen() -> AsyncIterator[str]:
            q = await events.subscribe()
            try:
                while True:
                    ev = await q.get()
                    yield ev.to_sse()
            finally:
                await events.unsubscribe(q)

        return StreamingResponse(gen(), media_type="text/event-stream")

    # ---- static ----

    if static_dir and static_dir.exists():
        # /assets 等静态文件；其余非 /api 路径全部回退 index.html（SPA 路由，SDD §4.3）
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

        @app.exception_handler(404)
        async def spa_fallback(request: Request, exc: Any) -> Any:
            if request.url.path.startswith("/api"):
                return JSONResponse(status_code=404, content={"detail": "Not Found"})
            return FileResponse(static_dir / "index.html")
    else:
        # 前端产物缺失：首次部署最常见的原因（本地还没构建、Docker 镜像里没 COPY 进去）。
        # 这里给可操作说明而不是 404 裸 JSON——否则首屏只有一行 {"detail":"Not Found"}，
        # 用户没有任何入口去进初始化向导。
        logger.error("web frontend missing at %s: run a build (npm run build) first", static_dir)

        @app.exception_handler(404)
        async def spa_missing(request: Request, exc: Any) -> Any:
            if request.url.path.startswith("/api"):
                return JSONResponse(status_code=404, content={"detail": "Not Found"})
            return PlainTextResponse(_FRONTEND_MISSING, status_code=503)

    return app


def _src_dict(s: Any) -> dict[str, Any]:
    return {
        "id": s.id,
        "telegram_chat_id": s.telegram_chat_id,
        "username": s.username,
        "title": s.title,
        "type": s.type,
        "enabled": bool(s.enabled),
        "auto_sync": bool(s.auto_sync),
        "sync_interval_sec": s.sync_interval_sec,
        "last_message_id": s.last_message_id,
        "media_scope": json.loads(s.media_scope),
        "note": s.note,
    }


def _candidate_dict(c: Any) -> dict[str, Any]:
    """候选源（FR-SRC-05）：username 不带 @，由前端统一展示（与 SourceRow 一致）。"""
    return {
        "chat_id": c.chat_id,
        "title": c.title,
        "username": c.username,
        "type": c.type,
        "members": c.members,
        "tags": c.tags,
    }


def _task_dict(t: Any, history: Any | None = None) -> dict[str, Any]:
    title = history.title if history is not None else None
    artist = history.artist if history is not None else None
    if not title or not artist:
        try:
            payload = json.loads(t.payload_json)
        except (TypeError, json.JSONDecodeError):
            payload = {}
        meta = payload.get("meta") if isinstance(payload, dict) else None
        if isinstance(meta, dict):
            title = title or meta.get("title")
            artist = artist or meta.get("artist")
    return {
        "id": t.id,
        "type": t.type,
        "status": t.status,
        "title": title,
        "artist": artist,
        "progress_bytes": t.progress_bytes,
        "total_bytes": t.total_bytes,
        "speed": t.speed,
        "retry_count": t.retry_count,
        "error": t.error,
    }


def _history_dict(h: Any) -> dict[str, Any]:
    return {
        "id": h.id,
        "source_id": h.source_id,
        "chat_id": h.chat_id,
        "message_id": h.message_id,
        "title": h.title,
        "artist": h.artist,
        "album": h.album,
        "duration_sec": h.duration_sec,
        "file_size": h.file_size,
        "save_path": h.save_path,
        "status": h.status,
        "error": h.error,
        "created_at": h.created_at,
    }
