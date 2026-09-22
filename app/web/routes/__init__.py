"""FastAPI 应用装配：路由、认证依赖、错误包络、SSE（SDD §4）。"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.db.models import Task
from app.db.store import Store
from app.domain import TemplateConfig, TrackMeta
from app.errors import AppError
from app.events import Event, EventBus
from app.services.download import DownloadRequest, DownloadService
from app.services.path_builder import render_path
from app.services.preview import PreviewService
from app.services.source import SearchService, SourceService, card_to_dict
from app.utils.linkparse import parse_link
from app.web import auth as web_auth
from app.web.routes import schemas

logger = logging.getLogger(__name__)


def _envelope(code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=400, content={"error": {"code": code, "message": message}})


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

    # ---- auth ----

    @app.post("/api/auth/telegram/send-code")
    async def send_code(
        req: schemas.SendCodeRequest, _: None = Depends(check_session)
    ) -> dict[str, str]:
        """发验证码（FR-AUTH-01）：UserClient 连接 + send_code。"""
        me = await tg.connect_user()  # 确保客户端存在并已连接
        del me
        if tg.user is None:
            raise HTTPException(status_code=503, detail="client unavailable")
        code_hash = await tg.user.send_code(req.phone)
        return {"code_hash": code_hash}

    @app.post("/api/auth/telegram/sign-in")
    async def sign_in(
        req: schemas.SignInRequest, _: None = Depends(check_session)
    ) -> dict[str, Any]:
        """登录（FR-AUTH-01）：生成 sessions/ 会话文件；2FA 需 password。"""
        if tg.user is None:
            raise HTTPException(status_code=409, detail="send code first")
        await tg.user.sign_in(req.phone, req.code, req.code_hash, req.password)
        return await tg.user.get_me()

    @app.post("/api/auth/logout")
    async def logout(response: Response, _: None = Depends(check_session)) -> dict[str, bool]:
        response.delete_cookie(web_auth.SESSION_COOKIE)
        await tg.disconnect_user()
        return {"ok": True}

    @app.get("/api/me")
    async def me(_: None = Depends(check_session)) -> schemas.MeResponse:
        """账号信息 + 连接状态（FR-AUTH-01/02）。"""
        if tg.user is None:
            return schemas.MeResponse(connected=False)
        try:
            info = await tg.user.get_me()
            return schemas.MeResponse(
                display_name=info.get("display_name"),
                username=info.get("username"),
                premium=bool(info.get("premium", False)),
                connected=True,
            )
        except AppError:
            return schemas.MeResponse(connected=False)

    # ---- setup（首次部署初始化，FR-OPS-02）----

    @app.get("/api/setup/status")
    async def setup_status(_: None = Depends(check_session)) -> dict[str, Any]:
        from app.services.setup import setup_complete  # noqa: PLC0415

        return {
            "complete": setup_complete(tg.secrets),
            "has_api_id": tg.secrets.api_id != 0,
            "has_api_hash": bool(tg.secrets.api_hash),
            "has_bot_token": bool(tg.secrets.bot_token),
            "proxy": tg.secrets.proxy is not None,
            "connected": tg.user is not None,
        }

    @app.post("/api/setup/secrets")
    async def setup_secrets(req: schemas.SetupSecretsRequest) -> dict[str, Any]:
        """保存密钥到 config.yaml（不入库，NFR-02）；凭据类字段保存后需重启生效。"""
        from app.services.setup import save_secrets, setup_complete  # noqa: PLC0415

        updated = save_secrets(
            base_dir,
            req.model_dump(exclude_none=True, exclude_unset=True),
        )
        tg.secrets = updated
        return {"ok": True, "complete": setup_complete(updated), "restart_required": True}


    # ---- sources ----

    @app.get("/api/sources")
    async def list_sources(_: None = Depends(check_session)) -> list[dict[str, Any]]:
        return [_src_dict(s) for s in sources.list_sources()]

    @app.post("/api/sources")
    async def add_source(
        req: schemas.SourceUpsertRequest, _: None = Depends(check_session)
    ) -> dict[str, Any]:
        src = await sources.add_source(parse_link(req.link))
        return _src_dict(src)

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
        task_id = store.create_task(
            Task(
                id=None,
                type="sync",
                payload_json=json.dumps(
                    {"source_id": source_id, **req.model_dump()}, ensure_ascii=False
                ),
            )
        )
        await events.publish(
            Event("task.status", {"task_id": task_id, "status": "queued", "error": None})
        )
        return {"task_id": task_id}

    @app.get("/api/sources/discover")
    async def discover(_: None = Depends(check_session)) -> list[dict[str, Any]]:
        return []

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
        out: list[dict[str, Any]] = []
        refs: list[tuple[int, int]] = []
        if req.message_refs:
            for r in req.message_refs:
                refs.append((int(r["chat_id"]), int(r["message_id"])))
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
                refs.append((link.chat_id, link.message_id))
        for chat_id, message_id in refs:
            task_id = await downloads.enqueue(
                DownloadRequest(chat_id=chat_id, message_id=message_id, force=req.force)
            )
            out.append({"chat_id": chat_id, "message_id": message_id, "task_id": task_id})
        return {"items": out}

    @app.get("/api/downloads")
    async def list_downloads(
        status: str | None = None, page: int = 0, _: None = Depends(check_session)
    ) -> list[dict[str, Any]]:
        tasks = store.list_tasks(status=status, limit=50)
        return [_task_dict(t) for t in tasks]

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


def _task_dict(t: Any) -> dict[str, Any]:
    return {
        "id": t.id,
        "type": t.type,
        "status": t.status,
        "progress_bytes": t.progress_bytes,
        "total_bytes": t.total_bytes,
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
