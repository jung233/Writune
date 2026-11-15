"""Structural contract checks for the shipped skill documentation.

These checks validate that the requested workflows and caveats are present in
the local skill bundle. They do not connect to Codex, Claude Code, OpenCode, or
an MCP server, and do not prove that any client discovers or invokes the skill.
"""

from pathlib import Path
import re

import pytest


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / ".agents" / "skills" / "writune-polish"


def _text(relative_path: str) -> str:
    return (SKILL_ROOT / relative_path).read_text(encoding="utf-8")


SCENARIOS = [
    (
        "english_sci_results",
        "references/style-profiles.md",
        (
            r"sci[_ -]?paper",
            r"\bResults\b",
            r"observations?",
            r"causal|mechanistic",
            r"avoid|do not add|without adding",
        ),
    ),
    (
        "chinese_journal",
        "references/style-profiles.md",
        (
            r"chinese_journal",
            r"clear.{0,30}Chinese|disciplined Chinese",
            r"formulaic|template[- ]like",
            r"preserv\w*.{0,30}(author|claim)",
            r"phrase blacklist|blacklist",
        ),
    ),
    (
        "course_essay",
        "references/style-profiles.md",
        (
            r"school_essay",
            r"Course essays",
            r"writer\W+s ideas|student\W+s ideas|writer\W+s voice",
            r"different argument|change.{0,20}argument",
        ),
    ),
    (
        "markdown_batch_of_twenty",
        "references/workflows.md",
        (
            r"writune_submit_batch",
            r"markdown_path",
            r"concurrenc\w*.{0,80}(limit|cap|simultaneous)|(?:limit|cap).{0,80}concurrenc\w*",
            r"original order|ordered items|task order",
        ),
    ),
    (
        "docx_placeholder",
        "references/file-handoff.md",
        (
            r"exclusive paragraph placeholder|placeholder.{0,50}own paragraph",
            r"table",
            r"header",
            r"footer",
            r"non[- ]text|unsupported package parts",
        ),
    ),
    (
        "first_token_timeout_then_secondary",
        "references/error-handling.md",
        (
            r"first[- ]text[- ]token timeout|first[- ]token timeout",
            r"at most two.{0,30}attempt|two upstream attempts",
            r"secondary model",
            r"non[- ]retryable.{0,40}(stop|sooner)",
        ),
    ),
    (
        "all_attempts_fail_to_original",
        "references/error-handling.md",
        (
            r"original_fallback",
            r"not (?:successfully )?polished|does not mean.{0,30}polished",
            r"preserv\w*.{0,30}source|original result",
            r"concise error codes|error codes",
        ),
    ),
    (
        "continue_independent_work",
        "references/workflows.md",
        (
            r"independent work.{0,60}(?:runs|running)|while it runs.{0,60}independent work",
            r"do not poll repeatedly|avoid.{0,30}poll(?:ing)? repeatedly|not repeatedly poll",
            r"external wake[- ]up|automatically resume.{0,40}conversation|resume.{0,40}external",
        ),
    ),
    (
        "direct_file_delivery",
        "references/file-handoff.md",
        (
            r"result_path",
            r"writune_apply",
            r"writune_export",
            r"do not.{0,30}(?:feed|return|send).{0,50}(?:full result|polished text).{0,30}(?:model|chat)",
        ),
    ),
    (
        "mcp_unavailable",
        "references/error-handling.md",
        (
            r"MCP unavailable|tools are missing|tools are unavailable",
            r"do not claim.{0,50}(?:submitted|polished)|do not say.{0,50}(?:submitted|polished)",
            r"preserv\w*.{0,30}(?:original )?draft",
            r"could not be reached|unavailable server|not available",
        ),
    ),
]


@pytest.mark.parametrize(
    ("scenario", "relative_path", "required_phrases"),
    SCENARIOS,
    ids=[scenario[0] for scenario in SCENARIOS],
)
def test_skill_scenario_contract(scenario, relative_path, required_phrases):
    text = _text(relative_path) + "\n" + _text("SKILL.md")
    missing = [pattern for pattern in required_phrases if not re.search(pattern, text, re.IGNORECASE | re.DOTALL)]
    assert not missing, f"{scenario} is missing documented concepts: {missing}"

    skill = _text("SKILL.md")
    for reference in re.findall(r"\]\((references/[^)]+\.md)\)", skill):
        assert (SKILL_ROOT / reference).is_file(), f"missing linked skill reference: {reference}"
