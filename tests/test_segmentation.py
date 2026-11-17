from __future__ import annotations

import json
from pathlib import Path

import pytest

from writune.polishing.segmentation import segment_markdown
from writune.service import Service
from tests.mock_responses import make_settings


def test_section_mode_splits_atx_and_multiline_setext_headings_exactly():
    source = (
        "Preamble stays separate.\r\n\r\n"
        "# First heading\r\nFirst section text.\r\n\r\n"
        "Setext title\r\ncontinued title\r\n---\r\nSetext body.\r\n\r\n"
        "### Third heading\r\nFinal text.\r\n"
    )

    parts = segment_markdown(source, segmentation="section")

    assert parts == [
        "Preamble stays separate.\r\n\r\n",
        "# First heading\r\nFirst section text.\r\n\r\n",
        "Setext title\r\ncontinued title\r\n---\r\nSetext body.\r\n\r\n",
        "### Third heading\r\nFinal text.\r\n",
    ]
    assert "".join(parts) == source


def test_section_mode_does_not_split_headings_inside_fenced_code():
    source = (
        "~~~markdown\n"
        "# Example heading\n"
        "Example title\n"
        "---\n"
        "~~~\n\n"
        "## Actual heading\nBody.\n"
    )

    parts = segment_markdown(source, segmentation="section")

    assert parts == ["~~~markdown\n# Example heading\nExample title\n---\n~~~\n\n", "## Actual heading\nBody.\n"]
    assert "".join(parts) == source


def test_section_mode_keeps_blank_prefix_with_first_heading_and_no_heading_as_one_unit():
    source = "\r\n\r\n  ## Heading\r\nText\r\n"
    assert segment_markdown(source, segmentation="section") == [source]

    plain = "No heading here.\n\nJust paragraphs.\n"
    assert segment_markdown(plain, segmentation="section") == [plain]
    assert segment_markdown("````\n# fenced\n", segmentation="section") == ["````\n# fenced\n"]


def test_document_mode_returns_complete_input_and_paragraph_mode_stays_default():
    source = "# Heading\n\nFirst paragraph.\n\nSecond paragraph.\n"
    assert segment_markdown(source, segmentation="document") == [source]
    assert segment_markdown(source) == segment_markdown(source, segmentation="paragraph")
    assert "".join(segment_markdown(source)) == source


@pytest.mark.parametrize("segmentation", ["unknown", "heading"])
def test_segment_markdown_rejects_unknown_segmentation(segmentation):
    with pytest.raises(ValueError, match="segmentation must be"):
        segment_markdown("Text.", segmentation=segmentation)


def _submitted_texts(service: Service, batch: dict) -> list[str]:
    return [Path(service.db.get(task_id)["original_path"]).read_bytes().decode("utf-8") for task_id in batch["task_ids"]]


def test_service_batch_selects_markdown_segmentation_and_rejects_other_modes(tmp_path):
    service = Service(make_settings(tmp_path, "http://127.0.0.1:9/v1"), start_worker=False)
    source = service.settings.allowed_workspaces[0] / "draft.md"
    source.write_bytes(b"Preamble.\n\n# One\nFirst.\n\n## Two\nSecond.\n")

    section_batch = service.submit_batch(markdown_path=source, segmentation="section")
    assert _submitted_texts(service, section_batch) == [
        "Preamble.\n\n",
        "# One\nFirst.\n\n",
        "## Two\nSecond.\n",
    ]

    document_batch = service.submit_batch(markdown_path=source, segmentation="document")
    assert _submitted_texts(service, document_batch) == [source.read_bytes().decode("utf-8")]

    with pytest.raises(ValueError, match="only with markdown_path"):
        service.submit_batch(items=[{"text": "A paragraph."}], segmentation="document")
    with pytest.raises(ValueError, match="only with markdown_path"):
        service.submit_batch(source_paths=[source], segmentation="section")


def test_cli_exposes_segmentation_for_markdown_batches(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from writune.cli import app

    service = Service(make_settings(tmp_path, "http://127.0.0.1:9/v1"), start_worker=False)
    source = service.settings.allowed_workspaces[0] / "draft.md"
    source.write_bytes(b"# One\nFirst.\n\n# Two\nSecond.\n")
    monkeypatch.setattr("writune.cli._service", lambda *args, **kwargs: service)

    result = CliRunner().invoke(app, ["submit-batch", "--markdown-path", str(source), "--segmentation", "document"])

    assert result.exit_code == 0, result.output
    batch = json.loads(result.output)
    assert len(batch["task_ids"]) == 1
    assert _submitted_texts(service, batch) == [source.read_bytes().decode("utf-8")]
