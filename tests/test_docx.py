from __future__ import annotations

from pathlib import Path

import pytest
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt

from writune.files import Workspace, apply_result


def _workspace(tmp_path: Path) -> Workspace:
    task_root = tmp_path / "tasks"
    task_root.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    return Workspace(allowed_roots=[project], task_root=task_root)


def _save(document: Document, path: Path) -> None:
    document.save(path)


def _all_paragraph_text(document: Document) -> list[str]:
    text = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                text.extend(paragraph.text for paragraph in cell.paragraphs)
    for section in document.sections:
        text.extend(paragraph.text for paragraph in section.header.paragraphs)
        text.extend(paragraph.text for paragraph in section.footer.paragraphs)
    return text


def test_docx_apply_replaces_exclusive_paragraph_and_preserves_style(tmp_path: Path) -> None:
    import base64
    from io import BytesIO
    from zipfile import ZipFile
    workspace = _workspace(tmp_path)
    target = workspace.allowed_roots[0] / "paper.docx"
    result = workspace.task_root / "result.txt"
    document = Document()
    heading = document.add_paragraph("Introduction")
    heading.style = "Heading 1"
    placeholder = document.add_paragraph()
    placeholder.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = placeholder.add_run("{{WRITUNE:ABSTRACT}}")
    run.bold = True
    run.font.size = Pt(14)
    table = document.add_table(rows=1, cols=1)
    table.cell(0, 0).text = "Unchanged table content"
    document.sections[0].header.paragraphs[0].text = "Header stays"
    document.sections[0].footer.paragraphs[0].text = "Footer stays"
    image=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5oEAAAAASUVORK5CYII=')
    document.add_picture(BytesIO(image))
    _save(document, target)
    result.write_text("A polished abstract", encoding="utf-8")

    applied = apply_result(result, target, "ABSTRACT", workspace=workspace)
    output = Document(applied["output_path"])

    assert output.paragraphs[1].text == "A polished abstract"
    assert output.paragraphs[1].alignment == WD_ALIGN_PARAGRAPH.CENTER
    assert output.paragraphs[1].runs[0].bold is True
    assert output.paragraphs[1].runs[0].font.size == Pt(14)
    assert output.tables[0].cell(0, 0).text == "Unchanged table content"
    assert output.sections[0].header.paragraphs[0].text == "Header stays"
    assert output.sections[0].footer.paragraphs[0].text == "Footer stays"
    assert "{{WRITUNE:ABSTRACT}}" not in " ".join(_all_paragraph_text(output))
    assert len(output.inline_shapes)==1
    with ZipFile(target) as before, ZipFile(applied['output_path']) as after:
        media=[name for name in before.namelist() if name.startswith('word/media/')]
        assert media
        assert all(before.read(name)==after.read(name) for name in media)


@pytest.mark.parametrize("location", ["table", "header", "footer"])
def test_docx_apply_finds_placeholder_in_tables_and_headers_footers(
    tmp_path: Path, location: str
) -> None:
    workspace = _workspace(tmp_path)
    target = workspace.allowed_roots[0] / f"{location}.docx"
    result = workspace.task_root / "result.txt"
    document = Document()
    if location == "table":
        table = document.add_table(rows=1, cols=1)
        table.cell(0, 0).paragraphs[0].text = "{{WRITUNE:ONE}}"
    elif location == "header":
        document.sections[0].header.paragraphs[0].text = "{{WRITUNE:ONE}}"
    else:
        document.sections[0].footer.paragraphs[0].text = "{{WRITUNE:ONE}}"
    _save(document, target)
    result.write_text("replacement", encoding="utf-8")

    applied = apply_result(result, target, "ONE", workspace=workspace)
    output = Document(applied["output_path"])
    assert "replacement" in _all_paragraph_text(output)


def test_docx_rejects_nonexclusive_duplicate_and_unsupported_placeholders(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    result = workspace.task_root / "result.txt"
    result.write_text("replacement", encoding="utf-8")

    target = workspace.allowed_roots[0] / "embedded.docx"
    document = Document()
    document.add_paragraph("before {{WRITUNE:X}} after")
    _save(document, target)
    with pytest.raises(ValueError, match="only text"):
        apply_result(result, target, "X", workspace=workspace)

    target = workspace.allowed_roots[0] / "duplicate.docx"
    document = Document()
    document.add_paragraph("{{WRITUNE:X}}")
    document.add_paragraph("{{WRITUNE:X}}")
    _save(document, target)
    with pytest.raises(ValueError, match="found 2"):
        apply_result(result, target, "X", workspace=workspace)


def test_docx_apply_is_idempotent_and_preserves_target_by_default(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    target = workspace.allowed_roots[0] / "draft.docx"
    result = workspace.task_root / "result.txt"
    document = Document()
    document.add_paragraph("{{WRITUNE:X}}")
    _save(document, target)
    original = target.read_bytes()
    result.write_text("replacement", encoding="utf-8")

    first = apply_result(result, target, "X", workspace=workspace)
    second = apply_result(result, target, "X", workspace=workspace)
    assert first["idempotent"] is False
    assert second["idempotent"] is True
    assert target.read_bytes() == original
    assert Document(first["output_path"]).paragraphs[0].text == "replacement"
