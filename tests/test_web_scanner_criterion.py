"""axe violations are stored under a success criterion that fits the column."""

import pytest

from src.db.models import ScanFix
from src.education.web_scanner import axe_criterion_from_tags


def test_success_criterion_tag_wins_over_the_category_tag():
    tags = ["cat.text-alternatives", "wcag2a", "wcag111", "section508"]
    assert axe_criterion_from_tags(tags) == "1.1.1"


def test_two_digit_success_criterion_keeps_its_digits():
    assert axe_criterion_from_tags(["cat.color", "wcag2aa", "wcag143"]) == "1.4.3"
    assert axe_criterion_from_tags(["cat.structure", "wcag21aa", "wcag1410"]) == (
        "1.4.10"
    )


def test_best_practice_rules_are_labelled_as_such():
    tags = ["cat.sensory-and-visual-cues", "best-practice"]
    assert axe_criterion_from_tags(tags) == "best-practice"


@pytest.mark.parametrize("tags", [None, [], [""], ["cat.sensory-and-visual-cues"]])
def test_rules_without_a_usable_tag_are_unknown(tags):
    assert axe_criterion_from_tags(tags) == "unknown"


def test_a_short_leading_tag_is_kept_when_nothing_better_exists():
    assert axe_criterion_from_tags(["cat.forms"]) == "cat.forms"


@pytest.mark.parametrize(
    "tags",
    [
        ["cat.text-alternatives", "wcag2a", "wcag111"],
        ["cat.sensory-and-visual-cues", "best-practice"],
        ["cat.name-role-value"],
        ["cat.sensory-and-visual-cues"],
    ],
)
def test_every_result_fits_the_stored_column(tags):
    limit = ScanFix.__table__.c.wcag_criteria.type.length
    assert len(axe_criterion_from_tags(tags)) <= limit
