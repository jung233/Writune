from __future__ import annotations

from writune.providers.tps import SlidingTPS, TokenCounter, approximate_token_count


def test_approximate_tokenizer_counts_words_and_unicode_without_using_bytes() -> None:
    assert approximate_token_count("hello world") == 2
    assert approximate_token_count("你好世界") == 4
    assert approximate_token_count("é") != len("é".encode("utf-8"))


def test_configured_tokenizer_is_used_and_marks_rate_exact() -> None:
    counter = TokenCounter(lambda text: len(text.split()) * 2)
    assert counter.count("two words") == 4
    assert counter.estimated is False


def test_chunk_boundaries_do_not_inflate_cumulative_token_count() -> None:
    detector=SlidingTPS(min_tps=0,window_seconds=1,warmup_tokens=30,warmup_seconds=10,consecutive_slow_windows=2,counter=TokenCounter(lambda text: len(text.split())))
    detector.observe('hel',0)
    detector.observe('lo ',.1)
    detector.observe('wor',.2)
    detector.observe('ld',.3)
    assert detector.token_count==2


def test_warmup_prevents_short_text_from_failing_low_rate_check() -> None:
    detector = SlidingTPS(
        min_tps=6,
        window_seconds=1,
        warmup_tokens=30,
        warmup_seconds=10,
        consecutive_slow_windows=2,
        counter=TokenCounter(lambda text: len(text)),
    )

    assert detector.observe("short", 0.0) is False
    assert detector.observe("", 20.0) is False
    assert detector.token_count == 5


def test_low_rate_requires_consecutive_slow_windows_after_warmup() -> None:
    detector = SlidingTPS(
        min_tps=6,
        window_seconds=1,
        warmup_tokens=30,
        warmup_seconds=0,
        consecutive_slow_windows=2,
        counter=TokenCounter(lambda text: len(text)),
    )

    assert detector.observe("x" * 30, 0.0) is False
    assert detector.observe("x", 1.0) is False
    assert detector.observe("x", 2.0) is True
    assert detector.latest_window_tps < 6


def test_fast_window_resets_consecutive_slow_counter() -> None:
    detector = SlidingTPS(
        min_tps=6,
        window_seconds=1,
        warmup_tokens=1,
        warmup_seconds=0,
        consecutive_slow_windows=2,
        counter=TokenCounter(lambda text: len(text)),
    )

    assert detector.observe("x", 0.0) is False
    assert detector.observe("x", 1.0) is False  # slow window 1
    assert detector.observe("x" * 10, 2.0) is False  # fast window resets
    assert detector.observe("x", 3.0) is False  # slow window 1 again
    assert detector.observe("x", 4.0) is True  # slow window 2
