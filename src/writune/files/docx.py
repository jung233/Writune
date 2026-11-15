"""Safe replacement of a single exclusive paragraph in a DOCX document."""

from __future__ import annotations

from copy import deepcopy
from io import BytesIO
from zipfile import BadZipFile, ZipFile
import xml.etree.ElementTree as ET

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph


def _iter_block_items(parent):
    element = getattr(parent, "_element", None)
    if element is None:
        element = getattr(parent, "element", None)
    if element is None:
        return
    for child in element.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, parent)
        elif child.tag == qn("w:tbl"):
            yield Table(child, parent)


def _paragraphs_in_table(table):
    for row in table.rows:
        for cell in row.cells:
            for item in _iter_block_items(cell):
                if isinstance(item, Paragraph):
                    yield item
                else:
                    yield from _paragraphs_in_table(item)


def _supported_paragraphs(document):
    seen_elements: set[int] = set()
    seen_paragraphs: set[int] = set()

    def visit_container(container):
        element = getattr(container, "_element", None)
        if element is None:
            element = getattr(container, "element", None)
        if element is None or id(element) in seen_elements:
            return
        seen_elements.add(id(element))
        for item in _iter_block_items(container):
            if isinstance(item, Paragraph):
                if id(item._p) not in seen_paragraphs:
                    seen_paragraphs.add(id(item._p))
                    yield item
            else:
                for paragraph in _paragraphs_in_table(item):
                    if id(paragraph._p) not in seen_paragraphs:
                        seen_paragraphs.add(id(paragraph._p))
                        yield paragraph

    # Document._element is the package root, whose direct child is ``w:body``;
    # paragraph/table blocks live inside the body element.
    yield from visit_container(document._body)
    for section in document.sections:
        for name in (
            "header",
            "first_page_header",
            "even_page_header",
            "footer",
            "first_page_footer",
            "even_page_footer",
        ):
            yield from visit_container(getattr(section, name))


def _archive_marker_occurrences(data: bytes, marker: str) -> int:
    """Count marker text in every Word paragraph in every XML package part."""
    paragraph_tag = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"
    text_tag = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"
    total = 0
    try:
        with ZipFile(BytesIO(data)) as archive:
            for name in archive.namelist():
                if not name.lower().endswith(".xml"):
                    continue
                root = ET.fromstring(archive.read(name))
                for paragraph in root.iter(paragraph_tag):
                    text = "".join(node.text or "" for node in paragraph.iter(text_tag))
                    total += text.count(marker)
    except (BadZipFile, ET.ParseError, OSError) as exc:
        raise ValueError("target DOCX contains an unreadable XML package part") from exc
    return total


def _has_only_text_runs(paragraph_element) -> bool:
    """Reject drawing, field, hyperlink, or other XML inside the target para."""
    allowed_p = {qn("w:pPr"), qn("w:r")}
    if any(child.tag not in allowed_p for child in paragraph_element):
        return False
    for run in paragraph_element.iter(qn("w:r")):
        if any(child.tag not in {qn("w:rPr"), qn("w:t")} for child in run):
            return False
    return True


def _replace_paragraph(paragraph: Paragraph, replacement: str) -> None:
    element = paragraph._p
    if not _has_only_text_runs(element):
        raise ValueError("DOCX placeholder paragraph contains unsupported content")
    runs = list(paragraph.runs)
    first_properties = deepcopy(runs[0]._r.rPr) if runs and runs[0]._r.rPr is not None else None
    for run in runs:
        element.remove(run._r)
    if replacement:
        new_run = paragraph.add_run(replacement)
        if first_properties is not None:
            new_run._r.insert(0, first_properties)


def replace_docx_placeholder(data: bytes, placeholder: str, replacement: str) -> bytes:
    """Return a validated DOCX with one exclusive placeholder paragraph replaced."""
    try:
        document = Document(BytesIO(data))
    except Exception as exc:
        raise ValueError("target is not a readable DOCX document") from exc

    supported = list(_supported_paragraphs(document))
    matches: list[Paragraph] = []
    supported_occurrences = 0
    for paragraph in supported:
        text = paragraph.text
        occurrences = text.count(placeholder)
        if occurrences == 0:
            continue
        supported_occurrences += occurrences
        if text != placeholder or occurrences != 1:
            raise ValueError("DOCX placeholder must be the only text in its paragraph")
        matches.append(paragraph)

    archive_occurrences = _archive_marker_occurrences(data, placeholder)
    if archive_occurrences != supported_occurrences:
        raise ValueError("DOCX placeholder is inside an unsupported document structure")
    if archive_occurrences != 1 or len(matches) != 1:
        raise ValueError(f"expected exactly one DOCX placeholder paragraph; found {archive_occurrences}")
    _replace_paragraph(matches[0], replacement)

    output = BytesIO()
    document.save(output)
    result = output.getvalue()
    # Re-open the generated archive before it is allowed to reach disk.
    try:
        checked = Document(BytesIO(result))
    except Exception as exc:
        raise ValueError("generated DOCX failed validation") from exc
    if _archive_marker_occurrences(result, placeholder):
        raise ValueError("generated DOCX still contains the placeholder")
    return result
