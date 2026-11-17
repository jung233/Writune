"""Markdown segmentation that preserves exact order and separators."""

from __future__ import annotations

import re


_FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_FENCE_CLOSE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*(?:\r?\n)?$")
_ATX_HEADING_RE = re.compile(r"^ {0,3}#{1,6}(?:[ \t]+[^\r\n]*)?(?:\r?\n)?$")
_SETEXT_UNDERLINE_RE = re.compile(r"^ {0,3}(?:=+[ \t]*|-+[ \t]*)(?:\r?\n)?$")
_NONPARAGRAPH_BLOCK_RE = re.compile(
    r"^ {0,3}(?:>|(?:[-+*]|\d{1,9}[.)])[ \t]+|<!--|</?(?:address|article|aside|base|blockquote|body|caption|center|col|dd|details|dialog|dir|div|dl|dt|fieldset|figcaption|figure|footer|form|frame|frameset|h[1-6]|head|header|hr|html|iframe|legend|li|link|main|menu|menuitem|meta|nav|noframes|ol|optgroup|option|p|param|search|section|summary|table|tbody|td|tfoot|th|thead|title|tr|track|ul)(?:[ \t/>]|$))",
    re.IGNORECASE,
)


def _opening_fence(line: str) -> tuple[str, int] | None:
    match = _FENCE_OPEN_RE.match(line)
    if not match:
        return None
    marker = match.group(1)
    return marker[0], len(marker)


def _is_closing_fence(line: str, char: str, minimum_length: int) -> bool:
    match = _FENCE_CLOSE_RE.match(line)
    return bool(match and match.group(1)[0] == char and len(match.group(1)) >= minimum_length)


def segment_markdown(text: str, segmentation: str = "paragraph") -> list[str]:
    """Split Markdown into paragraph, heading-section, or document units.

    Every mode preserves source order and separators; joining the returned
    strings reproduces ``text`` exactly. ``section`` recognizes ATX and
    Setext headings outside fenced code blocks. Each heading starts a unit
    that continues through the content before the next heading. A nonblank
    preamble is its own unit. ``document`` returns the complete input as one
    unit.
    """

    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if segmentation not in ("paragraph", "section", "document"):
        raise ValueError("segmentation must be paragraph, section, or document")
    if not text:
        return []
    if segmentation == "document":
        return [text]
    if segmentation == "section":
        return _segment_sections(text)
    return _segment_paragraphs(text)


def _segment_paragraphs(text: str) -> list[str]:
    """Split Markdown into ordered paragraph/fence units without losing bytes.

    Blank-line separators stay attached to the preceding unit, so joining the
    returned strings reproduces the input exactly. Fenced blocks, including an
    unclosed fence through end-of-file, are kept as one segment.
    """

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


def _segment_sections(text: str) -> list[str]:
    """Split at Markdown headings outside fenced code blocks."""

    lines = text.splitlines(keepends=True)
    heading_starts: list[int] = []
    paragraph_start: int | None = None
    fence: tuple[str, int] | None = None

    for index, line in enumerate(lines):
        if fence is not None:
            if _is_closing_fence(line, *fence):
                fence = None
                paragraph_start = None
            continue

        opening = _opening_fence(line)
        if opening is not None:
            fence = opening
            paragraph_start = None
            continue

        if _ATX_HEADING_RE.match(line):
            heading_starts.append(index)
            paragraph_start = None
            continue

        if _SETEXT_UNDERLINE_RE.match(line):
            if paragraph_start is not None:
                heading_starts.append(paragraph_start)
                paragraph_start = None
            continue

        if not line.strip():
            paragraph_start = None
            continue

        # Setext headings can span consecutive paragraph lines. Block starts
        # such as lists, block quotes, and HTML blocks cannot become their
        # title text, so start a fresh candidate after them.
        if len(line) - len(line.lstrip(" ")) >= 4 or _NONPARAGRAPH_BLOCK_RE.match(line):
            paragraph_start = None
        elif paragraph_start is None:
            paragraph_start = index

    if not heading_starts:
        return [text]

    starts = sorted(set(heading_starts))
    segments: list[str] = []
    first_heading = starts[0]
    if "".join(lines[:first_heading]).strip():
        segments.append("".join(lines[:first_heading]))
        section_start = first_heading
    else:
        # Leading blank lines are kept with the first heading rather than
        # producing an empty preamble unit.
        section_start = 0

    for next_heading in starts[1:]:
        segments.append("".join(lines[section_start:next_heading]))
        section_start = next_heading
    segments.append("".join(lines[section_start:]))

    if "".join(segments) != text:
        return [text]
    return segments
