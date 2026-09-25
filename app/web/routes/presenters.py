"""展示层序列化（SRP）：行/卡片 → API dict 的纯函数，无副作用、可单测。"""

from __future__ import annotations

import json
from typing import Any


def src_dict(s: Any) -> dict[str, Any]:
    """音乐源行 → API dict（FR-SRC-01）。"""
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


def candidate_dict(c: Any) -> dict[str, Any]:
    """候选源（FR-SRC-05）：username 不带 @，由前端统一展示（与 SourceRow 一致）。"""
    return {
        "chat_id": c.chat_id,
        "title": c.title,
        "username": c.username,
        "type": c.type,
        "members": c.members,
        "tags": c.tags,
    }


def task_dict(t: Any, history: Any | None = None) -> dict[str, Any]:
    """任务台账行 → API dict；缺元数据时回退 payload_json 里的 meta（FR-META-01）。"""
    title = history.title if history is not None else None
    artist = history.artist if history is not None else None
    album = history.album if history is not None else None
    duration_sec = history.duration_sec if history is not None else None
    if not title or not artist or not album:
        try:
            payload = json.loads(t.payload_json)
        except (TypeError, json.JSONDecodeError):
            payload = {}
        meta = payload.get("meta") if isinstance(payload, dict) else None
        if isinstance(meta, dict):
            title = title or meta.get("title")
            artist = artist or meta.get("artist")
            album = album or meta.get("album")
            duration_sec = duration_sec or meta.get("duration_sec")
    return {
        "id": t.id,
        "type": t.type,
        "status": t.status,
        "title": title,
        "artist": artist,
        "album": album,
        "duration_sec": duration_sec,
        "progress_bytes": t.progress_bytes,
        "total_bytes": t.total_bytes,
        "speed": t.speed,
        "retry_count": t.retry_count,
        "error": t.error,
    }


def history_dict(h: Any, task_id: int | None = None) -> dict[str, Any]:
    """历史行 → API dict（含当前挂着的任务 id）。"""
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
        # 这条记录当前挂着的任务（台账被删过则为 null）：实时读数与暂停/继续/取消都用它
        "task_id": task_id,
    }
