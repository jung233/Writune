"""Deterministic checks for polishing output."""

from __future__ import annotations

import re
from collections import Counter

from .errors import ValidationError
from .protect import _PLACEHOLDER_RE, _addition_check_fragments, _protected_fragments


_PREFACE_RE = re.compile(
    r"^(?:"
    r"here(?:'|’)s\s+(?:the\s+)?(?:polished|revised|edited)\b|"
    r"here\s+is\s+(?:the\s+)?(?:polished|revised|edited)\b|"
    r"(?:polished|revised|edited)\s+version\s*:|"
    r"sure\s*[,!:—-]|certainly\s*[,!:—-]|of\s+course\s*[,!:—-]|"
    r"as\s+an\s+ai\b|i\s+(?:cannot|can't|can’t|won't|won’t|am unable to)\b|"
    r"sorry\s*[,!:—-]\s*(?:i\s+)?(?:cannot|can't|can’t|won't|won’t|am unable)\b"
    r"|(?:以下是|下面是|这是)(?:润色|修改|优化|改写)(?:后|过)?的(?:文本|正文|版本|结果)\s*[:：]"
    r"|(?:润色|修改|优化|改写)(?:后|过)?的(?:文本|正文|版本|结果)\s*[:：]"
    r")",
    re.IGNORECASE,
)
_TRUNCATION_RE = re.compile(
    r"(?:\[\s*(?:truncated|cut\s+off|incomplete)\s*\]|"
    r"\((?:truncated|cut\s+off|incomplete)\)|"
    r"(?:\.\.\.|…)[ \t]*(?:\[(?:truncated|cut\s+off)\])?)\s*$",
    re.IGNORECASE,
)
_INCOMPLETE_END_RE = re.compile(
    r"(?:[,;:]|\b(?:and|or|but|because|with|of|to|for|from|including|such\s+as|which|that))\s*$",
    re.IGNORECASE,
)
_OPEN_FENCE_RE = re.compile(r"(?m)^ {0,3}(`{3,}|~{3,})[^\r\n]*(?:\r?\n|$)")
_CLOSE_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*(?:\r?\n)?$")


def _unclosed_fence(text: str) -> bool:
    fence_char: str | None = None
    fence_length = 0
    for line in text.splitlines(keepends=True):
        if fence_char is None:
            match = _OPEN_FENCE_RE.match(line)
            if match:
                fence = match.group(1)
                fence_char, fence_length = fence[0], len(fence)
            continue
        close = _CLOSE_FENCE_RE.match(line)
        if close and close.group(1)[0] == fence_char and len(close.group(1)) >= fence_length:
            fence_char = None
            fence_length = 0
    return fence_char is not None


def _has_incomplete_ending(text: str) -> bool:
    stripped = text.rstrip()
    if not stripped:
        return False
    # Existing prose may be a heading or a deliberate fragment. Mark only a
    # clearly open conjunction or punctuation ending as incomplete.
    return bool(_INCOMPLETE_END_RE.search(stripped))


def validate(original: str, output: str) -> None:
    """Raise ``ValidationError`` when output violates deterministic safeguards.

    These checks cover preservation and obvious formatting failures; they are
    not a semantic or factual review of the edited prose.
    """

    if not isinstance(original, str) or not isinstance(output, str):
        raise TypeError("original and output must be strings")

    polished = output.strip()
    source = original.strip()
    if not polished:
        raise ValidationError("Polished output is empty", code="EMPTY_OUTPUT")

    if _PREFACE_RE.match(polished):
        raise ValidationError("Output contains a preamble or refusal", code="UNWANTED_PREAMBLE")
    if _PLACEHOLDER_RE.search(polished):
        raise ValidationError("Output contains an unresolved protected placeholder", code="PLACEHOLDER_LOST")
    if _TRUNCATION_RE.search(polished):
        raise ValidationError("Output appears to be explicitly truncated", code="TRUNCATED_OUTPUT")
    if _unclosed_fence(polished):
        raise ValidationError("Output contains an unclosed Markdown code fence", code="TRUNCATED_OUTPUT")
    if not _has_incomplete_ending(source) and _has_incomplete_ending(polished):
        raise ValidationError("Output appears to end mid-sentence", code="TRUNCATED_OUTPUT")

    if len(source) >= 120 and len(polished) < max(30, int(len(source) * 0.25)):
        raise ValidationError("Output is abnormally short compared with the source", code="OUTPUT_TOO_SHORT")
    maximum_length = max(120, len(source) * 4, len(source) + 100)
    if len(polished) > maximum_length:
        raise ValidationError("Output is abnormally long compared with the source", code="OUTPUT_TOO_LONG")

    original_fragments = Counter(_protected_fragments(original))
    output_fragments = Counter(_protected_fragments(output))
    missing = original_fragments - output_fragments
    if missing:
        example = next(iter(missing))
        raise ValidationError(
            f"Output changed or omitted a protected value or reference: {example}",
            code="PROTECTED_CONTENT_CHANGED",
        )

    original_addition_fragments = Counter(_addition_check_fragments(original))
    output_addition_fragments = Counter(_addition_check_fragments(output))
    added = output_addition_fragments - original_addition_fragments
    if added:
        _, example = next(iter(added))
        raise ValidationError(
            f"Output introduced an additional protected value, reference, or structure: {example}",
            code="PROTECTED_CONTENT_ADDED",
        )
