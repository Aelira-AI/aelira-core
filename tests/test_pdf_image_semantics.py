"""Direct draw semantics remain occurrence-specific and ownership-checked."""

import io

import pikepdf
import pymupdf as fitz
import pytest
from PIL import Image

from src.education.pdf_checks.completeness import (
    IncompletePDFScanError,
    require_complete_pdf_scan,
)
from src.education.pdf_checks.image_checker import ImageAccessibilityChecker
from src.education.pdf_checks.image_checker import _displayed_image_occurrences

pytestmark = pytest.mark.unit


def _fixture(path, mode="valid", *, text=False):
    png = io.BytesIO()
    Image.new("RGB", (4, 4), "green").save(png, format="PNG")
    with fitz.open() as doc:
        page = doc.new_page()
        xref = page.insert_image(fitz.Rect(40, 40, 80, 80), stream=png.getvalue())
        page.insert_image(fitz.Rect(100, 40, 140, 80), xref=xref)
        page.insert_image(fitz.Rect(160, 40, 200, 80), xref=xref)
        if text:
            page.insert_textbox(
                fitz.Rect(40, 150, 500, 400),
                "A readable paragraph with enough text for native extraction. " * 5,
            )
        data = doc.tobytes()
    with pikepdf.open(io.BytesIO(data)) as pdf:
        page = pdf.pages[0]
        root = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.StructTreeRoot))
        docnode = pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name.StructElem, S=pikepdf.Name.Document, P=root
            )
        )
        figure = pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name.StructElem,
                S=pikepdf.Name.Figure,
                P=docnode,
                Pg=page.obj,
                Alt="Two green squares",
                K=pikepdf.Array([0, 1]),
            )
        )
        docnode.K = pikepdf.Array([figure])
        root.K = pikepdf.Array([docnode])
        root.ParentTree = pdf.make_indirect(
            pikepdf.Dictionary(Nums=pikepdf.Array([0, pikepdf.Array([figure, figure])]))
        )
        root.ParentTreeNextKey = 1
        pdf.Root.StructTreeRoot = root
        pdf.Root.MarkInfo = pikepdf.Dictionary(Marked=True)
        page.obj.StructParents = 0
        parts = []
        count = 0
        for op in pikepdf.parse_content_stream(page):
            if str(op.operator) == "Do":
                if count < 2:
                    props = pikepdf.Dictionary(MCID=0 if mode == "duplicate" else count)
                    tag = pikepdf.Name.Figure
                    if count == 0 and mode == "artifact_mcid":
                        tag = pikepdf.Name.Artifact
                    elif count == 0 and mode == "invalid_marker_tag":
                        tag = pikepdf.String("Figure")
                    parts.append(
                        pikepdf.ContentStreamInstruction(
                            [tag, props], pikepdf.Operator("BDC")
                        )
                    )
                else:
                    parts.append(
                        pikepdf.ContentStreamInstruction(
                            [pikepdf.Name.Artifact], pikepdf.Operator("BMC")
                        )
                    )
                parts.append(op)
                parts.append(
                    pikepdf.ContentStreamInstruction([], pikepdf.Operator("EMC"))
                )
                count += 1
            else:
                parts.append(op)
        page.obj.Contents = pdf.make_stream(pikepdf.unparse_content_stream(parts))
        if mode == "missing_alt":
            del figure.Alt
        elif mode == "orphan":
            docnode.K = pikepdf.Array([])
        elif mode == "wrong_parent":
            figure.P = root
        elif mode == "same_value_parent":
            duplicate_parent = pdf.make_indirect(
                pikepdf.Dictionary({key: value for key, value in docnode.items()})
            )
            assert duplicate_parent.objgen != docnode.objgen
            assert duplicate_parent == docnode
            figure.P = duplicate_parent
        elif mode == "wrong_owner":
            root.ParentTree.Nums[1][0] = docnode
        pdf.save(path)


def test_grouped_figure_and_artifact_are_resolved_per_draw(tmp_path):
    path = tmp_path / "grouped.pdf"
    _fixture(path)
    with require_complete_pdf_scan(True):
        assert ImageAccessibilityChecker().check(str(path)) == []


def test_missing_figure_alt_does_not_hide_informative_draws(tmp_path):
    path = tmp_path / "missing.pdf"
    _fixture(path, "missing_alt")
    with require_complete_pdf_scan(True):
        issues = ImageAccessibilityChecker().check(str(path))
    assert [item.image_index for item in issues] == [0, 1]
    assert len({item.image_xref for item in issues}) == 1


def _add_empty_element(path, *, role="P", empty_array=False, wrong_parent=False):
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        root = pdf.Root.StructTreeRoot
        docnode = root.K[0]
        empty = pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name.StructElem,
                S=pikepdf.Name("/" + role),
                P=root if wrong_parent else docnode,
                Pg=pdf.pages[0].obj,
            )
        )
        if empty_array:
            empty.K = pikepdf.Array([])
        docnode.K.append(empty)
        pdf.save(path)


