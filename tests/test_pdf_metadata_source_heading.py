"""Metadata acceptance must bind its first heading to existing source content."""

from pathlib import Path
from hashlib import sha256
from io import BytesIO

import fitz
import pikepdf
import pytest

from src.education.pdf_processor import PDFProcessor
from src.education.remediation.base import (
    IssueCategory,
    RemediationConfig,
    RemediationIssue,
)
from src.education.remediation.pdf_remediator import PdfRemediator
from src.education.remediation.content_tagger_v2 import ContentTaggerV2
from src.education.remediation.pdf_structure import PDFStructureTree

SOURCE = Path(__file__).parent / "fixtures/document_stack/metadata.pdf"
TITLE = "Synthetic metadata failure"


def _drawing_operations(pdf):
    return pikepdf.unparse_content_stream(
        [
            op
            for op in pikepdf.parse_content_stream(pdf.pages[0])
            if str(op.operator) not in {"BDC", "EMC"}
        ]
    )


def test_metadata_full_scan_remediation_preserves_source(tmp_path):
    source = SOURCE
    source_hash = sha256(source.read_bytes()).hexdigest()
    scanner = PDFProcessor(
        generate_alt_text=False, validate_alt_text=False, require_complete_scan=True
    )
    scan = scanner.process_pdf(str(source))
    result = PdfRemediator(
        str(source),
        scan.issues,
        RemediationConfig(
            output_directory=str(tmp_path),
            create_backup=False,
            use_ai=False,
            verify_fixes=True,
        ),
    ).remediate()
    try:
        assert result.output_file
        assert result.success
        assert result.verification_passed
        assert result.fixed_count == len(scan.issues)
        assert not result.manual_issues
        remaining = scanner.process_pdf(result.output_file)
        assert not remaining.issues, [issue for issue in remaining.issues]
        with fitz.open(source) as original, fitz.open(result.output_file) as saved:
            assert [page.get_text() for page in saved] == [
                page.get_text() for page in original
            ]
            assert saved[0].get_pixmap().samples == original[0].get_pixmap().samples
        with (
            pikepdf.open(source) as original,
            pikepdf.open(result.output_file) as saved,
        ):
            assert _drawing_operations(saved) == _drawing_operations(original)
            heading, paragraph = saved.Root.StructTreeRoot.K.K
            assert str(heading.S) == "/H1"
            assert heading.K == 1
            assert str(paragraph.S) == "/P"
            assert paragraph.K == 0
            assert "/ActualText" not in heading
            assert "/ActualText" not in paragraph
            owners = saved.Root.StructTreeRoot.ParentTree.Nums[1]
            assert owners[0].objgen == paragraph.objgen
            assert owners[1].objgen == heading.objgen
            assert heading.Pg.objgen == saved.pages[0].obj.objgen
            assert paragraph.Pg.objgen == saved.pages[0].obj.objgen
        assert sha256(source.read_bytes()).hexdigest() == source_hash
    finally:
        result.close_output_claim()


