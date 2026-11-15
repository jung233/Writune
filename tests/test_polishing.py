from __future__ import annotations

import pytest

from writune.polishing import (
    SCENES,
    STYLES,
    ValidationError,
    build_prompt,
    protect,
    segment_markdown,
    validate,
)


def test_build_prompt_keeps_variable_paragraph_out_of_stable_prefix() -> None:
    first = build_prompt("First paragraph.", context={"topic": "A", "section": "Results"})
    second = build_prompt("Second paragraph.", context={"section": "Results", "topic": "A"})

    assert first.prefix == second.prefix
    assert first.input.endswith("First paragraph.")
    assert second.input.endswith("Second paragraph.")
    assert "[CURRENT PARAGRAPH]" not in first.prefix
    assert '"section":"Results"' in first.prefix


@pytest.mark.parametrize("scene", list(SCENES))
@pytest.mark.parametrize("style", list(STYLES))
def test_all_builtin_scenes_and_styles_build(scene: str, style: str) -> None:
    prompt = build_prompt("Text.", scene=scene, style=style)
    assert prompt.prefix
    assert prompt.input == "[CURRENT PARAGRAPH]\nText."


@pytest.mark.parametrize(
    ("section", "expected"),
    [
        ("Results", "center the wording on reported observations"),
        ("Discussion", "retain the inferential status"),
        ("Methods", "needed for reproducibility"),
        ("Abstract", "include no result or conclusion"),
    ],
)
def test_sci_paper_section_constraints(section: str, expected: str) -> None:
    prompt = build_prompt("Text.", scene="sci_paper", context={"section": section})
    assert expected in prompt.prefix


def test_custom_scene_style_and_global_templates() -> None:
    prompt = build_prompt(
        "Text.",
        scene="lab_note",
        style="plainspoken",
        custom_templates={
            "scenes": {"lab_note": "Retain the lab's concise note style."},
            "styles": {"plainspoken": "Use everyday wording."},
            "global": "Do not change the author's first-person voice.",
        },
    )
    assert "Retain the lab's concise note style." in prompt.prefix
    assert "Use everyday wording." in prompt.prefix
    assert "Do not change the author's first-person voice." in prompt.prefix


def test_protect_masks_and_restores_sensitive_occurrences() -> None:
    source = (
        "GHRH was associated with TP53 and p53 (p = 0.03; 95% CI: 1.2–2.1) in 42 samples [3, 4]. "
        "The model was $y = \\beta_0 + \\beta_1x$; see \\cite{smith2024}."
    )
    protected = protect(source)

    assert protected.text != source
    assert "GHRH" not in protected.text
    assert "p53" not in protected.text
    assert "0.03" not in protected.text
    assert "\\cite{smith2024}" not in protected.text
    assert protected.restore(protected.text) == source


def test_user_placeholder_is_masked_whole_and_surrounding_prose_can_change() -> None:
    source = "Please polish this sentence. {{AUTHOR_NAME}} remains unchanged."
    protected = protect(source)

    assert "{{AUTHOR_NAME}}" not in protected.text
    assert "Please polish this sentence." in protected.text
    assert protected.restore(protected.text.replace("polish", "refine")) == (
        "Please refine this sentence. {{AUTHOR_NAME}} remains unchanged."
    )
    with pytest.raises(ValidationError) as error:
        validate(source, source.replace("{{AUTHOR_NAME}}", "Dr. Lee"))
    assert error.value.code == "PROTECTED_CONTENT_CHANGED"


def test_dollar_brace_placeholder_is_masked_without_masking_neighboring_prose() -> None:
    source = "Use ${NAME} exactly; the surrounding sentence should remain editable."
    protected = protect(source)

    assert "${NAME}" not in protected.text
    assert "the surrounding sentence should remain editable." in protected.text
    assert protected.restore(protected.text) == source
    validate(source, source.replace("surrounding sentence", "nearby sentence"))
    with pytest.raises(ValidationError) as error:
        validate(source, source.replace("${NAME}", "customer"))
    assert error.value.code == "PROTECTED_CONTENT_CHANGED"


