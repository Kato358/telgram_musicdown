"""搜索纯规则单测（FR-SEARCH-01/03）：关键词规范化、相关度分级、二次筛选、排序、去重。

域层纯函数，无 Telegram/DB 依赖（NFR-07）。
"""

from __future__ import annotations

from app.domain import (
    SearchFilters,
    SearchResultCard,
    dedupe_cards,
    keyword_tokens,
    matches_fields,
    normalize_keyword,
    relevance_score,
    sort_cards,
)


def _card(
    message_id: int,
    *,
    title: str | None = "晴天",
    artist: str | None = "周杰伦",
    caption: str | None = None,
    duration: int | None = 269,
    size: int | None = 100,
    ext: str | None = "mp3",
    date: str | None = "2026-01-01T00:00:00+00:00",
    unique: str | None = None,
) -> SearchResultCard:
    return SearchResultCard(
        chat_id=-1001,
        message_id=message_id,
        title=title,
        artist=artist,
        duration_sec=duration,
        file_size=size,
        ext=ext,
        mime="audio/mpeg",
        channel_title="频道",
        message_date=date,
        caption=caption,
        file_unique_id=unique if unique is not None else f"uniq_{message_id}",
    )


# ---- 规范化 ----


def test_normalize_keyword_collapses_case_and_whitespace() -> None:
    assert normalize_keyword("  Jay   Chou ") == "jay chou"
    assert keyword_tokens("  Jay   Chou ") == ["jay", "chou"]


# ---- 相关度 ----


def test_relevance_ranks_exact_above_prefix_above_contains() -> None:
    assert relevance_score(_card(1, title="晴天"), ["晴天"]) > relevance_score(
        _card(2, title="晴天了"), ["晴天"]
    )
    assert relevance_score(_card(2, title="晴天了"), ["晴天"]) > relevance_score(
        _card(3, title="我的晴天日记"), ["晴天"]
    )


def test_relevance_weights_title_above_artist_above_caption() -> None:
    title_hit = relevance_score(_card(1, title="晴天", artist=None, caption=None), ["晴天"])
    artist_hit = relevance_score(_card(2, title="夜曲", artist="晴天", caption=None), ["晴天"])
    caption_hit = relevance_score(_card(3, title="夜曲", artist=None, caption="晴天"), ["晴天"])
    assert title_hit > artist_hit > caption_hit


def test_relevance_requires_every_token() -> None:
    both = _card(1, title="周杰伦 晴天", artist=None, caption=None)
    one = _card(2, title="晴天", artist=None, caption=None)
    assert relevance_score(both, ["周杰伦", "晴天"]) > relevance_score(one, ["周杰伦", "晴天"])
    assert relevance_score(
        _card(3, title="无关", artist=None, caption=None), ["周杰伦", "晴天"]
    ) == (0, 0)


def test_relevance_ignores_missing_fields() -> None:
    assert relevance_score(_card(1, title=None, artist=None, caption=None), ["晴天"]) == (0, 0)


# ---- 筛选 ----


def test_filters_parse_ignores_junk() -> None:
    parsed = SearchFilters.parse(
        {
            "duration_min": -5,
            "duration_max": "600",
            "size_min": 100,
            "size_max": None,
            "date_from": "2024-1-2",
            "date_to": "2025-01-01",
            "exts": ["MP3", ".flac", 7],
        }
    )
    assert parsed.duration_min is None  # 负数不收
    assert parsed.duration_max is None  # 字符串不收（不猜）
    assert parsed.size_min == 100
    assert parsed.size_max is None
    assert parsed.date_from is None  # 未补零的日期会让文本序比较出错，不收
    assert parsed.date_to == "2025-01-01"
    assert parsed.exts == ("mp3", "flac")


def test_filters_inactive_when_empty() -> None:
    assert SearchFilters.parse(None).active is False
    assert SearchFilters.parse({"unknown": 1}).active is False
    assert SearchFilters.parse({"size_min": 0}).active is True


