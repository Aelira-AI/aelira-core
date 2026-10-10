"""Source-bound saved-PDF regressions for order and annotation repairs."""

from pathlib import Path

import pikepdf
import pymupdf as fitz
import pytest
from pikepdf import Array, Dictionary, Name, String

from src.education.pdf_checks.form_checker import FormFieldChecker
from src.education.pdf_checks.reading_order import ReadingOrderVerifier
from src.education.remediation.base import (
    IssueCategory,
    IssueSeverity,
    RemediationIssue,
)
from src.education.remediation.link_fixer import LinkFixer
from src.education.remediation.reading_order import HeuristicStrategy
from test_reading_order_mcid import tagged_pdf, texts

pytestmark = pytest.mark.unit


def wrap_document(pdf):
    root = pdf.Root.StructTreeRoot
    document = pdf.make_indirect(
        Dictionary(Type=Name.StructElem, S=Name.Document, P=root, K=root.K)
    )
    for child in document.K:
        child.P = document
    root.K = Array([document])
    return document


def issue(kind="links_missing_alt", **metadata):
    return RemediationIssue(
        category=IssueCategory.LINK,
        severity=IssueSeverity.HIGH,
        description="Missing link description",
        metadata={"issue_type": kind, "page_number": 1, **metadata},
    )


def annotation(pdf, uri="https://example.edu/resources"):
    return pdf.make_indirect(
        Dictionary(
            Type=Name.Annot,
            Subtype=Name.Link,
            Rect=Array([30, 325, 140, 347]),
            A=Dictionary(S=Name.URI, URI=String(uri)),
        )
    )


def test_wrapped_order_saved_and_content_preserved(tmp_path):
    pdf, path = tagged_pdf(tmp_path, order=(1, 0))
    wrap_document(pdf)
    pdf.save(path)
    before = fitz.open(path)
    render = before[0].get_pixmap().samples
    content = before[0].get_text()
    before.close()
    assert ReadingOrderVerifier().check(str(path)).issues
    result = HeuristicStrategy().fix(str(path))
    assert result.success, result.error
    assert texts(path) == ["First page 0", "Second page 0"]
    assert not ReadingOrderVerifier().check(str(path)).issues
    with fitz.open(path) as after, pikepdf.open(path) as saved:
        assert after[0].get_text() == content
        assert after[0].get_pixmap().samples == render
        assert saved.Root.StructTreeRoot.K[0].S == Name.Document
        assert all(
            child.P == saved.Root.StructTreeRoot.K[0]
            for child in saved.Root.StructTreeRoot.K[0].K
        )


def test_grouped_link_finding_covers_all_pages(tmp_path):
    pdf, path = tagged_pdf(tmp_path, pages=3)
    for page in pdf.pages:
        page.obj.Annots = Array([annotation(pdf)])
    pdf.save(path)
    raw = FormFieldChecker().check_links(str(path))[0]
    with fitz.open(path) as doc:
        result = LinkFixer(pdf, doc).fix([issue(**raw)])[0]
    assert result.success
    assert result.links_fixed == 3
    pdf.save(path)
    assert FormFieldChecker().check_links(str(path)) == []


def test_link_empty_contents_preserves_existing_alt(tmp_path):
    pdf, path = tagged_pdf(tmp_path)
    link = annotation(pdf)
    link.Contents = String("   ")
    link.Alt = String("University library resources")
    pdf.pages[0].obj.Annots = Array([link])
    pdf.save(path)
    with fitz.open(path) as doc:
        result = LinkFixer(pdf, doc).fix([issue()])[0]
    assert result.success
    assert str(link.Contents) == "University library resources"


def test_link_unsafe_action_refused_without_fabricated_name(tmp_path):
    pdf, path = tagged_pdf(tmp_path)
    link = annotation(pdf, "javascript:alert(1)")
    pdf.pages[0].obj.Annots = Array([link])
    pdf.save(path)
    with fitz.open(path) as doc:
        result = LinkFixer(pdf, doc).fix([issue()])[0]
    assert not result.success
    assert result.error
    assert "/Contents" not in link


def test_order_cyclic_structure_refuses_without_writing(tmp_path):
    pdf, path = tagged_pdf(tmp_path, order=(1, 0))
    document = wrap_document(pdf)
    document.K.append(document)
    pdf.save(path)
    original = Path(path).read_bytes()
    result = HeuristicStrategy().fix(str(path))
    assert not result.success
    assert result.error
    assert Path(path).read_bytes() == original