def test_markdown_fenced_block_is_one_masked_span_and_validator_protects_it() -> None:
    source = (
        "The code example is easy to follow.\n\n"
        "```python\nvalue = 7\nprint(value)\n```\n\n"
        "The surrounding prose can be polished."
    )
    protected = protect(source)

    assert "```python" not in protected.text
    assert "value = 7" not in protected.text
    assert protected.restore(protected.text) == source
    validate(source, source.replace("easy to follow", "straightforward"))
    with pytest.raises(ValidationError) as error:
        validate(source, source.replace("value = 7", "value = 8"))
    assert error.value.code == "PROTECTED_CONTENT_CHANGED"


def test_markdown_inline_code_span_is_protected_without_masking_prose() -> None:
    source = "Use `render({width: 320})` in this sentence."
    protected = protect(source)

    assert "`render({width: 320})`" not in protected.text
    assert "Use " in protected.text and " in this sentence." in protected.text
    validate(source, "Apply `render({width: 320})` in this sentence.")
    with pytest.raises(ValidationError):
        validate(source, "Use `render({width: 640})` in this sentence.")


def test_latex_balanced_commands_protect_nested_required_and_optional_arguments() -> None:
    source = (
        r"The paragraph is clear. \includegraphics[width={0.5\textwidth}]"
        r"{figures/plot_{A}.pdf} follows. \section{Main {nested} title} and "
        r"\subsection{Escaped \{braces\}} plus \cite[see \textit{also}]{smith{2024}} remain."
    )
    protected = protect(source)

    assert r"\includegraphics" not in protected.text
    assert "figures/plot_{A}.pdf" not in protected.text
    assert "Main {nested} title" not in protected.text
    assert r"Escaped \{braces\}" not in protected.text
    assert r"\cite[see" not in protected.text
    assert protected.restore(protected.text) == source

    validate(source, source.replace("The paragraph is clear", "The paragraph reads clearly"))
    for damaged in (
        source.replace("plot_{A}.pdf", "plot_{B}.pdf"),
        source.replace("Main {nested} title", "Main nested title"),
        source.replace(r"\{braces\}", "braces"),
        source.replace("smith{2024}", "smith2024"),
    ):
        with pytest.raises(ValidationError) as error:
            validate(source, damaged)
        assert error.value.code == "PROTECTED_CONTENT_CHANGED"


def test_latex_math_environments_include_extended_and_nested_forms() -> None:
    source = (
        r"The discussion continues. \begin{alignat}{2}"
        r"x &= y + z \\ \begin{cases}a, & x > 0 \\ b, & x \leq 0"
        r"\end{cases} \end{alignat} The prose resumes."
    )
    protected = protect(source)

    assert r"\begin{alignat}" not in protected.text
    assert r"\begin{cases}" not in protected.text
    assert protected.restore(protected.text) == source
    validate(source, source.replace("discussion continues", "discussion proceeds"))
    with pytest.raises(ValidationError) as error:
        validate(source, source.replace("x &= y + z", "x &= y - z"))
    assert error.value.code == "PROTECTED_CONTENT_CHANGED"


@pytest.mark.parametrize("environment", ["split", "multline", "flalign", "eqnarray"])
def test_additional_latex_math_environments_are_masked_whole(environment: str) -> None:
    source = f"The text continues. \\begin{{{environment}}}x = 1\\end{{{environment}}} More prose."
    protected = protect(source)

    assert f"\\begin{{{environment}}}" not in protected.text
    assert protected.restore(protected.text) == source
    with pytest.raises(ValidationError) as error:
        validate(source, source.replace("x = 1", "x = 2"))
    assert error.value.code == "PROTECTED_CONTENT_CHANGED"


def test_restore_requires_every_occurrence_exactly_once() -> None:
    protected = protect("The result was 7 and 7.")
    assert len(protected.placeholders) == 2
    with pytest.raises(ValidationError, match="exactly once"):
        protected.restore(protected.text.replace(protected.placeholders[0], ""))
    with pytest.raises(ValidationError, match="exactly once"):
        protected.restore(protected.text + protected.placeholders[0])


def test_validate_accepts_preserved_values_and_rejects_changed_values() -> None:
    original = "TP53 expression increased by 20% (p < 0.01) [2]."
    validate(original, "Expression of TP53 rose by 20% (p < 0.01) [2].")
    with pytest.raises(ValidationError) as error:
        validate(original, "Expression of TP53 rose by 25% (p < 0.01) [2].")
    assert error.value.code == "PROTECTED_CONTENT_CHANGED"


