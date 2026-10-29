"""Page-number corroboration costs one extraction per page, not pages x pages."""

import pymupdf as fitz
import pytest

from src.education.pdf_checks.reading_order import ReadingOrderVerifier
from src.education.remediation import source_order

pytestmark = pytest.mark.unit

PAGES = 12


@pytest.fixture
def sparse_numbers_pdf(tmp_path):
    """Long document where only the first two pages print a matching ordinal."""
    path = tmp_path / "sparse.pdf"
    with fitz.open() as pdf:
        for index in range(PAGES):
            page = pdf.new_page()
            page.insert_text((72, 100), f"Body text on page {index + 1}.")
            if index < 2:
                page.insert_text((300, 800), str(index + 1))
        pdf.save(path)
    return str(path)


@pytest.fixture
def extraction_counter(monkeypatch):
    calls = []
    original = source_order._page_number_box

    def counting(page, number):
        calls.append(number)
        return original(page, number)

    monkeypatch.setattr(source_order, "_page_number_box", counting)
    return calls


def _visual_blocks(path, verifier=None, *, shared=True):
    verifier = verifier or ReadingOrderVerifier()
    with fitz.open(path) as doc:
        ordinals = source_order.PageNumberBoxes(doc) if shared else None
        return [
            verifier._get_visual_text_order(doc[i], ordinals=ordinals)
            for i in range(len(doc))
        ]


def test_extractions_are_at_most_one_per_page(sparse_numbers_pdf, extraction_counter):
    _visual_blocks(sparse_numbers_pdf)
    assert len(extraction_counter) <= PAGES
    # Each page is extracted once, never again for another candidate page.
    assert len(extraction_counter) == len(set(extraction_counter))


def test_check_run_is_linear_in_pages(sparse_numbers_pdf, extraction_counter):
    ReadingOrderVerifier().check(sparse_numbers_pdf, max_pages=PAGES)
    assert 0 < len(extraction_counter) <= PAGES
    assert len(extraction_counter) == len(set(extraction_counter))


def test_cached_results_match_uncached_results(sparse_numbers_pdf, monkeypatch):
    cached = _visual_blocks(sparse_numbers_pdf)

    def uncached(self, page, duplicates=None, ordinals=None):
        # A fresh cache per page reproduces the old per-page recomputation.
        return original(
            self, page, duplicates, source_order.PageNumberBoxes(page.parent)
        )

    original = ReadingOrderVerifier._get_visual_text_order
    monkeypatch.setattr(ReadingOrderVerifier, "_get_visual_text_order", uncached)
    fresh = _visual_blocks(sparse_numbers_pdf)
    assert cached == fresh


def test_verified_page_ordinal_marked_when_three_pages_corroborate(tmp_path):
    path = tmp_path / "numbered.pdf"
    with fitz.open() as pdf:
        for index in range(5):
            page = pdf.new_page()
            page.insert_text((72, 100), f"Body text on page {index + 1}.")
            page.insert_text((300, 800), str(index + 1))
        pdf.save(path)
    pages = _visual_blocks(str(path))
    assert all(any(b.get("verified_page_ordinal") for b in blocks) for blocks in pages)