def test_filters_exclude_unknown_values_when_range_set() -> None:
    # 筛「时长 ≥ 100」时，一条时长未知的卡片无法证明它满足 —— 不放行
    filters = SearchFilters.parse({"duration_min": 100})
    assert filters.matches(_card(1, duration=269)) is True
    assert filters.matches(_card(2, duration=99)) is False
    assert filters.matches(_card(3, duration=None)) is False


def test_filters_match_dates_and_exts() -> None:
    filters = SearchFilters.parse(
        {"date_from": "2025-06-01", "date_to": "2025-12-31", "exts": ["flac"]}
    )
    assert filters.matches(_card(1, date="2025-07-01T00:00:00+00:00", ext="flac")) is True
    assert filters.matches(_card(2, date="2025-01-01T00:00:00+00:00", ext="flac")) is False
    assert filters.matches(_card(3, date="2025-07-01T00:00:00+00:00", ext="mp3")) is False


def test_filters_without_date_excludes_missing_date() -> None:
    filters = SearchFilters.parse({"date_from": "2025-01-01"})
    assert filters.matches(_card(1, date=None)) is False


# ---- 勾选字段过滤 ----


def test_matches_fields_ignores_unknown_field_names() -> None:
    card = _card(1, title="夜曲", artist=None, caption=None)
    # album 卡片上没有、file_name 已回退进 title：勾了它们不该把结果筛空
    assert matches_fields(card, ["晴天"], ["album"]) is True
    assert matches_fields(card, ["晴天"], None) is True
    assert matches_fields(card, ["晴天"], []) is True


def test_matches_fields_requires_each_token_in_some_field() -> None:
    card = _card(1, title="晴天", artist="周杰伦", caption=None)
    assert matches_fields(card, ["周杰伦", "晴天"], ["title"]) is False
    assert matches_fields(card, ["周杰伦", "晴天"], ["title", "artist"]) is True


# ---- 排序 ----


def test_sort_by_date_is_newest_first_with_missing_last() -> None:
    old = _card(1, date="2020-01-01T00:00:00+00:00")
    new = _card(2, date="2026-01-01T00:00:00+00:00")
    unknown = _card(3, date=None)
    assert [c.message_id for c in sort_cards([old, unknown, new], "date", "晴天")] == [2, 1, 3]


def test_sort_by_duration_and_size_put_unknown_last() -> None:
    short = _card(1, duration=100, size=10)
    long = _card(2, duration=400, size=99)
    unknown = _card(3, duration=None, size=None)
    assert [c.message_id for c in sort_cards([short, unknown, long], "duration", "晴天")] == [
        2,
        1,
        3,
    ]
    assert [c.message_id for c in sort_cards([short, unknown, long], "size", "晴天")] == [2, 1, 3]


def test_sort_relevance_breaks_ties_by_date() -> None:
    older = _card(1, date="2020-01-01T00:00:00+00:00")
    newer = _card(2, date="2026-01-01T00:00:00+00:00")
    assert [c.message_id for c in sort_cards([older, newer], "relevance", "晴天")] == [2, 1]


def test_sort_unknown_falls_back_to_relevance() -> None:
    exact = _card(1, title="晴天")
    loose = _card(2, title="我的晴天")
    assert [c.message_id for c in sort_cards([loose, exact], "bogus", "晴天")] == [1, 2]


# ---- 去重 ----


def test_dedupe_keeps_first_of_same_file() -> None:
    first = _card(1, unique="same")
    forwarded = _card(2, unique="same")
    other = _card(3, unique="other")
    assert [c.message_id for c in dedupe_cards([first, forwarded, other])] == [1, 3]


def test_dedupe_falls_back_to_chat_and_message() -> None:
    # file_unique_id 缺失（空串按缺失处理）时退回 (chat_id, message_id)
    a = _card(1, unique="")
    b = _card(1, unique="")
    c = _card(2, unique="")
    assert [x.message_id for x in dedupe_cards([a, b, c])] == [1, 2]