def running_numbers(tmp_path):
    pdf = pikepdf.new()
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    pdf.Root.StructTreeRoot = root
    children, numbers = [], []
    for number in range(1, 4):
        page = pdf.add_blank_page(page_size=(400, 400))
        page.obj.StructParents = number - 1
        page.obj.Resources = Dictionary(
            Font=Dictionary(
                F1=Dictionary(
                    Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica
                )
            )
        )
        labels = [
            (30, 330, f"Title {number}"),
            (350, 20, f"{number:02}"),
            (30, 230, f"Body content belonging to this example page {number}"),
        ]
        page.obj.Contents = pdf.make_stream(
            "\n".join(
                f"/P <</MCID {i}>> BDC BT /F1 12 Tf {x} {y} Td ({text}) Tj ET EMC"
                for i, (x, y, text) in enumerate(labels)
            ).encode()
        )
        owners = [
            pdf.make_indirect(
                Dictionary(Type=Name.StructElem, S=Name.P, P=root, Pg=page.obj, K=i)
            )
            for i in range(3)
        ]
        children.extend(owners)
        numbers.extend([number - 1, Array(owners)])
    root.K = Array(children)
    root.ParentTree = Dictionary(Nums=Array(numbers))
    wrap_document(pdf)
    path = tmp_path / "running.pdf"
    pdf.save(path)
    return pdf, path


def test_running_page_numbers_preserve_page_and_group_identity(tmp_path):
    pdf, path = running_numbers(tmp_path)
    document = pdf.Root.StructTreeRoot.K[0]
    before = [child.objgen for child in document.K]
    result = HeuristicStrategy().fix_document(pdf, 2)
    assert result.success, result.error
    assert [child.objgen for child in document.K] == before[:3] + [
        before[3],
        before[5],
        before[4],
    ] + before[6:]
    assert all(child.P == document for child in document.K)
    pdf.save(path)
    assert texts(path, 1) == [
        "Title 2",
        "Body content belonging to this example page 2",
        "02",
    ]


def test_ordinary_remediator_saves_the_retained_order_handle(tmp_path):
    from src.education.remediation.base import RemediationConfig
    from src.education.remediation.pdf_remediator import PdfRemediator

    pdf, path = running_numbers(tmp_path)
    from src.education.pdf_processor import PDFProcessor

    raw = (
        PDFProcessor(generate_alt_text=False, validate_alt_text=False)
        .process_pdf(str(path))
        .issues
    )
    result = PdfRemediator(
        str(path),
        raw,
        RemediationConfig(
            use_ai=False,
            verify_fixes=True,
            create_backup=False,
            output_directory=str(tmp_path / "out"),
        ),
    ).remediate()
    assert result.success, result.error_message
    assert result.output_file
    assert texts(result.output_file, 1) == [
        "Title 2",
        "Body content belonging to this example page 2",
        "02",
    ]
    assert (
        sum(
            fixed.category == IssueCategory.READING_ORDER
            for fixed in result.fixed_issues
        )
        == 3
    )
    assert result.verification_passed


