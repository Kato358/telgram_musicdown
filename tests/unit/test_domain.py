"""单元测试：跨服务共用的纯规则（app/domain.py，NFR-07）。"""

from __future__ import annotations

from dataclasses import asdict

from app.domain import (
    SourceFilters,
    card_to_meta,
    is_audio_message,
    message_to_card,
    meta_from_dict,
    scope_allows,
    source_tags,
)
from tests.fakes import (
    make_audio_document_message,
    make_audio_message,
    make_video_message,
)

SCOPE = ["audio", "audio_document"]


def test_source_tags_ascii_keyword_needs_word_boundary() -> None:
    # FR-SRC-05：候选源筛选不放大假阳性——"ost" 不该命中 "hosting"
    assert "ost" not in source_tags("Hosting News")
    assert source_tags("OST Collection", None) == ["ost"]
    assert "影视原声" in source_tags("影视原声带")


def test_source_tags_uses_username_and_dedupes() -> None:
    tags = source_tags("无损音乐分享", "lossless_music")
    assert {"音乐", "无损", "music"} <= set(tags)
    assert len(tags) == len(set(tags))


def test_source_tags_misses_unrelated_title() -> None:
    assert source_tags("家庭相册", "family_photos") == []


def test_scope_allows_audio_and_document_separately() -> None:
    # FR-SRC-02 媒体范围：audio 与 audio_document 各自开关
    assert scope_allows(["audio"], make_audio_message(1))
    assert not scope_allows(["audio_document"], make_audio_message(1))
    assert scope_allows(["audio_document"], make_audio_document_message(2))
    assert not scope_allows(SCOPE, make_video_message(3))


def test_voice_never_passes_scope_or_audio_check() -> None:
    # 验收 #11：voice 既不算音频消息，也不在任何媒体范围内
    voice = make_audio_message(4, voice=True)
    assert not is_audio_message(voice)
    assert not scope_allows(SCOPE, voice)


def test_card_to_meta_roundtrips_through_dict() -> None:
    # 任务 payload 的序列化契约：TrackMeta ↔ dict 往返不丢字段
    meta = card_to_meta(message_to_card(make_audio_message(7), "Music Channel"))
    assert meta_from_dict(asdict(meta)) == meta
    assert meta.unique_id == "uniq_7"
    assert meta.mime == "audio/mpeg"
    assert meta.ext == "mp3"
    assert meta.channel_title == "Music Channel"


def test_source_filters_duration_and_extension() -> None:
    card = message_to_card(make_audio_message(8), "Music Channel")  # 269s, mp3
    assert SourceFilters.matches(None, card)
    assert SourceFilters.matches('{"min_duration": 60}', card)
    assert not SourceFilters.matches('{"max_duration": 60}', card)
    assert SourceFilters.matches('{"ext_whitelist": ["mp3"]}', card)
    assert not SourceFilters.matches('{"ext_whitelist": ["flac"]}', card)


def test_source_filters_broken_json_is_ignored() -> None:
    card = message_to_card(make_audio_message(9), None)
    assert SourceFilters.matches("{not json", card)
