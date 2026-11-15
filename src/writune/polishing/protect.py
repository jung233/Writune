"""Conservative protection of facts that polishing must preserve."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field

from .errors import ValidationError


_NUMBER = r"(?:[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|[+-]?\.\d+)(?:[eE][+-]?\d+)?(?:%|‰)?"
_AUTHOR_NAME = r"(?:[A-Z]\.\s+){0,2}[A-Z][A-Za-z'’–-]+(?:-[A-Z][A-Za-z'’–-]+)*"
_NARRATIVE_AUTHOR_GROUP = (
    rf"{_AUTHOR_NAME}(?:"
    rf"\s+et\s+al\."
    rf"|\s+(?:and|&)\s+{_AUTHOR_NAME}"
    rf"|(?:,\s*{_AUTHOR_NAME}){{1,2}}(?:\s*,?\s*(?:and|&)\s*{_AUTHOR_NAME})?"
    rf")?"
)

# More structured expressions come first. The selector below also gives earlier
# patterns priority when several candidates begin at the same position.
_PROTECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\$\$[\s\S]*?\$\$|(?<!\\)\$(?!\$|\{)[^\n$]+\$|"
        r"\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\)|"
        r"\\begin\{(?P<env>equation\*?|align\*?|gather\*?|math)\}"
        r"[\s\S]*?\\end\{(?P=env)\}"
    ),
    re.compile(
        r"\\(?:cite\w*|parencite|textcite|autocite|ref|eqref|pageref|autoref|cref|Cref|label)"
        r"\*?(?:\[[^\]]*\])*\{[^{}]*\}"
    ),
    re.compile(r"(?<!\w)\[(?:\s*\d+[a-z]?(?:\s*[,;–-]\s*\d+[a-z]?)*\s*)\](?!\w)"),
    re.compile(
        r"(?<!\w)\((?:[A-Z][A-Za-z'’-]+(?:\s+et\s+al\.)?,?\s*"
        r"(?:19|20)\d{2}[a-z]?(?:\s*;\s*[A-Z][A-Za-z'’-]+(?:\s+et\s+al\.)?,?\s*"
        r"(?:19|20)\d{2}[a-z]?)*)\)(?!\w)"
    ),
    re.compile(
        rf"(?<!\w){_NARRATIVE_AUTHOR_GROUP}\s+"
        r"\((?:19|20)\d{2}[a-z]?(?:\s*[,;]\s*(?:19|20)\d{2}[a-z]?)*\)(?!\w)"
    ),
    re.compile(r"(?i)\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b"),
    re.compile(r"(?i)\b(?:PMID|PMCID|NCT)\s*[:#]?\s*\d+\b"),
    re.compile(
        r"(?i)\b(?:GSE|GSM|GPL|SRP|SRR|SRS|PRJNA|PRJEB|PRJDB|E-MTAB-|PXD|ERP|DRP)"
        r"[A-Z0-9_-]+\b"
    ),
    re.compile(r"https?://[^\s)\]>]+", re.IGNORECASE),
    re.compile(
        rf"(?i)(?<!\w)(?:p|q)\s*(?:[- ]?value)?\s*(?:is|was|=|<|>|≤|≥|≈|≠)\s*{_NUMBER}"
        rf"(?:\s*[,;]\s*{_NUMBER})?"
    ),
    re.compile(
        rf"(?i)(?<!\w)(?:95\s*%\s*)?(?:confidence\s+interval|CI)\s*[:=]?\s*"
        rf"\[?\s*{_NUMBER}(?:\s*[,;–—-]\s*{_NUMBER})?\s*\]?"
    ),
    re.compile(
        rf"(?i)(?<!\w)(?:OR|RR|HR|IRR|AOR|SHR|log2FC|logFC|FC)\s*(?:=|:)?\s*{_NUMBER}"
    ),
    re.compile(
        rf"(?i)(?<!\w)(?:n|N|t|z|F|U|W|rho|r|R\^2|R²|df|chi[- ]square|χ²)"
        rf"\s*(?:\([^)]{{1,40}}\))?\s*(?:=|:|<|>|≤|≥|≈)\s*{_NUMBER}"
    ),
    # Keep ranges as one item so changing a range dash cannot silently reverse
    # or alter its meaning.
    re.compile(rf"(?<![\w.]){_NUMBER}\s*(?:[-–—]|\bto\b)\s*{_NUMBER}(?!\w)", re.IGNORECASE),
    # These symbols can carry independent statistical meaning outside delimited
    # math, so preserve the symbols without masking neighboring prose.
    re.compile(r"(?<![\w])(?:≤|≥|±|χ²|χ\^2|χ2)(?![\w])"),
    re.compile(r"\\[A-Za-z@]+\*?"),
    # Mixed-case symbols that include digits (for example p53 or H3K27me3)
    # are common in gene and protein notation and are hard to infer from prose.
    re.compile(r"(?<![A-Za-z0-9_])(?=[A-Za-z0-9_-]*\d)[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*(?![A-Za-z0-9_])"),
    re.compile(r"(?<![A-Za-z0-9_])(?:[A-Z]{2,8}\d{0,4}[A-Z]?|[A-Z]{2,8}-[A-Z0-9]{1,8}|[A-Z]\d{1,4}[A-Z]?)(?![A-Za-z0-9_])"),
    re.compile(rf"(?<![\w.]){_NUMBER}(?!\w)", re.IGNORECASE),
)

_PLACEHOLDER_RE = re.compile(r"⟦WTN-[0-9a-f]{12}-\d{4,}⟧")
_TEX_COMMAND_RE = re.compile(r"\\(?:[A-Za-z@]+|.)")
_TEX_ENV_RE = re.compile(r"\\(begin|end)\s*\{\s*([A-Za-z]+\*?)\s*\}")
_MATH_ENVIRONMENTS = {
    "align", "align*", "alignat", "alignat*", "aligned", "alignedat", "array",
    "bmatrix", "Bmatrix", "cases", "displaymath", "equation", "equation*",
    "eqnarray", "eqnarray*", "flalign", "flalign*", "gather", "gather*",
    "gathered", "matrix", "math", "multline", "multline*", "pmatrix", "smallmatrix",
    "split", "Vmatrix", "vmatrix",
}
_MARKDOWN_FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_MARKDOWN_FENCE_CLOSE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*(?:\r?\n)?$")
_BACKTICK_RUN_RE = re.compile(r"(?<!`)(`+)(?!`)")


def _placeholder_spans(text: str) -> list[tuple[int, int, str]]:
    """Find balanced double-brace user placeholders, including nested ones."""

    spans: list[tuple[int, int, str]] = []
    index = 0
    while index < len(text) - 1:
        if not text.startswith("{{", index):
            index += 1
            continue
        start = index
        depth = 1
        index += 2
        while index < len(text) - 1 and depth:
            if text.startswith("{{", index):
                depth += 1
                index += 2
            elif text.startswith("}}", index):
                depth -= 1
                index += 2
            else:
                index += 1
        if depth == 0:
            spans.append((start, index, text[start:index]))
    return spans


def _dollar_brace_placeholder_spans(text: str) -> list[tuple[int, int, str]]:
    """Find explicit ``${...}`` placeholders without treating prose as code."""

    return [
        (match.start(), match.end(), match.group(0))
        for match in re.finditer(r"\$\{[^{}\r\n]+\}", text)
    ]


def _markdown_fence_spans(text: str) -> list[tuple[int, int, str]]:
    """Find complete Markdown fenced blocks, including unclosed EOF fences."""

    lines = text.splitlines(keepends=True)
    offsets: list[int] = []
    cursor = 0
    for line in lines:
        offsets.append(cursor)
        cursor += len(line)

    spans: list[tuple[int, int, str]] = []
    index = 0
    while index < len(lines):
        opening = _MARKDOWN_FENCE_OPEN_RE.match(lines[index])
        if not opening:
            index += 1
            continue
        marker = opening.group(1)
        char, minimum_length = marker[0], len(marker)
        start = offsets[index]
        end_index = index + 1
        while end_index < len(lines):
            closing = _MARKDOWN_FENCE_CLOSE_RE.match(lines[end_index])
            if (
                closing
                and closing.group(1)[0] == char
                and len(closing.group(1)) >= minimum_length
            ):
                end_index += 1
                break
            end_index += 1
        end = offsets[end_index] if end_index < len(lines) else len(text)
        if end_index == len(lines):
            end = len(text)
        spans.append((start, end, text[start:end]))
        index = end_index
    return spans


def _markdown_inline_code_spans(
    text: str,
    fenced_spans: list[tuple[int, int, str]],
) -> list[tuple[int, int, str]]:
    """Find Markdown backtick code spans while preserving exact delimiters."""

    spans: list[tuple[int, int, str]] = []
    regions: list[tuple[int, int]] = []
    cursor = 0
    for start, end, _ in fenced_spans:
        if cursor < start:
            regions.append((cursor, start))
        cursor = end
    if cursor < len(text):
        regions.append((cursor, len(text)))

    for region_start, region_end in regions:
        runs = list(_BACKTICK_RUN_RE.finditer(text, region_start, region_end))
        index = 0
        while index < len(runs):
            opening = runs[index]
            run_length = len(opening.group(1))
            closing_index = index + 1
            while closing_index < len(runs):
                if len(runs[closing_index].group(1)) == run_length:
                    closing = runs[closing_index]
                    spans.append((opening.start(), closing.end(), text[opening.start():closing.end()]))
                    index = closing_index + 1
                    break
                closing_index += 1
            else:
                index += 1
    return spans


def _balanced_group_end(text: str, start: int) -> int | None:
    """Return the exclusive end of a balanced TeX ``{}`` or ``[]`` group."""

    pairs = {"{": "}", "[": "]"}
    if start >= len(text) or text[start] not in pairs:
        return None
    stack = [pairs[text[start]]]
    index = start + 1
    while index < len(text):
        char = text[index]
        if char == "\\":
            # Escaped braces and brackets do not affect nesting.
            index += 2
            continue
        if char in pairs:
            stack.append(pairs[char])
        elif char in "}]":
            if not stack or char != stack[-1]:
                return None
            stack.pop()
            if not stack:
                return index + 1
        index += 1
    return None


def _latex_command_spans(text: str) -> list[tuple[int, int, str]]:
    """Find control sequences with all immediately following balanced arguments."""

    spans: list[tuple[int, int, str]] = []
    for command in _TEX_COMMAND_RE.finditer(text):
        start = command.start()
        end = command.end()
        if end < len(text) and text[end] == "*":
            end += 1
        cursor = end
        while cursor < len(text):
            group_start = cursor
            while group_start < len(text) and text[group_start].isspace():
                group_start += 1
            if group_start >= len(text) or text[group_start] not in "[{":
                break
            group_end = _balanced_group_end(text, group_start)
            if group_end is None:
                break
            end = group_end
            cursor = group_end
        spans.append((start, end, text[start:end]))
    return spans


def _latex_math_environment_spans(text: str) -> list[tuple[int, int, str]]:
    """Find complete math environments, including nested instances of a name."""

    tags = list(_TEX_ENV_RE.finditer(text))
    spans: list[tuple[int, int, str]] = []
    for index, opening in enumerate(tags):
        if opening.group(1) != "begin" or opening.group(2) not in _MATH_ENVIRONMENTS:
            continue
        environment = opening.group(2)
        depth = 1
        closing: re.Match[str] | None = None
        for tag in tags[index + 1:]:
            if tag.group(2) != environment:
                continue
            if tag.group(1) == "begin":
                depth += 1
            else:
                depth -= 1
                if depth == 0:
                    closing = tag
                    break
        if closing is None:
            continue
        start = opening.start()
        spans.append((start, closing.end(), text[start:closing.end()]))
    return spans


def _structural_spans(text: str) -> list[tuple[int, int, int, str]]:
    """Return structural spans with precedence over individual fact matches."""

    fenced_spans = _markdown_fence_spans(text)
    spans: list[tuple[int, int, int, str]] = []
    sources = (
        (0, [*_placeholder_spans(text), *_dollar_brace_placeholder_spans(text)]),
        (1, fenced_spans),
        (2, _markdown_inline_code_spans(text, fenced_spans)),
        (3, _latex_math_environment_spans(text)),
        (4, _latex_command_spans(text)),
    )
    for priority, source in sources:
        spans.extend((start, end, priority, value) for start, end, value in source)
    return spans


def _protected_spans(text: str, terms=None) -> list[tuple[int, int, str]]:
    """Return non-overlapping protected spans in source order."""
    candidates: list[tuple[int, int, int, str]] = []
    candidates.extend(_structural_spans(text))
    for term in terms or []:
        if isinstance(term,str) and term:
            for match in re.finditer(re.escape(term),text):
                candidates.append((match.start(),match.end(),5,term))
    for priority, pattern in enumerate(_PROTECTION_PATTERNS):
        for match in pattern.finditer(text):
            candidates.append((match.start(), match.end(), priority + 10, match.group(0)))
    candidates.sort(key=lambda item: (item[0], item[2], -(item[1] - item[0])))

    selected: list[tuple[int, int, str]] = []
    cursor = 0
    index = 0
    while index < len(candidates):
        start = candidates[index][0]
        if start < cursor:
            index += 1
            continue
        same_start: list[tuple[int, int, int, str]] = []
        while index < len(candidates) and candidates[index][0] == start:
            same_start.append(candidates[index])
            index += 1
        eligible = [candidate for candidate in same_start if candidate[1] > cursor]
        if not eligible:
            continue
        _, end, _, value = min(eligible, key=lambda item: (item[2], -(item[1] - item[0])))
        selected.append((start, end, value))
        cursor = end
    return selected


def _protected_fragments(text: str) -> list[str]:
    """Return protected values in source order."""

    return [value for _, _, value in _protected_spans(text)]


def _addition_check_fragments(text: str) -> list[tuple[str, str]]:
    """Extract protected additions while excluding gene-like acronym guesses."""

    fragments: list[tuple[str, str]] = []
    for _, _, priority, value in _structural_spans(text):
        fragments.append((f"structure:{priority}", value))

    # These patterns cover math/formulas, references and identifiers, statistical
    # values, ranges, and standalone numbers. Gene-like symbol patterns are left
    # out so a newly introduced all-caps abbreviation is not treated as a fact.
    # Include statistical symbols and TeX commands while excluding the two
    # intentionally permissive gene-like acronym patterns.
    checked_pattern_indexes = (*range(0, 16), len(_PROTECTION_PATTERNS) - 1)
    for index in checked_pattern_indexes:
        for match in _PROTECTION_PATTERNS[index].finditer(text):
            fragments.append((f"pattern:{index}", match.group(0)))
    return fragments


@dataclass(frozen=True, slots=True)
class ProtectedText:
    """Masked source text and the exact values needed to restore it."""

    text: str
    _entries: tuple[tuple[str, str], ...] = field(repr=False)

    @property
    def placeholders(self) -> tuple[str, ...]:
        """The per-occurrence placeholder tokens inserted into ``text``."""

        return tuple(token for token, _ in self._entries)

    def restore(self, output: str) -> str:
        """Restore every protected occurrence, rejecting missing or altered tokens."""

        if not isinstance(output, str):
            raise TypeError("output must be a string")
        expected = set(self.placeholders)
        found = Counter(_PLACEHOLDER_RE.findall(output))
        for token in expected:
            if found[token] != 1:
                raise ValidationError(
                    f"Protected placeholder {token} must occur exactly once (found {found[token]})",
                    code="PROTECTED_PLACEHOLDER_MISSING",
                )
        unknown = set(found) - expected
        if unknown:
            raise ValidationError(
                "Output contains an unknown protected placeholder",
                code="PROTECTED_PLACEHOLDER_UNKNOWN",
            )

        restored = output
        for token, original in self._entries:
            restored = restored.replace(token, original, 1)
        if _PLACEHOLDER_RE.search(restored):
            raise ValidationError(
                "Output contains an unresolved protected placeholder",
                code="PROTECTED_PLACEHOLDER_UNRESOLVED",
            )
        return restored


def protect(text: str, terms=None) -> ProtectedText:
    """Mask numbers, statistics, identifiers, citations, gene-like symbols, and math.

    Protection is conservative and deterministic. It preserves each protected
    occurrence independently, including repeated identical values.
    """

    if not isinstance(text, str):
        raise TypeError("text must be a string")

    spans = _protected_spans(text,terms)
    if not spans:
        return ProtectedText(text=text, _entries=())

    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    salt = 0
    while f"⟦WTN-{digest}-" in text:
        salt += 1
        digest = hashlib.sha256(f"{text}\0{salt}".encode("utf-8")).hexdigest()[:12]

    entries: list[tuple[str, str]] = []
    pieces: list[str] = []
    cursor = 0
    for fragment_index, (start, end, value) in enumerate(spans):
        pieces.append(text[cursor:start])
        token = f"⟦WTN-{digest}-{fragment_index:04d}⟧"
        pieces.append(token)
        entries.append((token, value))
        cursor = end
    pieces.append(text[cursor:])
    return ProtectedText(text="".join(pieces), _entries=tuple(entries))
