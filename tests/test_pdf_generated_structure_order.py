"""Saved structure must describe source content once, in source order."""

from pathlib import Path

import fitz
import pikepdf
import pytest

from src.education.pdf_processor import PDFProcessor
from src.education.remediation.base import RemediationConfig
from src.education.remediation.pdf_remediator import PdfRemediator


def _elements(node):
    if not isinstance(node, pikepdf.Dictionary):
        return
    if "/S" in node:
        yield node
    kids = node.get("/K", [])
    if not isinstance(kids, (pikepdf.Array, list)):
        kids = [kids]
    for kid in kids:
        yield from _elements(kid)


def _page_of(element):
    while "/Pg" not in element:
        element = element.P
    return element.Pg.objgen


def test_saved_syllabus_structure_is_source_backed_and_ordered(tmp_path):
    source = Path(__file__).parent / "fixtures/pdfs/simple_syllabus.pdf"
    scan = PDFProcessor(generate_alt_text=False, validate_alt_text=False).process_pdf(
        str(source)
    )
    result = PdfRemediator(
        str(source),
        scan.issues,
        RemediationConfig(
            use_ai=False,
            verify_fixes=True,
            create_backup=False,
            output_directory=str(tmp_path),
            allow_legacy_nested_ai=False,
        ),
    ).remediate()
    try:
        assert result.output_file
        with fitz.open(source) as original, pikepdf.open(result.output_file) as saved:
            elements = list(_elements(saved.Root.StructTreeRoot))
            for page_index, page in enumerate(saved.pages):
                texts = [
                    str(e.ActualText)
                    for e in elements
                    if "/ActualText" in e and _page_of(e) == page.obj.objgen
                ]
                assert "(anonymous)" not in texts
                # Each occurrence must consume distinct source text, including
                # repeated phrases. Operator runs can omit inter-line spaces.
                source_text = "".join(original[page_index].get_text().split())
                cursor = 0
                for text in texts:
                    normalized = "".join(text.split())
                    position = source_text.find(normalized, cursor)
                    assert position >= cursor, f"Unbound or reordered text: {text}"
                    cursor = position + len(normalized)
            bodies = [e for e in elements if str(e.S) == "/LBody"]
            assert len(bodies) >= 4
            for body in bodies:
                assert "/K" in body, "List body has no content binding"
            assert all(
                "/ActualText" in element
                for element in elements
                if str(element.S) == "/P"
            ), "Empty formatting-only text runs must not become paragraphs"
    finally:
        result.close_output_claim()


@pytest.mark.parametrize("generated", [True, False])
def test_table_binding_and_existing_structure_order_are_preserved(tmp_path, generated):
    from src.education.remediation.content_tagger_v2 import ContentTaggerV2
    from src.education.remediation.pdf_structure import PDFStructureTree

    source = tmp_path / "table.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 72), "Before table")
        page.insert_text((72, 120), "Table cell")
        page.insert_text((72, 180), "After table")
        doc.save(source)
    with pikepdf.open(source) as pdf, fitz.open(source) as doc:
        tree = PDFStructureTree(pdf)
        tree.add_heading(1, 1, "After table")
        page = pdf.pages[0]
        table = pdf.make_indirect(
            pikepdf.Dictionary(S=pikepdf.Name.Table, P=tree.struct_root, Pg=page.obj)
        )
        row = pdf.make_indirect(pikepdf.Dictionary(S=pikepdf.Name.TR, P=table))
        cell = pdf.make_indirect(
            pikepdf.Dictionary(
                S=pikepdf.Name.TD,
                P=row,
                ActualText="Table cell",
                K=pikepdf.Dictionary(Type=pikepdf.Name.MCR, MCID=7, Pg=page.obj),
            )
        )
        row.K = pikepdf.Array([cell])
        table.K = pikepdf.Array([row])
        tree.kids.append(table)
        page.obj.StructParents = 0
        tree.struct_root.ParentTree = pdf.make_indirect(
            pikepdf.Dictionary(
                Nums=pikepdf.Array([0, pikepdf.Array([None] * 7 + [cell])])
            )
        )
        tagger = ContentTaggerV2(pdf, doc, order_generated_structure=generated)
        ops = list(pikepdf.parse_content_stream(page))
        blocks = tagger._find_content_blocks(ops, page)
        start, end, _ = blocks[1]
        ops[end:end] = [pikepdf.ContentStreamInstruction([], pikepdf.Operator("EMC"))]
        ops[start:start] = [
            pikepdf.ContentStreamInstruction(
                [pikepdf.Name.TD, pikepdf.Dictionary(MCID=7)], pikepdf.Operator("BDC")
            )
        ]
        page.obj.Contents = pdf.make_stream(pikepdf.unparse_content_stream(ops))
        tagger.tag_all_pages()
        output = tmp_path / "saved.pdf"
        pdf.save(output)
    with pikepdf.open(output) as saved:
        elements = list(_elements(saved.Root.StructTreeRoot))
        texts = [str(e.ActualText) for e in elements if "/ActualText" in e]
        expected = (
            ["Before table", "Table cell", "After table"]
            if generated
            else ["After table", "Table cell", "Before table"]
        )
        assert texts == expected
        saved_cell = next(e for e in elements if str(e.S) == "/TD")
        assert saved_cell.K.MCID == 7
        assert saved_cell.K.Pg.objgen == saved.pages[0].obj.objgen
        assert (
            saved.Root.StructTreeRoot.ParentTree.Nums[1][7].objgen == saved_cell.objgen
        )