@pytest.mark.parametrize(
    "damage",
    [
        "partial_bt",
        "nested_marks",
        "unbalanced_marks",
        "duplicate_mcid",
        "owner_replacement",
        "ancestor_replacement",
        "wrong_parent",
        "wrong_page",
        "shared_owner",
        "duplicate_owner_reference",
        "duplicate_array_reference",
        "duplicate_mcr_reference",
        "malformed_parent_tree",
        "ambiguous_paragraphs",
        "unsupported_font",
        "marked_replacement",
        "nested_bt",
        "malformed_show_text",
        "shared_page_key",
        "duplicate_parent_tree_owner",
        "dangling_scalar_reference",
        "dangling_mcr_reference",
        "duplicate_title_single_marked_run",
        "duplicate_title_unmarked_run",
        "duplicate_title_inside_body_run",
        "malformed_tj_array",
        "boolean_tj_array",
        "unsupported_quote_show",
    ],
)
def test_existing_marked_heading_refuses_unsafe_binding_without_mutation(
    tmp_path, damage
):
    with pikepdf.open(SOURCE) as pdf, fitz.open(SOURCE) as document:
        page = pdf.pages[0]
        root = pdf.Root.StructTreeRoot
        parent = root.K
        owner = parent.K[0]
        raw = page.Contents.read_bytes()
        if damage == "partial_bt":
            raw = raw.replace(
                b") Tj ET\nBT /F1 12 Tf 72 706 Td (", b") Tj 0 -24 Td (", 1
            )
        elif damage == "nested_marks":
            raw = raw.replace(b"BT /F1", b"/Span BMC\nBT /F1", 1).replace(
                b" Tj ET", b" Tj ET\nEMC", 1
            )
        elif damage == "unbalanced_marks":
            raw += b"\nEMC"
        elif damage == "duplicate_mcid":
            raw += b"\n/P <</MCID 0>> BDC EMC"
        elif damage == "owner_replacement":
            owner.ActualText = pikepdf.String("replacement")
        elif damage == "ancestor_replacement":
            parent.ActualText = pikepdf.String("replacement")
        elif damage == "wrong_parent":
            owner.P = root
        elif damage == "wrong_page":
            owner.Pg = pdf.add_blank_page().obj
        elif damage == "shared_owner":
            parent.K.append(owner)
        elif damage == "duplicate_owner_reference":
            parent.K.append(
                pdf.make_indirect(pikepdf.Dictionary(S=pikepdf.Name.P, K=0))
            )
        elif damage == "duplicate_array_reference":
            parent.K.append(
                pdf.make_indirect(
                    pikepdf.Dictionary(S=pikepdf.Name.P, K=pikepdf.Array([0]))
                )
            )
        elif damage == "duplicate_mcr_reference":
            parent.K.append(
                pdf.make_indirect(
                    pikepdf.Dictionary(
                        S=pikepdf.Name.P,
                        K=pikepdf.Array(
                            [
                                pikepdf.Dictionary(
                                    Type=pikepdf.Name.MCR, MCID=0, Pg=page.obj
                                )
                            ]
                        ),
                    )
                )
            )
        elif damage == "malformed_parent_tree":
            root.ParentTree.Nums.append(1)
        elif damage == "ambiguous_paragraphs":
            second = pdf.make_indirect(pikepdf.Dictionary(S=pikepdf.Name.P, K=1))
            parent.K.append(second)
            root.ParentTree.Nums[1].append(second)
            raw += b"\n" + raw.replace(b"MCID 0", b"MCID 1")
        elif damage == "unsupported_font":
            page.Resources.Font.F1.Encoding = pikepdf.Dictionary(
                BaseEncoding=pikepdf.Name.StandardEncoding,
                Differences=pikepdf.Array([65, pikepdf.Name.Z]),
            )
        elif damage == "marked_replacement":
            raw = raw.replace(b"/MCID 0", b"/MCID 0 /ActualText (replacement)")
        elif damage == "nested_bt":
            raw = raw.replace(b"BT /F1", b"BT BT /F1", 1)
        elif damage == "malformed_show_text":
            raw = raw.replace(b" Tj ET", b" Tj 42 Tj ET", 1)
        elif damage == "shared_page_key":
            pdf.add_blank_page().obj.StructParents = 0
        elif damage == "duplicate_parent_tree_owner":
            root.ParentTree.Nums[1].append(owner)
        elif damage in {"dangling_scalar_reference", "dangling_mcr_reference"}:
            reference = (
                1
                if damage == "dangling_scalar_reference"
                else pikepdf.Dictionary(
                    Type=pikepdf.Name.MCR,
                    MCID=1,
                    Pg=page.obj,
                )
            )
            parent.K.append(
                pdf.make_indirect(
                    pikepdf.Dictionary(
                        Type=pikepdf.Name.StructElem,
                        S=pikepdf.Name.P,
                        K=reference,
                        P=parent,
                        Pg=page.obj,
                    )
                )
            )
        elif damage in {
            "duplicate_title_single_marked_run",
            "duplicate_title_unmarked_run",
        }:
            run = b"BT /F1 12 Tf 72 600 Td (Synthetic metadata failure) Tj ET"
            if damage == "duplicate_title_single_marked_run":
                second = pdf.make_indirect(
                    pikepdf.Dictionary(
                        Type=pikepdf.Name.StructElem,
                        S=pikepdf.Name.P,
                        K=1,
                        P=parent,
                        Pg=page.obj,
                    )
                )
                parent.K.append(second)
                root.ParentTree.Nums[1].append(second)
                run = b"/P <</MCID 1>> BDC " + run + b" EMC"
            raw += b"\n" + run
        elif damage == "duplicate_title_inside_body_run":
            raw = raw.replace(
                b"Title and language are absent",
                b"Body repeats Synthetic metadata failure inside this run",
            )
        elif damage in {"malformed_tj_array", "boolean_tj_array"}:
            item = b"/Bogus" if damage == "malformed_tj_array" else b"true"
            raw = raw.replace(
                b"(Synthetic metadata failure) Tj",
                b"[(Synthetic metadata failure) " + item + b"] TJ",
            )
        elif damage == "unsupported_quote_show":
            raw += b"\nBT /F1 12 Tf 72 600 Td (Synthetic metadata failure) ' ET"
        page.Contents = pdf.make_stream(raw)
        tagger = ContentTaggerV2(pdf, document)
        before = BytesIO()
        pdf.save(before)
        assert not tagger.promote_marked_paragraph_prefix(0, TITLE, 1)
        after = BytesIO()
        pdf.save(after)
        assert after.getvalue() == before.getvalue()


