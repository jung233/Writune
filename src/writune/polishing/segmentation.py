"""Markdown paragraph segmentation that preserves exact order and separators."""

from __future__ import annotations

import re


_FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_FENCE_CLOSE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*(?:\r?\n)?$")


def _opening_fence(line: str) -> tuple[str, int] | None:
    match = _FENCE_OPEN_RE.match(line)
    if not match:
        return None
    marker = match.group(1)
    return marker[0], len(marker)


def _is_closing_fence(line: str, char: str, minimum_length: int) -> bool:
    match = _FENCE_CLOSE_RE.match(line)
    return bool(match and match.group(1)[0] == char and len(match.group(1)) >= minimum_length)


def segment_markdown(text: str) -> list[str]:
    """Split Markdown into ordered paragraph/fence units without losing bytes.

    Blank-line separators stay attached to the preceding unit, so joining the
    returned strings reproduces the input exactly. Fenced blocks, including an
    unclosed fence through end-of-file, are kept as one segment.
    """

    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if not text:
        return []

    lines = text.splitlines(keepends=True)
    result: list[str] = []
    paragraph: list[str] = []
    pending_blank: list[str] = []
    index = 0

    def flush_paragraph() -> None:
        nonlocal paragraph, pending_blank
        if paragraph:
            result.append("".join(paragraph + pending_blank))
            paragraph = []
            pending_blank = []

    while index < len(lines):
        line = lines[index]
        fence = _opening_fence(line)
        if fence:
            # Finish text before a fence. Its separating blank lines remain
            # with the preceding unit; leading whitespace stays with the fence.
            if paragraph:
                flush_paragraph()
            elif result and pending_blank:
                result[-1] += "".join(pending_blank)
                pending_blank = []

            fence_lines = pending_blank + [line]
            pending_blank = []
            char, minimum_length = fence
            index += 1
            while index < len(lines):
                fence_lines.append(lines[index])
                if _is_closing_fence(lines[index], char, minimum_length):
                    index += 1
                    break
                index += 1
            result.append("".join(fence_lines))
            continue

        if not line.strip():
            if paragraph or result:
                pending_blank.append(line)
            else:
                # Preserve leading blank lines as a prefix of the first unit.
                pending_blank.append(line)
            index += 1
            continue

        if pending_blank:
            if paragraph:
                flush_paragraph()
            elif result:
                result[-1] += "".join(pending_blank)
                pending_blank = []
            # With no prior content, leading blank lines prefix this paragraph.
        if not paragraph:
            paragraph.extend(pending_blank)
            pending_blank = []
        paragraph.append(line)
        index += 1

    if paragraph:
        flush_paragraph()
    elif pending_blank:
        if result:
            result[-1] += "".join(pending_blank)
        else:
            result.append("".join(pending_blank))

    # Defensive invariant: no input character is dropped or reordered.
    if "".join(result) != text:
        return [text]
    return result