def test_generated_heading_refuses_text_missing_from_source(tmp_path):
    from src.education.remediation.base import RemediationIssue, IssueCategory
    from src.education.remediation.pdf_structure import PDFStructureTree

    source = tmp_path / "heading.pdf"
    with fitz.open() as document:
        document.new_page().insert_text((72, 72), "Visible title")
        document.save(source)
    remediator = PdfRemediator(str(source), [], RemediationConfig(use_ai=False))
    with fitz.open(source) as document, pikepdf.open(source) as pdf:
        remediator._struct_tree = PDFStructureTree(pdf)
        issue = RemediationIssue(
            id="heading",
            category=IssueCategory.HEADING,
            description="Missing heading",
            severity="medium",
        )
        assert not remediator._apply_heading_fix(issue, document, "(anonymous)")
        assert len(remediator._struct_tree.kids) == 0
        assert remediator._apply_heading_fix(issue, document, "Visible title")
        assert not remediator._apply_heading_fix(issue, document, "Visible title")
        assert len(remediator._struct_tree.kids) == 1


def test_generated_heading_does_not_consume_extra_words_in_same_text_run(tmp_path):
    source = tmp_path / "combined.pdf"
    text = (
        "Title\nBody words belong to the same text run as this heading.\n"
        "Every source word must remain readable without a duplicate title."
    )
    with fitz.open() as doc:
        doc.new_page().insert_text((72, 72), text)
        doc.save(source)
    scan = PDFProcessor(generate_alt_text=False, validate_alt_text=False).process_pdf(
        str(source)
    )
    result = PdfRemediator(
        str(source),
        scan.issues,
        RemediationConfig(
            use_ai=False,
            verify_fixes=True,
            create_backup=False,
            output_directory=str(tmp_path),
            allow_legacy_nested_ai=False,
        ),
    ).remediate()
    try:
        assert result.output_file
        with pikepdf.open(result.output_file) as saved:
            elements = list(_elements(saved.Root.StructTreeRoot))
            assert not any(str(e.S) == "/H1" for e in elements)
            texts = [str(e.ActualText) for e in elements if "/ActualText" in e]
            assert texts == [" ".join(text.split())]
        assert not any(
            str(issue.category.value) == "heading" for issue in result.fixed_issues
        )
        assert any(
            str(issue.category.value) == "heading" for issue in result.manual_issues
        )
    finally:
        result.close_output_claim()


def test_heading_fallback_uses_target_page_and_refuses_empty_page(tmp_path):
    from src.education.remediation.base import RemediationIssue, IssueCategory

    source = tmp_path / "pages.pdf"
    with fitz.open() as doc:
        doc.new_page().insert_text((72, 72), "First page")
        doc.new_page().insert_text((72, 72), "Second page")
        doc.new_page()
        doc.set_metadata({"title": "(anonymous)"})
        doc.save(source)
    remediator = PdfRemediator(str(source), [], RemediationConfig(use_ai=False))
    with fitz.open(source) as doc:
        remediator._pdf = doc
        issue = RemediationIssue(
            id="heading",
            category=IssueCategory.HEADING,
            severity="medium",
            description="Missing heading",
            metadata={"page_number": 2},
        )
        assert remediator._get_rule_based_fix(issue, doc) == "Second page"
        issue.metadata["page_number"] = 3
        assert remediator._get_rule_based_fix(issue, doc) is None


def test_partial_text_run_list_stays_manual_without_duplicate_content(tmp_path):
    source = tmp_path / "combined-list.pdf"
    text = (
        "1. First source item contains enough text to describe its purpose.\n"
        "2. Second source item must not be copied into unbound list structure.\n"
        "3. Third source item remains readable in the same source text run."
    )
    with fitz.open() as doc:
        doc.new_page().insert_text((72, 72), text)
        doc.save(source)
    scan = PDFProcessor(generate_alt_text=False, validate_alt_text=False).process_pdf(
        str(source)
    )
    result = PdfRemediator(
        str(source),
        scan.issues,
        RemediationConfig(
            use_ai=False,
            verify_fixes=True,
            create_backup=False,
            output_directory=str(tmp_path),
            allow_legacy_nested_ai=False,
        ),
    ).remediate()
    try:
        assert result.output_file
        with pikepdf.open(result.output_file) as saved:
            elements = list(_elements(saved.Root.StructTreeRoot))
            assert not any(str(e.S) == "/L" for e in elements)
            texts = [str(e.ActualText) for e in elements if "/ActualText" in e]
            assert texts == [" ".join(text.split())]
        assert not any(issue.category.value == "list" for issue in result.fixed_issues)
        assert any(issue.category.value == "list" for issue in result.manual_issues)
    finally:
        result.close_output_claim()


