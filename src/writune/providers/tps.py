"""Token counting and warmup-aware sliding-window generation speed checks."""

from __future__ import annotations

import importlib
import re
from collections import deque
from collections.abc import Callable


_APPROX_TOKEN = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]"
    r"|[\u3040-\u30ff]"
    r"|[\uac00-\ud7af]"
    r"|\b\w+\b"
    r"|[^\w\s]",
    re.UNICODE,
)


def approximate_token_count(text: str) -> int:
    """Count lexical units, never bytes, as a language-neutral approximation."""

    return len(_APPROX_TOKEN.findall(text))


class TokenCounter:
    """Use a configured tokenizer when available, otherwise estimate tokens."""

    def __init__(self, tokenizer: object | None = None) -> None:
        self._counter: Callable[[str], int] | None = self._resolve(tokenizer)
        self.estimated = self._counter is None

    @staticmethod
    def _resolve(tokenizer: object | None) -> Callable[[str], int] | None:
        if tokenizer is None:
            return None
        if callable(tokenizer):
            return lambda text: int(tokenizer(text))
        for method_name in ("count_tokens", "tokenize", "encode"):
            method = getattr(tokenizer, method_name, None)
            if callable(method):
                if method_name=='count_tokens':
                    return lambda text,method=method: int(method(text))
                return lambda text, method=method: len(method(text))
        if isinstance(tokenizer, str) and tokenizer.strip():
            try:
                module = importlib.import_module("tiktoken")
                encoder = module.get_encoding(tokenizer)
            except (ImportError, KeyError, ValueError):
                return None
            return lambda text: len(encoder.encode(text))
        return None

    def count(self, text: str) -> int:
        if not text:
            return 0
        if self._counter is not None:
            try:
                return max(0, self._counter(text))
            except Exception:
                self.estimated = True
                self._counter = None
        return approximate_token_count(text)


class SlidingTPS:
    """Track output rate in consecutive, adjacent windows after warmup."""

    def __init__(
        self,
        *,
        min_tps: float,
        window_seconds: float,
        warmup_tokens: int,
        warmup_seconds: float,
        consecutive_slow_windows: int,
        counter: TokenCounter,
    ) -> None:
        self.min_tps = max(0.0, float(min_tps))
        self.window_seconds = max(0.001, float(window_seconds))
        self.warmup_tokens = max(0, int(warmup_tokens))
        self.warmup_seconds = max(0.0, float(warmup_seconds))
        self.consecutive_slow_windows = max(1, int(consecutive_slow_windows))
        self.counter = counter
        self._events: deque[tuple[float, int]] = deque()
        self._first_text_at: float | None = None
        self._next_window_end: float | None = None
        self._total_tokens = 0
        self._pruned_tokens = 0
        self._slow_windows = 0
        self._latest_rate = 0.0
        self._text = ''

    @property
    def token_count(self) -> int:
        return self._total_tokens

    @property
    def generation_tps(self) -> float:
        if self._first_text_at is None or not self._events:
            return 0.0
        elapsed = self._events[-1][0] - self._first_text_at
        if elapsed <= 0:
            return 0.0
        return self._total_tokens / elapsed

    @property
    def latest_window_tps(self) -> float:
        return self._latest_rate

    def observe(self, text: str, timestamp: float) -> bool:
        """Record a nonempty text delta and report whether TPS has failed."""

        if not text:
            return False
        self._text += text
        cumulative=self.counter.count(self._text)
        count=cumulative-self._total_tokens
        if self._first_text_at is None:
            self._first_text_at = timestamp
            self._next_window_end = timestamp + max(
                self.window_seconds, self.warmup_seconds
            )
        self._total_tokens = cumulative
        self._events.append((timestamp, count))

        if self._next_window_end is None:
            return False
        # Evaluate each elapsed adjacent window once. A single slow interval
        # cannot fail the request until the configured consecutive threshold.
        while timestamp >= self._next_window_end:
            end = self._next_window_end
            start = end - self.window_seconds
            window_tokens = sum(
                tokens for instant, tokens in self._events if start < instant <= end
            )
            generated_by_end = self._pruned_tokens + sum(
                tokens for instant, tokens in self._events if instant <= end
            )
            elapsed_by_end = end - self._first_text_at
            if (
                generated_by_end >= self.warmup_tokens
                and elapsed_by_end >= self.warmup_seconds
            ):
                self._latest_rate = window_tokens / self.window_seconds
                if self._latest_rate < self.min_tps:
                    self._slow_windows += 1
                else:
                    self._slow_windows = 0
                if self._slow_windows >= self.consecutive_slow_windows:
                    return True
            self._next_window_end += self.window_seconds
            cutoff = self._next_window_end - self.window_seconds
            while self._events and self._events[0][0] <= cutoff:
                _, pruned_count = self._events.popleft()
                self._pruned_tokens += pruned_count
        return False


__all__ = ["SlidingTPS", "TokenCounter", "approximate_token_count"]
