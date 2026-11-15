from __future__ import annotations

from writune.polishing import build_prompt


def test_context_json_is_deterministic_and_compact() -> None:
    left = build_prompt(
        "one",
        scene="sci_paper",
        context={"topic": "Neurons", "constraints": ["Preserve genes", "No new claims"]},
    )
    right = build_prompt(
        "two",
        scene="sci_paper",
        context={"constraints": ["Preserve genes", "No new claims"], "topic": "Neurons"},
    )

    assert left.prefix == right.prefix
    assert '{"constraints":["Preserve genes","No new claims"],"topic":"Neurons"}' in left.prefix
    assert "\n  \"topic\"" not in left.prefix


def test_prefix_changes_only_when_stable_context_changes() -> None:
    baseline = build_prompt("Paragraph A", context={"document": "study-1"})
    another_paragraph = build_prompt("Paragraph B", context={"document": "study-1"})
    another_context = build_prompt("Paragraph A", context={"document": "study-2"})

    assert baseline.prefix == another_paragraph.prefix
    assert baseline.prefix != another_context.prefix
    assert baseline.input != another_paragraph.input


def test_optional_local_context_is_variable_input_after_stable_prefix() -> None:
    first = build_prompt(
        "A",
        context={"topic": "Neurons", "audience": "Biomedical researchers"},
        local_context="Prior paragraph: methods.",
    )
    second = build_prompt(
        "B",
        context={"audience": "Biomedical researchers", "topic": "Neurons"},
        local_context="Next paragraph: results.",
    )

    assert first.prefix == second.prefix
    assert '"audience":"Biomedical researchers"' in first.prefix
    assert "[OPTIONAL LOCAL CONTEXT]\nPrior paragraph: methods." in first.input
    assert "[CURRENT PARAGRAPH]\nA" in first.input
    assert first.input.index("[OPTIONAL LOCAL CONTEXT]") < first.input.index("[CURRENT PARAGRAPH]")
    assert "Next paragraph: results." in second.input
    assert "Prior paragraph: methods." not in first.prefix