@pytest.mark.parametrize(
    "case",
    [
        "repeated",
        "figure",
        "table",
        "form",
        "replacement",
        "columns",
        "object_reference",
        "custom_role",
        "bad_parent",
        "bad_owner",
        "mixed_pages",
    ],
)
def test_ambiguous_order_refused_without_mutation(tmp_path, case):
    pdf, path = tagged_pdf(
        tmp_path, order=(1, 0), pages=2 if case == "mixed_pages" else 1
    )
    document = wrap_document(pdf)
    leaf = document.K[0]
    if case == "repeated":
        content = (
            pdf.pages[0]
            .obj.Contents.read_bytes()
            .replace(b"Second page 0", b"First page 0")
        )
        pdf.pages[0].obj.Contents = pdf.make_stream(content)
    elif case in {"figure", "table", "form", "custom_role"}:
        leaf.S = Name(
            "/"
            + {
                "figure": "Figure",
                "table": "Table",
                "form": "Form",
                "custom_role": "Custom",
            }[case]
        )
    elif case == "replacement":
        leaf.ActualText = String("Second page 0")
    elif case == "columns":
        content = (
            pdf.pages[0].obj.Contents.read_bytes().replace(b"30 270 Td", b"220 330 Td")
        )
        pdf.pages[0].obj.Contents = pdf.make_stream(content)
    elif case == "object_reference":
        leaf.K = Dictionary(Type=Name.OBJR, Obj=annotation(pdf))
    elif case == "bad_parent":
        leaf.P = pdf.Root.StructTreeRoot
    elif case == "bad_owner":
        pdf.Root.StructTreeRoot.ParentTree.Nums[1][0] = leaf
    elif case == "mixed_pages":
        document.K = Array([document.K[0], document.K[2], document.K[1], document.K[3]])
    pdf.save(path)
    original = Path(path).read_bytes()
    result = HeuristicStrategy().fix_document(pdf, 1)
    assert not result.success, case
    assert result.error
    # Repeated identical text is itself ambiguous even when a text-only scanner
    # cannot distinguish the swapped occurrences.
    pdf.save(tmp_path / "candidate.pdf")
    with (
        pikepdf.open(path) as source,
        pikepdf.open(tmp_path / "candidate.pdf") as candidate,
    ):
        assert str(source.Root.StructTreeRoot) == str(candidate.Root.StructTreeRoot)
    assert Path(path).read_bytes() == original


def test_order_resource_limits_fail_closed(tmp_path, monkeypatch):
    from src.education.remediation import source_order

    pdf, path = tagged_pdf(tmp_path, order=(1, 0))
    wrap_document(pdf)
    pdf.save(path)
    monkeypatch.setattr(source_order, "MAX_NODES", 1)
    result = HeuristicStrategy().fix_document(pdf, 1)
    assert not result.success
    assert result.error == "reading_order_structure_limit"


def test_order_total_document_attempts_bounded(tmp_path):
    pdf, _ = running_numbers(tmp_path)
    strategy = HeuristicStrategy()
    strategy._repair_attempts = 20
    result = strategy.fix_document(pdf, 1)
    assert not result.success
    assert result.error == "reading_order_document_repair_limit"


@pytest.mark.parametrize(
    "case",
    [
        "javascript",
        "launch",
        "next",
        "aa",
        "relative",
        "bad_destination",
        "named_destination",
        "duplicate",
        "wrong_page",
    ],
)
def test_unsupported_links_refuse_without_changing_actions(tmp_path, case):
    pdf, path = tagged_pdf(tmp_path, pages=2)
    link = annotation(pdf)
    pdf.pages[0].obj.Annots = Array([link])
    if case == "javascript":
        link.A = Dictionary(S=Name.JavaScript, JS=String("app.alert(1)"))
    elif case == "launch":
        link.A = Dictionary(S=Name.Launch, F=String("application.exe"))
    elif case == "next":
        link.A.Next = Dictionary(S=Name.JavaScript, JS=String("app.alert(1)"))
    elif case == "aa":
        link.AA = Dictionary(E=Dictionary(S=Name.JavaScript, JS=String("app.alert(1)")))
    elif case == "relative":
        link.A.URI = String("/relative/path")
    elif case == "bad_destination":
        link.A = Dictionary(S=Name.GoTo, D=Array([999, Name.Fit]))
    elif case == "named_destination":
        link.A = Dictionary(S=Name.GoTo, D=String("missing-name"))
    elif case == "duplicate":
        pdf.pages[1].obj.Annots = Array([link])
    elif case == "wrong_page":
        link.P = pdf.pages[1].obj
    pdf.save(path)
    original = str(link)
    with fitz.open(path) as doc:
        result = LinkFixer(pdf, doc).fix([issue()])[0]
    assert not result.success
    assert result.links_fixed == 0
    assert result.error
    assert str(link) == original


def test_explicit_local_destination_and_partial_group(tmp_path):
    pdf, path = tagged_pdf(tmp_path, pages=2)
    local, unsafe = annotation(pdf), annotation(pdf, "javascript:alert(1)")
    local.A = Dictionary(S=Name.GoTo, D=Array([pdf.pages[1].obj, Name.Fit]))
    # A rectangle away from text requires an exact supported destination label.
    local.Rect = Array([1, 1, 5, 5])
    pdf.pages[0].obj.Annots = Array([local, unsafe])
    pdf.save(path)
    with fitz.open(path) as doc:
        result = LinkFixer(pdf, doc).fix([issue()])[0]
    assert not result.success
    assert result.links_fixed == 1
    assert str(local.Contents) == "Go to page 2"
    assert "/Contents" not in unsafe
    assert "page 1 link 2" in result.error
    pdf.save(path)
    remaining = FormFieldChecker().check_links(str(path))
    assert len(remaining) == 1
    assert "1 of 2" in remaining[0]["message"]