def test_empty_text_setup_is_not_tagged_but_real_text_is(tmp_path):
    from src.education.remediation.content_tagger_v2 import ContentTaggerV2

    source = tmp_path / "empty-text.pdf"
    with fitz.open() as doc:
        doc.new_page().insert_text((72, 72), "Visible words")
        doc.save(source)
    with pikepdf.open(source) as pdf, fitz.open(source) as doc:
        page = pdf.pages[0]
        existing = pikepdf.unparse_content_stream(pikepdf.parse_content_stream(page))
        page.obj.Contents = pdf.make_stream(b"BT /helv 12 Tf 0 0 Td ET\n" + existing)
        tagger = ContentTaggerV2(pdf, doc, order_generated_structure=True)
        stats = tagger.tag_all_pages()
        assert stats["blocks_created"] == 1
        paragraphs = [e for e in _elements(pdf.Root.StructTreeRoot) if str(e.S) == "/P"]
        assert len(paragraphs) == 1
        assert str(paragraphs[0].ActualText) == "Visible words"


@pytest.mark.parametrize("inherited", [True, False])
@pytest.mark.parametrize("binding", ["integer", "mcr", "array"])
def test_bound_original_leaf_is_not_reused_for_repeated_unmarked_text(
    tmp_path, inherited, binding
):
    from src.education.remediation.content_tagger_v2 import ContentTaggerV2
    from src.education.remediation.pdf_structure import PDFStructureTree

    source = tmp_path / "repeated.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 72), "Repeated words")
        page.insert_text((72, 120), "Repeated words")
        doc.save(source)
    with pikepdf.open(source) as pdf, fitz.open(source) as doc:
        tree = PDFStructureTree(pdf)
        page = pdf.pages[0]
        parent = pdf.make_indirect(
            pikepdf.Dictionary(S=pikepdf.Name.Document, P=tree.struct_root, Pg=page.obj)
        )
        reference = (
            0
            if binding == "integer"
            else pikepdf.Dictionary(Type=pikepdf.Name.MCR, MCID=0, Pg=page.obj)
        )
        original = pdf.make_indirect(
            pikepdf.Dictionary(
                S=pikepdf.Name.P,
                P=parent,
                ActualText="Repeated words",
                K=pikepdf.Array([reference]) if binding == "array" else reference,
            )
        )
        if not inherited:
            original.Pg = page.obj
        parent.K = pikepdf.Array([original])
        tree.struct_root.K = pikepdf.Array([parent])
        tree.struct_root.ParentTree = pdf.make_indirect(
            pikepdf.Dictionary(Nums=pikepdf.Array([0, pikepdf.Array([original])]))
        )
        page.obj.StructParents = 0
        tagger = ContentTaggerV2(pdf, doc)
        ops = list(pikepdf.parse_content_stream(page))
        start, end, _ = tagger._find_content_blocks(ops, page)[0]
        ops[end:end] = [pikepdf.ContentStreamInstruction([], pikepdf.Operator("EMC"))]
        ops[start:start] = [
            pikepdf.ContentStreamInstruction(
                [pikepdf.Name.P, pikepdf.Dictionary(MCID=0)], pikepdf.Operator("BDC")
            )
        ]
        page.obj.Contents = pdf.make_stream(pikepdf.unparse_content_stream(ops))
        stats = tagger.tag_all_pages()
        assert stats["blocks_matched"] == 0
        assert stats["blocks_created"] == 1
        output = tmp_path / "saved.pdf"
        pdf.save(output)
    with pikepdf.open(output) as saved:
        paragraphs = [
            e for e in _elements(saved.Root.StructTreeRoot) if str(e.S) == "/P"
        ]
        assert [str(e.ActualText) for e in paragraphs] == [
            "Repeated words",
            "Repeated words",
        ]
        owners = saved.Root.StructTreeRoot.ParentTree.Nums[1]
        assert len(owners) == 2
        assert owners[0].objgen != owners[1].objgen
        first_binding = paragraphs[0].K
        if binding == "integer":
            assert first_binding == 0
        else:
            if binding == "array":
                assert len(first_binding) == 1
                first_binding = first_binding[0]
            assert first_binding.MCID == 0
