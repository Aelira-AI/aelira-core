"""Reading-order structure ownership uses PDF references, not wrapper addresses."""

from contextlib import contextmanager
from types import SimpleNamespace

import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name

from src.education.pdf_checks import reading_order
from src.education.pdf_checks.completeness import (
    IncompletePDFScanError,
    require_complete_pdf_scan,
)
from src.education.pdf_checks.reading_order import ReadingOrderVerifier

pytestmark = pytest.mark.unit


def _element(pdf, **values):
    return pdf.make_indirect(Dictionary(Type=Name.StructElem, **values))


@pytest.fixture
def two_pages(tmp_path):
    with pikepdf.new() as pdf:
        pdf.add_blank_page()
        pdf.add_blank_page()
        yield pdf, tmp_path / "two-pages.pdf"


def _root(pdf, children, singleton=False):
    pdf.Root.StructTreeRoot = pdf.make_indirect(
        Dictionary(
            Type=Name.StructTreeRoot, K=children[0] if singleton else Array(children)
        )
    )


def _texts(verifier, path, page):
    return [
        b["text"] for b in verifier._get_structure_tree_order(None, str(path), page)
    ]


@pytest.mark.parametrize("singleton", [False, True])
def test_distinct_page_wrappers_keep_text_and_tables_on_their_pages(
    two_pages, monkeypatch, singleton
):
    pdf, path = two_pages
    first = _element(pdf, S=Name.P, Pg=pdf.pages[0].obj, ActualText="First page")
    second = _element(pdf, S=Name.Table, Pg=pdf.pages[1].obj, Alt="Second page table")
    container = _element(pdf, S=Name.Document, K=Array([first, second]))
    _root(pdf, [container], singleton)
    pdf.save(path)
    real_open = pikepdf.open

    @contextmanager
    def retained_page_wrappers(*args, **kwargs):
        with real_open(*args, **kwargs) as reopened:
            # Keep lookup wrappers alive so address reuse cannot hide the bug.
            pages = [SimpleNamespace(obj=page.obj) for page in reopened.pages]
            kids = reopened.Root.StructTreeRoot.K
            document = kids if singleton else kids[0]
            reference = document.K[0].Pg
            assert reference is not pages[0].obj
            assert reference.objgen == pages[0].obj.objgen
            yield SimpleNamespace(Root=reopened.Root, pages=pages)

    monkeypatch.setattr(reading_order.pikepdf, "open", retained_page_wrappers)
    verifier = ReadingOrderVerifier()
    with require_complete_pdf_scan(True):
        assert _texts(verifier, path, 0) == ["First page"]
        assert _texts(verifier, path, 1) == ["Second page table"]
        assert not verifier._page_has_tables(str(path), 0)
        assert verifier._page_has_tables(str(path), 1)


@pytest.mark.parametrize("singleton", [False, True])
def test_children_inherit_page_and_explicit_reference_overrides_it(
    two_pages, singleton
):
    pdf, path = two_pages
    inherited = _element(pdf, S=Name.P, ActualText="Inherited first page")
    override = _element(
        pdf, S=Name.Table, Pg=pdf.pages[1].obj, Alt="Explicit second page"
    )
    first = _element(
        pdf,
        S=Name.Sect,
        Pg=pdf.pages[0].obj,
        K=inherited if singleton else Array([inherited]),
    )
    second = _element(
        pdf,
        S=Name.Sect,
        Pg=pdf.pages[0].obj,
        K=override if singleton else Array([override]),
    )
    _root(pdf, [first, second])
    pdf.save(path)
    verifier = ReadingOrderVerifier()
    with require_complete_pdf_scan(True):
        assert _texts(verifier, path, 0) == ["Inherited first page"]
        assert _texts(verifier, path, 1) == ["Explicit second page"]
        assert not verifier._page_has_tables(str(path), 0)
        assert verifier._page_has_tables(str(path), 1)


@pytest.mark.parametrize("singleton", [False, True])
def test_table_inherits_page_reference(two_pages, singleton):
    pdf, path = two_pages
    table = _element(pdf, S=Name.Table, Alt="Inherited table")
    parent = _element(
        pdf, S=Name.Sect, Pg=pdf.pages[1].obj, K=table if singleton else Array([table])
    )
    _root(pdf, [parent], singleton)
    pdf.save(path)
    verifier = ReadingOrderVerifier()
    with require_complete_pdf_scan(True):
        assert _texts(verifier, path, 0) == []
        assert _texts(verifier, path, 1) == ["Inherited table"]
        assert not verifier._page_has_tables(str(path), 0)
        assert verifier._page_has_tables(str(path), 1)


@pytest.mark.parametrize(
    "invalid_kind", ["direct", "nonpage", "detached_page", "scalar"]
)
@pytest.mark.parametrize("helper", ["text", "table"])
def test_invalid_explicit_page_never_inherits_parent_and_marks_scan_incomplete(
    two_pages, invalid_kind, helper
):
    pdf, path = two_pages
    references = {
        "direct": Dictionary(Type=Name.Page),
        "nonpage": pdf.make_indirect(Dictionary(Type=Name.StructElem)),
        "detached_page": pdf.make_indirect(Dictionary(Type=Name.Page)),
        "scalar": 42,
    }
    child = _element(
        pdf, S=Name.Table, Pg=references[invalid_kind], ActualText="Unresolved text"
    )
    descendant = _element(pdf, S=Name.P, ActualText="Unresolved descendant")
    child.K = descendant
    parent = _element(pdf, S=Name.Sect, Pg=pdf.pages[0].obj, K=Array([child]))
    _root(pdf, [parent])
    pdf.save(path)
    verifier = ReadingOrderVerifier()
    with pytest.raises(IncompletePDFScanError, match="reading_order.page_reference"):
        with require_complete_pdf_scan(True):
            for page in (0, 1):
                if helper == "text":
                    assert _texts(verifier, path, page) == []
                else:
                    assert not verifier._page_has_tables(str(path), page)


def test_other_page_table_does_not_skip_comparison(two_pages, monkeypatch):
    pdf, path = two_pages
    _root(
        pdf,
        [
            _element(pdf, S=Name.P, Pg=pdf.pages[0].obj, ActualText="First page"),
            _element(pdf, S=Name.Table, Pg=pdf.pages[1].obj, ActualText="Second page"),
        ],
    )
    pdf.save(path)
    verifier = ReadingOrderVerifier()
    compared = []

    def compare(page_num, visual, structure, multi_column=False):
        compared.append((page_num, [block["text"] for block in structure]))

    monkeypatch.setattr(verifier, "_compare_reading_orders", compare)
    with require_complete_pdf_scan(True):
        verifier.check(str(path))
    assert compared == [(1, ["First page"])]
