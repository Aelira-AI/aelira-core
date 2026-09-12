"""Reading order compares content sequences, independent of text block slicing."""

import fitz
import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name

from src.education.pdf_checks.completeness import (
    IncompletePDFScanError,
    require_complete_pdf_scan,
)
from src.education.pdf_checks.reading_order import ReadingOrderVerifier

pytestmark = pytest.mark.unit


def _blocks(texts):
    return [
        {
            "text": text,
            "x": 20,
            "y": index * 30,
            "bbox": (20, index * 30, 180, index * 30 + 10),
        }
        for index, text in enumerate(texts)
    ]


@pytest.mark.parametrize(
    "visual,structure",
    [
        (
            ["Midterm Exam 25% Covers chapters 1-6", "Final Exam 40% All chapters"],
            [
                "Midterm Exam",
                "25%",
                "Covers chapters 1-6",
                "Final Exam",
                "40%",
                "All chapters",
            ],
        ),
        (
            ["First sentence", "Second sentence", "Third sentence"],
            ["First sentence Second sentence Third sentence"],
        ),
        (
            ["First\n sentence\t Second sentence"],
            ["first sentence", "SECOND   sentence"],
        ),
        (["Shared first Shared second"], ["Shared", "first", "Shared", "second"]),
    ],
)
def test_split_merge_and_whitespace_preserve_order(visual, structure):
    assert (
        ReadingOrderVerifier()._compare_reading_orders(
            1, _blocks(visual), _blocks(structure)
        )
        is None
    )


@pytest.mark.parametrize("multi_column", [False, True])
@pytest.mark.parametrize(
    "visual,structure",
    [
        (
            ["First paragraph", "Second paragraph"],
            ["Second paragraph", "First paragraph"],
        ),
        (["First paragraph", "Second paragraph"], ["First paragraph"]),
        (
            ["First paragraph", "Second paragraph"],
            ["First paragraph", "Second paragraph", "Second paragraph"],
        ),
        (["A complete important paragraph"], ["A"]),
        (["Same prefix original ending"], ["Same prefix substituted ending"]),
        (
            ["Paragraph " + str(i) for i in range(20)],
            ["Paragraph " + str(i) for i in range(19)],
        ),
        (["x" * 120 + " original"], ["x" * 120 + " substituted"]),
        (["First paragraph"], []),
        ([], ["Invented paragraph"]),
    ],
)
def test_changed_content_or_sequence_is_not_accepted(visual, structure, multi_column):
    issue = ReadingOrderVerifier()._compare_reading_orders(
        3, _blocks(visual), _blocks(structure), multi_column=multi_column
    )
    assert issue is not None
    assert issue.page_number == 3


def _columns(with_heading=False):
    blocks = [
        {"text": text, "x": x, "y": y, "bbox": (x, y, x + 100, y + 10)}
        for text, x, y in [
            ("Left first", 20, 40),
            ("Right first", 300, 40),
            ("Left second", 20, 70),
            ("Right second", 300, 70),
        ]
    ]
    if with_heading:
        blocks.insert(
            0, {"text": "Page heading", "x": 20, "y": 10, "bbox": (20, 10, 400, 20)}
        )
    return blocks


@pytest.mark.parametrize("with_heading", [False, True])
def test_geometry_supported_column_order_is_accepted(with_heading):
    visual = _columns(with_heading)
    texts = ["Left first", "Left second", "Right first", "Right second"]
    if with_heading:
        texts.insert(0, "Page heading")
    assert (
        ReadingOrderVerifier()._compare_reading_orders(
            1, visual, _blocks(texts), multi_column=True
        )
        is None
    )


@pytest.mark.parametrize(
    "texts",
    [
        ["Left second", "Left first", "Right first", "Right second"],
        ["Right first", "Right second", "Left first", "Left second"],
        ["Left first", "Left second", "Right first"],
        ["Left first", "Left second", "Right first", "Right first", "Right second"],
    ],
)
def test_column_tolerance_does_not_allow_reordered_missing_or_duplicate_content(texts):
    assert (
        ReadingOrderVerifier()._compare_reading_orders(
            1, _columns(), _blocks(texts), multi_column=True
        )
        is not None
    )


def test_oversize_comparison_cannot_report_a_complete_scan():
    with pytest.raises(IncompletePDFScanError, match="reading_order.comparison_limit"):
        with require_complete_pdf_scan(True):
            ReadingOrderVerifier()._compare_reading_orders(
                1, _blocks(["x" * 200001]), _blocks(["x" * 200001])
            )


