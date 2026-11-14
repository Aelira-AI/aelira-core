"""OBJR structure children (ISO 32000-1 14.12.3.4) on the scan path.

A resolvable OBJR contributes no reading-order text of its own and must not
fail the completeness gate; an unresolvable one keeps failing closed. The
remediation refusals (source_order, snapshot) are asserted elsewhere and are
deliberately untouched by this behaviour.
"""

import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name

from src.education.pdf_checks.completeness import (
    IncompletePDFScanError,
    require_complete_pdf_scan,
)
from src.education.pdf_checks.reading_order import ReadingOrderVerifier

pytestmark = pytest.mark.unit


def tagged_pdf(tmp_path, order=(0, 1), *, pages=1):
    pdf = pikepdf.new()
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    pdf.Root.StructTreeRoot = root
    children, numbers = [], []
    for page_index in range(pages):
        page = pdf.add_blank_page(page_size=(400, 400))
        page.obj.StructParents = page_index
        page.obj.Resources = Dictionary(
            Font=Dictionary(
                F1=Dictionary(
                    Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica
                )
            )
        )
        labels = [f"First page {page_index}", f"Second page {page_index}"]
        page.obj.Contents = pdf.make_stream(
            "\n".join(
                f"/P <</MCID {i}>> BDC BT /F1 12 Tf 30 {330-i*60} Td ({labels[i]}) Tj ET EMC"
                for i in (1, 0)
            ).encode()
        )
        owners = []
        for i in range(2):
            element = pdf.make_indirect(
                Dictionary(Type=Name.StructElem, S=Name.P, P=root)
            )
            element.Pg = page.obj
            element.K = Dictionary(Type=Name.MCR, MCID=i)
            owners.append(element)
        children.extend(owners[i] for i in order)
        numbers.extend([page_index, Array(owners)])
    root.K = Array(children)
    root.ParentTree = Dictionary(Nums=Array(numbers))
    return pdf, tmp_path / "marked.pdf"


def annotation(pdf, page):
    annot = pdf.make_indirect(
        Dictionary(
            Type=Name.Annot,
            Subtype=Name.Link,
            Rect=Array([30, 330, 200, 345]),
        )
    )
    page.obj.Annots = Array([annot])
    return annot


def attach_objr(pdf, obj_value, *, keep=None):
    root = pdf.Root.StructTreeRoot
    target = root.K[0]
    objr = Dictionary(Type=Name.OBJR, R=Name.R, P=target, Obj=obj_value)
    if "/K" in target:
        target.K = Array([target.K, objr])
    else:
        target.K = Array([objr])
    return objr


def texts(path, page=0):
    return [
        block["text"]
        for block in ReadingOrderVerifier()._get_structure_tree_order(
            None, str(path), page
        )
    ]


def test_objr_with_resolvable_annotation_scans_complete_and_preserves_order(tmp_path):
    baseline, baseline_path = tagged_pdf(tmp_path)
    baseline.save(baseline_path)
    pdf, path = tagged_pdf(tmp_path)
    attach_objr(pdf, annotation(pdf, pdf.pages[0]))
    pdf.save(path)
    with require_complete_pdf_scan(True):
        ReadingOrderVerifier().check(str(path))
    assert texts(path) == texts(baseline_path)


def test_objr_hiding_page_stream_content_fails_closed(tmp_path):
    # A page-stream MCID (no /Stm) under an OBJR places content whose order
    # cannot be interpreted: the scan keeps failing with object_reference.
    pdf, path = tagged_pdf(tmp_path)
    target = pdf.Root.StructTreeRoot.K[0]
    hidden_mcr = target.K
    objr = attach_objr(pdf, annotation(pdf, pdf.pages[0]))
    objr.K = Array([hidden_mcr])
    pdf.save(path)
    with pytest.raises(IncompletePDFScanError, match="reading_order.object_reference"):
        with require_complete_pdf_scan(True):
            ReadingOrderVerifier().check(str(path))
    result = ReadingOrderVerifier().check(str(path))
    assert result.issues and result.compliance_score < 100


def test_objr_stream_owned_marked_content_skips_cleanly(tmp_path):
    # The Word/hyperlink shape: /K children owned by an annotation
    # appearance stream. Page coverage is untouched and the scan completes.
    baseline, baseline_path = tagged_pdf(tmp_path)
    baseline.save(baseline_path)
    pdf, path = tagged_pdf(tmp_path)
    objr = attach_objr(pdf, annotation(pdf, pdf.pages[0]))
    objr.K = Array([Dictionary(Type=Name.MCR, MCID=0, Stm=pdf.pages[0].obj.Contents)])
    pdf.save(path)
    with require_complete_pdf_scan(True):
        ReadingOrderVerifier().check(str(path))
    assert texts(path) == texts(baseline_path)


@pytest.mark.parametrize("obj_value", [3, "ObjRef"], ids=["int", "string"])
def test_objr_without_resolvable_object_fails_closed(tmp_path, obj_value):
    pdf, path = tagged_pdf(tmp_path)
    attach_objr(pdf, obj_value)
    pdf.save(path)
    with pytest.raises(IncompletePDFScanError, match="reading_order.object_reference"):
        with require_complete_pdf_scan(True):
            ReadingOrderVerifier().check(str(path))
    result = ReadingOrderVerifier().check(str(path))
    assert result.issues and result.compliance_score < 100


def test_objr_without_object_key_fails_closed(tmp_path):
    pdf, path = tagged_pdf(tmp_path)
    objr = attach_objr(pdf, annotation(pdf, pdf.pages[0]))
    del objr["/Obj"]
    pdf.save(path)
    with pytest.raises(IncompletePDFScanError, match="reading_order.object_reference"):
        with require_complete_pdf_scan(True):
            ReadingOrderVerifier().check(str(path))
