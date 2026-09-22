"""核心域类型：TrackMeta、TemplateConfig。

path_builder 是纯函数层，本模块为其提供唯一数据契约（SDD §2.2）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePath


@dataclass(slots=True)
class TrackMeta:
    """待渲染音轨的元数据快照，字段缺省为 None 表示「无值」走回退链。"""

    chat_id: int
    message_id: int
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    track: int | None = None
    duration_sec: int | None = None
    file_size: int | None = None
    bitrate: int | None = None
    file_name: str | None = None
    caption: str | None = None
    message_date: str | None = None  # ISO-8601 UTC 文本（store 约定 §2.6）
    channel_title: str | None = None
    ext: str | None = None
    unique_id: str | None = None


@dataclass(slots=True)
class TemplateConfig:
    """模板配置：目录模板、文件名模板与日期格式（FR-NAME-01/06）。"""

    dir_template: str = "{artist}/{album}"
    file_template: str = "{track:02d} {title}"
    date_format: str = "%Y-%m"
    caption_artist_re: str = r"^\s*(?P<artist>.+?)\s*[-\u2013\u2014]\s*(?P<title>.+?)\s*$"
    save_path: PurePath = field(default_factory=lambda: Path("./library"))
