"""Heading inventory work stays bounded on pages with many candidate targets."""

import pymupdf as fitz

from src.education.remediation import source_order
from test_pdf_bound_heading_inventory import scan, title_pdf


def test_page_text_is_extracted_once_for_all_targets_on_a_page(tmp_path, monkeypatch):
    path = title_pdf(tmp_path)
    original = fitz.Page.get_text
    calls = []

    def counting(self, *args, **kwargs):
        if args[:1] == ("dict",):
            calls.append(self.number)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(fitz.Page, "get_text", counting)

    found = scan(path)

    assert found.complete
    geometry_targets = [t for t in found.targets if t.bounds is not None]
    assert len(geometry_targets) >= 2
    assert calls == [0]


def test_line_matching_beyond_the_work_budget_leaves_inventory_incomplete(
    tmp_path, monkeypatch
):
    path = title_pdf(tmp_path)
    monkeypatch.setattr(source_order, "MAX_MATCH_WORK", 1)

    found = scan(path)

    assert found.complete is False
    assert found.reason == "heading_work_limit"
    assert found.has_h1 is False