def test_nonvisual_alternative_is_not_called_reordered_or_verified():
    structure = [
        {"text": "Visible paragraph", "source": "ActualText"},
        {"text": "Description of a diagram", "source": "Alt"},
    ]
    with require_complete_pdf_scan(True):
        issue = ReadingOrderVerifier()._compare_reading_orders(
            1, _blocks(["Visible paragraph"]), structure
        )
    assert issue is not None
    assert issue.severity == "warning"
    assert "placement" in issue.recommendation
    assert issue.actual_order == ["Visible paragraph", "Description of a diagram"]


@pytest.mark.parametrize("reordered", [False, True])
def test_bound_image_alt_keeps_strict_scan_available_with_honest_review(
    tmp_path, reordered
):
    from src.education.pdf_processor import PDFProcessor
    from src.education.remediation.content_tagger_v2 import ContentTaggerV2

    source, saved = tmp_path / "image-source.pdf", tmp_path / "image-saved.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text(
            (40, 60),
            "First paragraph provides source text for the reading order check.",
        )
        page.insert_text(
            (40, 150),
            "Second paragraph retains its original words and position on the page.",
        )
        pixels = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 10, 10), False)
        pixels.clear_with(128)
        page.insert_image(fitz.Rect(40, 80, 60, 100), pixmap=pixels)
        doc.save(source)
    with pikepdf.open(source) as pdf, fitz.open(source) as doc:
        ContentTaggerV2(pdf, doc).tag_all_pages()
        document = pdf.Root.StructTreeRoot.K[0]
        children = list(document.K)
        figure = next(child for child in children if child.S == Name.Figure)
        assert figure.K.MCID >= 0
        figure.Alt = "A gray square."
        if reordered:
            children[0], children[1] = children[1], children[0]
            document.K = Array(children)
        pdf.save(saved)
    with require_complete_pdf_scan(True):
        result = ReadingOrderVerifier().check(str(saved))
    assert len(result.issues) == 1
    assert result.compliance_score < 100
    assert result.issues[0].actual_order
    assert ("differs" if reordered else "placement") in result.issues[0].recommendation
    scan = PDFProcessor(generate_alt_text=False, validate_alt_text=False).process_pdf(
        str(saved)
    )
    assert any("reading order" in item["message"].lower() for item in scan.issues)


@pytest.mark.parametrize("replacement", ["Parent replacement", ""])
def test_actual_text_replaces_descendants_and_is_not_duplicated_by_alt(
    tmp_path, replacement
):
    path = tmp_path / "replacement.pdf"
    with pikepdf.new() as pdf:
        pdf.add_blank_page()
        child = pdf.make_indirect(
            Dictionary(Type=Name.StructElem, S=Name.Span, ActualText="Child text")
        )
        parent = pdf.make_indirect(
            Dictionary(
                Type=Name.StructElem,
                S=Name.P,
                Pg=pdf.pages[0].obj,
                ActualText=replacement,
                Alt="Alternative description",
                K=child,
            )
        )
        pdf.Root.StructTreeRoot = pdf.make_indirect(
            Dictionary(Type=Name.StructTreeRoot, K=parent)
        )
        pdf.save(path)
    with require_complete_pdf_scan(True):
        result = ReadingOrderVerifier()._get_structure_tree_order(None, str(path), 0)
    assert result == (
        [{"text": replacement, "source": "ActualText"}] if replacement else []
    )


@pytest.mark.parametrize("reordered", [False, True])
def test_saved_pdf_check_accepts_split_spans_but_rejects_reordering(
    tmp_path, reordered
):
    source = tmp_path / "source.pdf"
    saved = tmp_path / "tagged.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((40, 60), "First paragraph")
        page.insert_text((40, 100), "Second paragraph")
        doc.save(source)
    with pikepdf.open(source) as pdf:
        texts = ["First", "paragraph", "Second", "paragraph"]
        if reordered:
            texts = ["Second", "paragraph", "First", "paragraph"]
        pdf.Root.StructTreeRoot = pdf.make_indirect(
            Dictionary(
                Type=Name.StructTreeRoot,
                K=Array(
                    [
                        pdf.make_indirect(
                            Dictionary(
                                Type=Name.StructElem,
                                S=Name.P,
                                Pg=pdf.pages[0].obj,
                                ActualText=text,
                            )
                        )
                        for text in texts
                    ]
                ),
            )
        )
        pdf.save(saved)
    with require_complete_pdf_scan(True):
        result = ReadingOrderVerifier().check(str(saved))
    assert result.has_structure_tree
    assert bool(result.issues) == reordered
