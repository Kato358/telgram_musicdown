"""path_builder 单测：回退链、空段省略、补零、截断、保留名、冲突（NFR-07）。"""

from __future__ import annotations

from pathlib import Path

from app.domain import TemplateConfig, TrackMeta
from app.services.path_builder import render_path, render_template, resolve_conflict


def make_cfg(tmp_path: Path, **kw) -> TemplateConfig:
    return TemplateConfig(save_path=tmp_path, **kw)


def test_render_path_default_template(tmp_path: Path) -> None:
    meta = TrackMeta(
        chat_id=-100,
        message_id=1,
        title="晴天",
        artist="周杰伦",
        album="叶惠美",
        track=3,
        ext="flac",
    )
    p = render_path(meta, make_cfg(tmp_path))
    assert p == tmp_path / "周杰伦" / "叶惠美" / "03 晴天.flac"


def test_render_path_album_missing_omits_album_segment(tmp_path: Path) -> None:
    # FR-NAME-03：{album} 缺失省略目录段，不生成 Unknown Album
    meta = TrackMeta(chat_id=-100, message_id=2, title="晴天", artist="周杰伦", track=3, ext="mp3")
    p = render_path(meta, make_cfg(tmp_path))
    assert p == tmp_path / "周杰伦" / "03 晴天.mp3"


def test_render_path_artist_fallback_unknown(tmp_path: Path) -> None:
    meta = TrackMeta(chat_id=-100, message_id=3, title="晴天", ext="mp3")
    p = render_path(meta, make_cfg(tmp_path))
    assert p == tmp_path / "Unknown Artist" / "晴天.mp3"


def test_render_path_empty_dir_template_is_flat(tmp_path: Path) -> None:
    # 目录模板存过空值（FR-NAME-01）：不建子目录，文件直接落保存根
    meta = TrackMeta(chat_id=-100, message_id=3, title="晴天", artist="周杰伦", ext="mp3")
    p = render_path(meta, make_cfg(tmp_path, dir_template=""))
    assert p == tmp_path / "晴天.mp3"


def test_render_path_title_fallback_chain(tmp_path: Path) -> None:
    # title: audio.title → file_name 去扩展名 → caption 首行 → message_{id}
    meta = TrackMeta(chat_id=-100, message_id=4, file_name="song.mp3", ext="mp3")
    assert render_path(meta, make_cfg(tmp_path)).name == "song.mp3"
    meta2 = TrackMeta(chat_id=-100, message_id=4, caption="第一行\n第二行", ext="mp3")
    assert render_path(meta2, make_cfg(tmp_path)).name == "第一行.mp3"
    meta3 = TrackMeta(chat_id=-100, message_id=42, ext="mp3")
    assert render_path(meta3, make_cfg(tmp_path)).name == "message_42.mp3"


def test_render_path_artist_caption_fallback(tmp_path: Path) -> None:
    # FR-NAME-05：caption 正则 "artist - title"，仅官方字段为空时启用
    meta = TrackMeta(chat_id=-100, message_id=5, caption="周杰伦 - 晴天", ext="mp3")
    p = render_path(meta, make_cfg(tmp_path))
    assert p == tmp_path / "周杰伦" / "晴天.mp3"


def test_render_template_date_uses_global_date_format() -> None:
    # FR-NAME-06：{date} 的默认形态由全局 date_format 决定（默认 %Y-%m）
    meta = TrackMeta(chat_id=-100, message_id=1, message_date="2024-05-17T18:30:00+00:00")
    assert render_template("{date}", meta, TemplateConfig()) == "2024-05"
    assert render_template("{date}", meta, TemplateConfig(date_format="%Y-%m-%d")) == "2024-05-17"


def test_render_template_explicit_date_filter_ignores_date_format() -> None:
    # 显式 {date:%…} 是覆盖：即使全局 date_format 换了形态，过滤器仍按原始日期渲染
    meta = TrackMeta(chat_id=-100, message_id=1, message_date="2024-05-17T18:30:00+00:00")
    cfg = TemplateConfig(date_format="%Y")
    assert render_template("{date:%Y-%m-%d}", meta, cfg) == "2024-05-17"


def test_render_template_date_missing_omits_segment() -> None:
    # 没有消息日期时整段省略（FR-NAME-03），不编造日期
    meta = TrackMeta(chat_id=-100, message_id=1)
    assert render_template("{date}", meta, TemplateConfig()) == ""


def test_render_template_track_zero_pad_and_truncate() -> None:
    meta = TrackMeta(chat_id=-100, message_id=6, track=7, caption="x" * 200)
    assert render_template("{track:02d}", meta, TemplateConfig()) == "07"
    out = render_template("{caption:truncate:10}", meta, TemplateConfig())
    assert out == "x" * 10


def test_render_path_illegal_chars_replaced(tmp_path: Path) -> None:
    meta = TrackMeta(chat_id=-100, message_id=7, title="a<b>:c", artist="周/伦", ext="mp3")
    p = render_path(meta, make_cfg(tmp_path))
    assert "<" not in p.name and ">" not in p.name
    assert "<" not in str(p.parent) and "/" not in p.parent.name


def test_render_path_reserved_name(tmp_path: Path) -> None:
    meta = TrackMeta(chat_id=-100, message_id=8, title="CON", ext="mp3")
    p = render_path(meta, make_cfg(tmp_path))
    assert p.stem != "CON"


def test_resolve_conflict_dedupe_size_match(tmp_path: Path) -> None:
    target = tmp_path / "song.mp3"
    target.write_bytes(b"12345")
    final, hit = resolve_conflict(target, expected_size=5)
    assert final == target and hit is True


def test_resolve_conflict_appends_counter(tmp_path: Path) -> None:
    target = tmp_path / "song.mp3"
    target.write_bytes(b"12345")
    final, hit = resolve_conflict(target, expected_size=999)
    assert hit is False
    assert final == tmp_path / "song (1).mp3"
    final.write_bytes(b"x")
    final2, _ = resolve_conflict(target, expected_size=999)
    assert final2 == tmp_path / "song (2).mp3"


def test_render_path_length_truncated(tmp_path: Path) -> None:
    meta = TrackMeta(chat_id=-100, message_id=9, title="曲" * 200, artist="艺" * 100, ext="mp3")
    p = render_path(meta, make_cfg(tmp_path))
    # 截断发生在 save_path 拼接前的相对路径上（≤230 字节）
    assert len(str(p.relative_to(tmp_path)).encode("utf-8")) <= 230