def test_existing_contents_and_action_preserved_even_with_ai(tmp_path):
    pdf, path = tagged_pdf(tmp_path)
    link = annotation(pdf)
    link.Contents = String("Existing library description")
    pdf.pages[0].obj.Annots = Array([link])
    pdf.save(path)

    class NoAI:
        def generate_text_sync(self, **kwargs):
            raise AssertionError("AI cannot establish source link meaning")

    original = str(link)
    with fitz.open(path) as doc:
        result = LinkFixer(pdf, doc, NoAI()).fix([issue()])[0]
    assert not result.success
    assert result.links_fixed == 0
    assert str(link) == original


def test_untagged_footer_cannot_move_same_text_body_node(tmp_path):
    pdf, path = running_numbers(tmp_path)
    page = pdf.pages[1]
    content = page.obj.Contents.read_bytes().replace(
        b"350 20 Td (02)", b"30 160 Td (02)"
    )
    content += b"\nBT /F1 12 Tf 350 20 Td (02) Tj ET"
    page.obj.Contents = pdf.make_stream(content)
    pdf.save(path)
    before = str(pdf.Root.StructTreeRoot)
    result = HeuristicStrategy().fix_document(pdf, 2)
    assert not result.success
    assert str(pdf.Root.StructTreeRoot) == before


def test_running_number_does_not_permute_semantic_parent(tmp_path):
    pdf, path = running_numbers(tmp_path)
    # A table must not become a sortable page container merely because it
    # includes a numeric cell at the bottom of the page.
    pdf.Root.StructTreeRoot.K[0].S = Name.Table
    pdf.save(path)
    before = str(pdf.Root.StructTreeRoot)
    result = HeuristicStrategy().fix_document(pdf, 2)
    assert not result.success
    assert str(pdf.Root.StructTreeRoot) == before


def test_nested_groups_keep_semantics_and_parent_tree(tmp_path):
    pdf, path = tagged_pdf(tmp_path, order=(1, 0))
    document = wrap_document(pdf)
    groups = []
    for child in document.K:
        group = pdf.make_indirect(
            Dictionary(
                Type=Name.StructElem,
                S=Name.Div,
                P=document,
                K=Array([child]),
                Pg=pdf.pages[0].obj,
            )
        )
        child.P = group
        groups.append(group)
    document.K = Array(groups)
    parent_tree = [owner.objgen for owner in pdf.Root.StructTreeRoot.ParentTree.Nums[1]]
    pdf.save(path)
    result = HeuristicStrategy().fix_document(pdf, 1)
    assert result.success, result.error
    assert [group.objgen for group in document.K] == [
        group.objgen for group in reversed(groups)
    ]
    assert [
        owner.objgen for owner in pdf.Root.StructTreeRoot.ParentTree.Nums[1]
    ] == parent_tree
    assert all(group.K[0].P == group for group in document.K)
    pdf.save(path)
    assert texts(path) == ["First page 0", "Second page 0"]


def test_ordinary_partial_links_stay_manual_and_survive_save(tmp_path):
    from src.education.pdf_processor import PDFProcessor
    from src.education.remediation.base import RemediationConfig
    from src.education.remediation.pdf_remediator import PdfRemediator

    pdf, path = running_numbers(tmp_path)
    pdf.pages[0].obj.Annots = Array(
        [annotation(pdf), annotation(pdf, "javascript:alert(1)")]
    )
    pdf.save(path)
    raw = (
        PDFProcessor(generate_alt_text=False, validate_alt_text=False)
        .process_pdf(str(path))
        .issues
    )
    result = PdfRemediator(
        str(path),
        raw,
        RemediationConfig(
            use_ai=False,
            verify_fixes=True,
            create_backup=False,
            output_directory=str(tmp_path / "out"),
        ),
    ).remediate()
    assert result.success, result.error_message
    assert result.output_file
    assert not any(
        fixed.category == IssueCategory.LINK for fixed in result.fixed_issues
    )
    manual = [
        item for item in result.manual_issues if item.category == IssueCategory.LINK
    ]
    assert len(manual) == 1
    assert "page 1 link 2" in manual[0].reason
    with pikepdf.open(result.output_file) as saved:
        assert str(saved.pages[0].obj.Annots[0].Contents).strip()
        assert "/Contents" not in saved.pages[0].obj.Annots[1]
    assert "1 of 2" in FormFieldChecker().check_links(result.output_file)[0]["message"]