@pytest.mark.parametrize("role", ["P", "Figure", "Sect"])
@pytest.mark.parametrize("empty_array", [False, True])
@pytest.mark.parametrize("mode", ["valid", "missing_alt"])
def test_empty_structure_elements_do_not_invalidate_image_ownership(
    tmp_path, role, empty_array, mode
):
    path = tmp_path / "empty-element.pdf"
    _fixture(path, mode)
    _add_empty_element(path, role=role, empty_array=empty_array)
    original = path.read_bytes()
    with require_complete_pdf_scan(True):
        issues = ImageAccessibilityChecker().check(str(path))
    assert [item.image_index for item in issues] == ([] if mode == "valid" else [0, 1])
    assert path.read_bytes() == original


@pytest.mark.parametrize("mode", ["orphan", "wrong_owner", "duplicate"])
def test_empty_sibling_cannot_hide_broken_image_ownership(tmp_path, mode):
    path = tmp_path / "broken-with-empty-element.pdf"
    _fixture(path, mode)
    _add_empty_element(path)
    with pytest.raises(IncompletePDFScanError):
        with require_complete_pdf_scan(True):
            ImageAccessibilityChecker().check(str(path))
    assert len(ImageAccessibilityChecker().check(str(path))) == 3


def test_empty_element_still_requires_correct_parent(tmp_path):
    path = tmp_path / "wrong-empty-parent.pdf"
    _fixture(path)
    _add_empty_element(path, wrong_parent=True)
    with pytest.raises(IncompletePDFScanError):
        with require_complete_pdf_scan(True):
            ImageAccessibilityChecker().check(str(path))


def test_empty_figure_cannot_claim_draws_through_parent_tree_alone(tmp_path):
    path = tmp_path / "empty-owner.pdf"
    _fixture(path)
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        figure = pdf.Root.StructTreeRoot.K[0].K[0]
        del figure.K
        pdf.save(path)
    with pytest.raises(IncompletePDFScanError):
        with require_complete_pdf_scan(True):
            ImageAccessibilityChecker().check(str(path))
    assert len(ImageAccessibilityChecker().check(str(path))) == 3


def test_strict_pdf_processor_completes_with_empty_paragraph_tag(tmp_path):
    from src.education.pdf_processor import PDFProcessor

    path = tmp_path / "empty-paragraph-scan.pdf"
    _fixture(path, "missing_alt", text=True)
    _add_empty_element(path)
    original = path.read_bytes()
    result = PDFProcessor(require_complete_scan=True).process_pdf(str(path))
    assert result.pages == 1
    assert result.text_extracted
    assert 0 <= result.compliance_score < 100
    assert [item.image_index for item in result.image_issues] == [0, 1]
    missing_images = [
        item for item in result.issues if item.get("rule") == "WCAG 1.1.1"
    ]
    assert len(missing_images) == 2
    assert all(
        item["message"] == "Image missing alternative text" for item in missing_images
    )
    assert path.read_bytes() == original


def test_identical_pixels_in_distinct_resources_keep_actual_draw_references(tmp_path):
    path = tmp_path / "separate-resources.pdf"
    _fixture(path)
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        page = pdf.pages[0]
        ops = list(pikepdf.parse_content_stream(page))
        draws = [index for index, op in enumerate(ops) if str(op.operator) == "Do"]
        original = page.Resources.XObject[ops[draws[0]].operands[0]]
        duplicate = pdf.make_stream(original.read_raw_bytes())
        for key, value in original.items():
            if key != "/Length":
                duplicate[key] = value
        page.Resources.XObject["/SeparatePixels"] = duplicate
        ops[draws[1]] = pikepdf.ContentStreamInstruction(
            [pikepdf.Name("/SeparatePixels")], pikepdf.Operator("Do")
        )
        page.obj.Contents = pdf.make_stream(pikepdf.unparse_content_stream(ops))
        pdf.save(path)
    with fitz.open(path) as doc:
        occurrences = _displayed_image_occurrences(doc[0], 1)
    assert occurrences[0]["image_xref"] != occurrences[1]["image_xref"]
    with require_complete_pdf_scan(True):
        assert ImageAccessibilityChecker().check(str(path)) == []


@pytest.mark.parametrize(
    "mode",
    [
        "orphan",
        "wrong_parent",
        "same_value_parent",
        "wrong_owner",
        "duplicate",
        "artifact_mcid",
        "invalid_marker_tag",
    ],
)
def test_invalid_ownership_cannot_suppress_image_failures(tmp_path, mode):
    path = tmp_path / "invalid.pdf"
    _fixture(path, mode)
    with pytest.raises(IncompletePDFScanError):
        with require_complete_pdf_scan(True):
            ImageAccessibilityChecker().check(str(path))
    assert len(ImageAccessibilityChecker().check(str(path))) == 3
