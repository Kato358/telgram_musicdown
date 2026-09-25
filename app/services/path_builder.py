"""模板渲染（FR-NAME-01~07，SDD §2.2）。

自实现小型渲染器：支持 ``{field}``、``{field:02d}``（整数补零）、
``{field:truncate:N}``、``{field:%Y-%m}``（日期格式）。

回退链严格按 SRS FR-NAME-02 表：
- ``title``：audio.title → file_name 去扩展名 → caption 首行 → message_{id}（必有值）。
- ``artist``：audio.performer → caption 正则（FR-NAME-05）→ Unknown Artist。
- ``album``/``track``：无值时整段省略（FR-NAME-03）。

渲染顺序：字段取值 → 过滤器 → 非法字符替换 → 段拆分 → 空段剔除 →
保留名检查 → 路径长度截断（≤230 字节）→ save_path 拼接。
path_builder 是纯函数，无 Telegram 依赖（NFR-07）。
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path, PurePath

from app.domain import TemplateConfig, TrackMeta
from app.utils.sanitize import MAX_PATH_BYTES, sanitize_segment, truncate_to_bytes

UNKNOWN_ARTIST = "Unknown Artist"

_FIELD_RE = re.compile(r"\{(\w+)(?::([^{}]*))?\}")
# FR-NAME-05 默认 caption 正则；仅官方字段为空时启用
_CAPTION_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _title_fallback(meta: TrackMeta, cfg: TemplateConfig) -> str:
    """title 回退链：audio.title → file_name → caption 正则 → caption 首行 → message_{id}。"""
    if meta.title:
        return meta.title
    if meta.file_name and (stem := Path(meta.file_name).stem):
        return stem
    if meta.caption:
        m = re.match(cfg.caption_artist_re, meta.caption)
        if m and m.group("title"):
            return m.group("title").strip()
        first = meta.caption.strip().splitlines()[0].strip()
        if first:
            return first
    return f"message_{meta.message_id}"


def _artist_fallback(meta: TrackMeta, cfg: TemplateConfig) -> str:
    """artist 回退链：audio.performer → caption 正则 → Unknown Artist。"""
    if meta.artist:
        return meta.artist
    if meta.caption:
        m = re.match(cfg.caption_artist_re, meta.caption)
        if m and m.group("artist"):
            return m.group("artist").strip()
    return UNKNOWN_ARTIST


def _resolve_field(meta: TrackMeta, field: str, cfg: TemplateConfig) -> str | None:
    """字段取值（FR-NAME-02 回退链表）。None 表示无值。"""
    simple: dict[str, str | None] = {
        "album": meta.album,
        "channel": meta.channel_title,
        "channel_id": str(meta.chat_id),
        "message_id": str(meta.message_id),
        "caption": meta.caption,
        "ext": meta.ext,
        "bitrate": str(meta.bitrate) if meta.bitrate is not None else None,
        "size": str(meta.file_size) if meta.file_size is not None else None,
        "unique_id": (meta.unique_id or "")[:8] or None,
        "track": str(meta.track) if meta.track is not None else None,
        "duration": str(meta.duration_sec) if meta.duration_sec is not None else None,
        "year": meta.message_date[:4] if meta.message_date else None,
        "date": meta.message_date[:10] if meta.message_date else None,
    }
    if field in simple:
        return simple[field]
    if field == "title":
        return _title_fallback(meta, cfg)
    if field == "artist":
        return _artist_fallback(meta, cfg)
    if field == "file_name":
        return Path(meta.file_name).name if meta.file_name else None
    return None


def resolve_field(meta: TrackMeta, field: str, cfg: TemplateConfig) -> str | None:
    """单字段取值（含回退链）的公开入口：设置页模板字段文档与预览共用。"""
    return _resolve_field(meta, field, cfg)


def _truncate_filter(value: str, spec: str) -> str:
    try:
        n = int(spec.split(":", 1)[1])
    except ValueError:
        return value
    return value[:n]


def _date_filter(value: str, spec: str, meta: TrackMeta, cfg: TemplateConfig) -> str:
    if (field_date := _resolve_field(meta, "date", cfg)) is None:
        return value
    try:
        return datetime.fromisoformat(field_date).strftime(spec)
    except ValueError:
        return value


def _pad_filter(value: str, spec: str) -> str:
    """``:02d`` 补零。"""
    pad = spec[:-1] if spec.endswith("d") else spec
    if pad.isdigit() and value.isdigit():
        return value.zfill(len(pad))
    return value


def _apply_filter(
    value: str | None, spec: str | None, meta: TrackMeta, cfg: TemplateConfig
) -> str | None:
    """过滤器：``:02d`` 补零、``:truncate:N`` 截断、``:%Y-%m`` 日期。"""
    if value is None:
        return None
    if not spec:
        return value
    if spec.startswith("truncate:"):
        return _truncate_filter(value, spec)
    if spec.startswith("%"):
        return _date_filter(value, spec, meta, cfg)
    if spec.endswith("d") or spec.isdigit():
        return _pad_filter(value, spec)
    return value


def render_template(template: str, meta: TrackMeta, cfg: TemplateConfig) -> str:
    """渲染模板字符串；无值字段整段省略（FR-NAME-03）。"""

    def sub(m: re.Match[str]) -> str:
        field, spec = m.group(1), m.group(2)
        value = _resolve_field(meta, field, cfg)
        value = _apply_filter(value, spec, meta, cfg)
        return value if value is not None else ""

    return _FIELD_RE.sub(sub, template)


def _split_segments(rendered: str) -> list[str]:
    """按段拆分 + 空段剔除。"""
    parts = re.split(r"[/\\]+", rendered)
    return [p for p in (seg.strip() for seg in parts) if p]


def render_path(meta: TrackMeta, cfg: TemplateConfig) -> PurePath:
    """渲染完整落盘路径：save_path + 目录段 + 文件名（FR-NAME-01）。

    纯函数；冲突策略（FR-NAME-03 追加 `` (n)``）由 download 服务在落盘时处理。
    """
    dir_rendered = render_template(cfg.dir_template, meta, cfg)
    file_rendered = render_template(cfg.file_template, meta, cfg)
    # 文件名最终为空回退 message_{id}（FR-NAME-03）
    file_rendered = file_rendered.strip()
    ext = f".{meta.ext}" if meta.ext else ""
    segments = _split_segments(dir_rendered) + [file_rendered + ext]
    # 逐段 sanitize：非法字符替换、保留名检查
    sanitized = [sanitize_segment(seg) for seg in segments]
    joined = "/".join(sanitized)
    truncated = truncate_to_bytes(joined, MAX_PATH_BYTES)
    return cfg.save_path / Path(*truncated.split("/"))


def resolve_conflict(target: Path, expected_size: int | None = None) -> tuple[Path, bool]:
    """冲突策略（FR-NAME-03）：目标存在且大小一致 → 去重命中；
    否则追加 `` (n)``，n 从 1 递增。

    返回 (最终路径, is_dedupe_hit)。
    """
    if not target.exists():
        return target, False
    if expected_size is not None and target.stat().st_size == expected_size:
        return target, True
    stem, dot, ext = target.name.rpartition(".")
    base = stem if dot else target.name
    suffix = dot + ext if dot else ""
    n = 1
    while True:
        candidate = target.with_name(f"{base} ({n}){suffix}")
        if not candidate.exists():
            return candidate, False
        if expected_size is not None and candidate.stat().st_size == expected_size:
            return candidate, True
        n += 1