def test_narrative_author_year_citation_is_protected_as_a_complete_reference() -> None:
    source = (
        "The observed pattern is consistent with prior work. "
        "Smith et al. (2020) reported a related association."
    )
    protected = protect(source)

    assert "Smith et al. (2020)" not in protected.text
    assert "The observed pattern is consistent with prior work." in protected.text
    assert protected.restore(protected.text) == source
    validate(source, source.replace("observed pattern", "measured trend"))
    with pytest.raises(ValidationError) as error:
        validate(source, source.replace("Smith", "Jones"))
    assert error.value.code == "PROTECTED_CONTENT_CHANGED"


def test_independent_statistical_symbols_are_protected_without_masking_prose() -> None:
    source = "The threshold was ≤ 0.05; the estimate was 2.1 ± 0.3 with χ² above 4."
    protected = protect(source)

    for symbol in ("≤", "±", "χ²"):
        assert symbol not in protected.text
    assert "The threshold was " in protected.text
    assert "the estimate was " in protected.text
    assert protected.restore(protected.text) == source
    validate(source, source.replace("The threshold", "This threshold"))
    for changed in (source.replace("≤", "≥"), source.replace("±", "+"), source.replace("χ²", "χ")):
        with pytest.raises(ValidationError) as error:
            validate(source, changed)
        assert error.value.code == "PROTECTED_CONTENT_CHANGED"


@pytest.mark.parametrize(
    "output",
    [
        "The result was 3 and 4.",
        "The result was 3 [4].",
        "The result was 3 with formula $x^2$.",
        "The result was 3 {{NEW_VALUE}}.",
    ],
)
def test_validate_rejects_added_protected_content(output: str) -> None:
    with pytest.raises(ValidationError) as error:
        validate("The result was 3.", output)
    assert error.value.code == "PROTECTED_CONTENT_ADDED"


def test_validate_does_not_reject_new_prose_acronym_only() -> None:
    validate("The model fit was good.", "The model fit was good for BMI.")


@pytest.mark.parametrize(
    "output",
    [
        "",
        "Here is the polished version: The result increased.",
        "I cannot help with that request.",
        "The result increased... [truncated]",
        "The result increased because",
    ],
)
def test_validate_rejects_empty_preamble_refusal_and_truncation(output: str) -> None:
    with pytest.raises(ValidationError):
        validate("The result increased.", output)


def test_validate_rejects_abnormal_lengths_and_unclosed_fences() -> None:
    source = "A" * 160
    with pytest.raises(ValidationError) as short_error:
        validate(source, "A short result.")
    assert short_error.value.code == "OUTPUT_TOO_SHORT"

    with pytest.raises(ValidationError) as long_error:
        validate("Short source.", "word " * 100)
    assert long_error.value.code == "OUTPUT_TOO_LONG"

    with pytest.raises(ValidationError) as fence_error:
        validate("Code sample.", "```python\nprint('ok')")
    assert fence_error.value.code == "TRUNCATED_OUTPUT"


def test_segment_markdown_preserves_paragraph_order_and_fences() -> None:
    source = (
        "# Heading\n\nFirst paragraph.\nwith a wrapped line.\n\n"
        "```python\nprint('unchanged')\n```\n\nLast paragraph.\n"
    )
    parts = segment_markdown(source)

    assert "".join(parts) == source
    assert len(parts) == 4
    assert parts[0] == "# Heading\n\n"
    assert parts[1] == "First paragraph.\nwith a wrapped line.\n\n"
    assert parts[2] == "```python\nprint('unchanged')\n```\n\n"
    assert parts[3] == "Last paragraph.\n"


def test_segment_markdown_handles_tilde_fence_crlf_and_unclosed_fence() -> None:
    source = "Intro.\r\n\r\n~~~text\r\na\r\nb\r\n~~~\r\nTail."
    parts = segment_markdown(source)
    assert "".join(parts) == source
    assert len(parts) == 3
    assert parts[1] == "~~~text\r\na\r\nb\r\n~~~\r\n"

    unclosed = "Text.\n\n```\ncode\nstill code"
    unclosed_parts = segment_markdown(unclosed)
    assert "".join(unclosed_parts) == unclosed
    assert len(unclosed_parts) == 2