def test_link_annotation_limit_refuses_before_mutation(tmp_path, monkeypatch):
    from src.education.remediation import link_fixer

    pdf, path = tagged_pdf(tmp_path)
    link = annotation(pdf)
    pdf.pages[0].obj.Annots = Array([link])
    pdf.save(path)
    monkeypatch.setattr(link_fixer, "MAX_ANNOTATIONS", 0)
    with fitz.open(path) as doc:
        result = LinkFixer(pdf, doc).fix([issue()])[0]
    assert not result.success
    assert result.error == "link_annotation_limit"
    assert "/Contents" not in link


def test_link_rtl_text_uses_exact_destination(tmp_path):
    pdf, path = tagged_pdf(tmp_path)
    page = pdf.pages[0]
    cmap = b"""/CIDInit /ProcSet findresource begin 12 dict begin begincmap
/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def
/CMapName /Hebrew def /CMapType 2 def
1 begincodespacerange <00> <FF> endcodespacerange
3 beginbfchar <61> <05D0> <62> <05D1> <63> <05D2> endbfchar
endcmap CMapName currentdict /CMap defineresource pop end end"""
    page.obj.Resources.Font.F1.ToUnicode = pdf.make_stream(cmap)
    page.obj.Contents = pdf.make_stream(b"BT /F1 12 Tf 30 330 Td (abc abc) Tj ET")
    link = annotation(pdf)
    link.Rect = Array([25, 320, 160, 350])
    page.obj.Annots = Array([link])
    pdf.save(path)
    with fitz.open(path) as doc:
        assert any("\u05d0" in word[4] for word in doc[0].get_text("words"))
        result = LinkFixer(pdf, doc).fix([issue()])[0]
    assert result.success
    assert str(link.Contents) == "https://example.edu/resources"


def test_running_number_refuses_semantic_ancestor(tmp_path):
    pdf, path = running_numbers(tmp_path)
    root = pdf.Root.StructTreeRoot
    document = root.K[0]
    group = pdf.make_indirect(
        Dictionary(
            Type=Name.StructElem,
            S=Name.Div,
            P=document,
            Pg=pdf.pages[1].obj,
            K=Array(list(document.K)[3:6]),
        )
    )
    for child in group.K:
        child.P = group
    document.K = Array(list(document.K)[:3] + [group] + list(document.K)[6:])
    document.S = Name.Table
    pdf.save(path)
    original = [child.objgen for child in group.K]
    result = HeuristicStrategy().fix_document(pdf, 2)
    assert not result.success
    assert [child.objgen for child in group.K] == original


@pytest.mark.parametrize("page_number", [None, True, "1", 0, -1, 4])
def test_invalid_order_page_metadata_stays_manual(tmp_path, page_number):
    from src.education.remediation.base import RemediationConfig
    from src.education.remediation.pdf_remediator import PdfRemediator

    pdf, path = running_numbers(tmp_path)
    remediator = PdfRemediator(str(path), [], RemediationConfig(use_ai=False))
    remediator._pikepdf_doc = pdf
    finding = RemediationIssue(
        category=IssueCategory.READING_ORDER,
        severity=IssueSeverity.MEDIUM,
        description="Order needs review",
        metadata={"issue_type": "reading_order_mismatch", "page_number": page_number},
    )
    original = str(pdf.Root.StructTreeRoot)
    with fitz.open(path) as doc:
        remediator._process_issue(finding, doc)
    assert remediator.result.fixed_count == 0
    assert remediator.result.failed_count == 0
    assert len(remediator.result.manual_issues) == 1
    assert remediator.result.manual_issues[0].reason == "reading_order_invalid_page"
    assert str(pdf.Root.StructTreeRoot) == original