def test_promoted_binding_survives_tagger_reuse_for_unmarked_content(tmp_path):
    source = tmp_path / "mixed-marking.pdf"
    with pikepdf.open(SOURCE) as pdf:
        page = pdf.pages[0]
        page.Contents = pdf.make_stream(
            page.Contents.read_bytes()
            + b"\nBT /F1 12 Tf 72 640 Td (Additional source paragraph) Tj ET"
        )
        pdf.save(source)
    with pikepdf.open(source) as pdf, fitz.open(source) as document:
        tagger = ContentTaggerV2(pdf, document)
        assert tagger.promote_marked_paragraph_prefix(0, TITLE, 1)
        before = list(pdf.Root.StructTreeRoot.ParentTree.Nums[1])
        stats = tagger.tag_all_pages()
        assert stats["blocks_created"] == 1
        after = pdf.Root.StructTreeRoot.ParentTree.Nums[1]
        assert len(after) == 3
        assert [element.objgen for element in after][:2] == [
            element.objgen for element in before
        ]
        assert str(after[1].S) == "/H1"


def test_heading_issue_for_second_identical_marked_line_stays_manual(tmp_path):
    source = tmp_path / "ambiguous-heading.pdf"
    with pikepdf.open(SOURCE) as pdf:
        page = pdf.pages[0]
        parent = pdf.Root.StructTreeRoot.K
        second = pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name.StructElem,
                S=pikepdf.Name.P,
                K=1,
                P=parent,
                Pg=page.obj,
            )
        )
        parent.K.append(second)
        pdf.Root.StructTreeRoot.ParentTree.Nums[1].append(second)
        page.Contents = pdf.make_stream(
            page.Contents.read_bytes()
            + b"\n/P <</MCID 1>> BDC BT /F1 12 Tf 72 600 Td (Synthetic metadata failure) Tj ET EMC"
        )
        pdf.save(source)
    with pikepdf.open(source) as pdf, fitz.open(source) as document:
        matches = document[0].search_for(TITLE)
        assert len(matches) == 2
        remediator = PdfRemediator(str(source), [], RemediationConfig(use_ai=False))
        remediator._struct_tree = PDFStructureTree(pdf)
        issue = RemediationIssue(
            id="second-heading",
            category=IssueCategory.HEADING,
            description="Second occurrence needs a heading",
            severity="medium",
            metadata={
                "page_number": 1,
                "suggested_level": 2,
                "text": TITLE,
                "bbox": tuple(matches[1]),
            },
        )
        before = BytesIO()
        pdf.save(before)
        assert not remediator._apply_heading_fix(issue, document, TITLE)
        assert issue.id in remediator._source_binding_refusals
        after = BytesIO()
        pdf.save(after)
        assert after.getvalue() == before.getvalue()
