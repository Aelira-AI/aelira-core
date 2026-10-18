"""Web and code scans must give each review-queue row a stable identity."""

import re

from src.api.education.web_scan_routes import (
    _add_review_queue_rows,
    _review_queue_row,
)


class _Session:
    def __init__(self):
        self.added = []

    def add(self, row):
        self.added.append(row)


def _row(**changes):
    values = {
        "issue_id": "web-1.1.1-abc",
        "category": "alt_text",
        "severity": "high",
        "description": "Images need alternative text",
        "location": "https://example.org/ | img",
        "original_content": '<img src="a.svg">',
        "fixed_content": "Describe what the picture shows.",
        "ai_generated": False,
        "confidence": 0.9,
        "wcag_criteria": "1.1.1",
    }
    values.update(changes)
    return _review_queue_row("scan-1", **values)


def test_row_has_the_identity_the_review_queue_requires():
    row = _row()
    assert re.fullmatch(r"[a-f0-9]{64}", row.occurrence_key)
    assert re.fullmatch(r"[a-f0-9]{64}", row.review_digest)
    assert row.review_status == "pending"
    assert row.needs_review is True
    assert row.wcag_criteria == "1.1.1"
    assert row.fix_method == "heuristic"


def test_a_model_written_fix_is_marked_as_ai_without_naming_a_provider():
    row = _row(ai_generated=True)
    assert row.fix_method == "ai"
    assert row.model_used is None
    assert row.provider_used is None


def test_same_issue_in_different_places_keeps_separate_rows():
    first = _row(location="https://example.org/ | img.a")
    second = _row(location="https://example.org/ | img.b")
    session = _Session()
    assert _add_review_queue_rows(session, [first, second]) == 2
    assert first.occurrence_key != second.occurrence_key


def test_a_repeat_of_the_same_issue_in_the_same_place_is_stored_once():
    session = _Session()
    assert _add_review_queue_rows(session, [_row(), _row()]) == 1
    assert len(session.added) == 1
