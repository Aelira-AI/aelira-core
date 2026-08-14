"""Tests for the WCAG criterion and scanner rule to IssueCategory mapper."""

import importlib.util
from pathlib import Path

# Load the category_mapper module directly to avoid triggering the heavy
# remediation __init__.py imports (docx, pikepdf, etc.) which are not
# needed for this standalone module.
_mod_path = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "education"
    / "remediation"
    / "category_mapper.py"
)
_spec = importlib.util.spec_from_file_location("category_mapper", _mod_path)
_mapper = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mapper)

wcag_criterion_to_category = _mapper.wcag_criterion_to_category
code_rule_to_category = _mapper.code_rule_to_category
impact_to_severity = _mapper.impact_to_severity
impact_to_confidence = _mapper.impact_to_confidence


class TestWcagCriterionToCategory:
    """Tests for wcag_criterion_to_category()."""

    def test_1_1_1_maps_to_alt_text(self):
        assert wcag_criterion_to_category("1.1.1") == "alt_text"

    def test_1_2_1_maps_to_alt_text(self):
        assert wcag_criterion_to_category("1.2.1") == "alt_text"

    def test_1_2_2_maps_to_alt_text(self):
        assert wcag_criterion_to_category("1.2.2") == "alt_text"

    def test_1_2_3_maps_to_alt_text(self):
        assert wcag_criterion_to_category("1.2.3") == "alt_text"

    def test_1_3_1_maps_to_structure(self):
        assert wcag_criterion_to_category("1.3.1") == "structure"

    def test_1_3_2_maps_to_reading_order(self):
        assert wcag_criterion_to_category("1.3.2") == "reading_order"

    def test_1_4_1_maps_to_color(self):
        assert wcag_criterion_to_category("1.4.1") == "color"

    def test_1_4_3_maps_to_contrast(self):
        assert wcag_criterion_to_category("1.4.3") == "contrast"

    def test_1_4_6_maps_to_contrast(self):
        assert wcag_criterion_to_category("1.4.6") == "contrast"

    def test_2_1_1_maps_to_navigation(self):
        assert wcag_criterion_to_category("2.1.1") == "navigation"

    def test_2_1_2_maps_to_navigation(self):
        assert wcag_criterion_to_category("2.1.2") == "navigation"

    def test_2_4_1_maps_to_navigation(self):
        assert wcag_criterion_to_category("2.4.1") == "navigation"

    def test_2_4_2_maps_to_title(self):
        assert wcag_criterion_to_category("2.4.2") == "title"

    def test_2_4_3_maps_to_navigation(self):
        assert wcag_criterion_to_category("2.4.3") == "navigation"

    def test_2_4_4_maps_to_link(self):
        assert wcag_criterion_to_category("2.4.4") == "link"

    def test_2_4_6_maps_to_heading(self):
        assert wcag_criterion_to_category("2.4.6") == "heading"

    def test_2_4_7_maps_to_navigation(self):
        assert wcag_criterion_to_category("2.4.7") == "navigation"

    def test_3_1_1_maps_to_language(self):
        assert wcag_criterion_to_category("3.1.1") == "language"

    def test_3_1_2_maps_to_language(self):
        assert wcag_criterion_to_category("3.1.2") == "language"

    def test_3_3_1_maps_to_form(self):
        assert wcag_criterion_to_category("3.3.1") == "form"

    def test_3_3_2_maps_to_form(self):
        assert wcag_criterion_to_category("3.3.2") == "form"

    def test_4_1_1_maps_to_structure(self):
        assert wcag_criterion_to_category("4.1.1") == "structure"

    def test_4_1_2_maps_to_aria(self):
        assert wcag_criterion_to_category("4.1.2") == "aria"

    def test_4_1_3_maps_to_aria(self):
        assert wcag_criterion_to_category("4.1.3") == "aria"

    def test_unknown_criterion_defaults_to_structure(self):
        assert wcag_criterion_to_category("99.99.99") == "structure"

    def test_empty_string_defaults_to_structure(self):
        assert wcag_criterion_to_category("") == "structure"

    def test_whitespace_stripped(self):
        assert wcag_criterion_to_category(" 1.1.1 ") == "alt_text"

    def test_none_like_invalid_defaults_to_structure(self):
        assert wcag_criterion_to_category("not-a-criterion") == "structure"


class TestCodeRuleToCategory:
    """Tests for code_rule_to_category()."""

    def test_html_image_alt(self):
        assert code_rule_to_category("html", "image-alt") == "alt_text"

    def test_html_heading_hierarchy(self):
        assert code_rule_to_category("html", "heading-hierarchy") == "heading"

    def test_html_form_label(self):
        assert code_rule_to_category("html", "form-label") == "form"

    def test_html_lang_attribute(self):
        assert code_rule_to_category("html", "lang-attribute") == "language"

    def test_html_page_title(self):
        assert code_rule_to_category("html", "page-title") == "title"

    def test_html_landmark_main(self):
        assert code_rule_to_category("html", "landmark-main") == "aria"

    def test_html_button_keyboard(self):
        assert code_rule_to_category("html", "button-keyboard") == "navigation"

    def test_css_focus_indicator(self):
        assert code_rule_to_category("css", "focus-indicator") == "navigation"

    def test_css_color_contrast(self):
        assert code_rule_to_category("css", "color-contrast") == "contrast"

    def test_css_font_size(self):
        assert code_rule_to_category("css", "font-size") == "structure"

    def test_aria_wildcard_any_rule(self):
        assert code_rule_to_category("aria", "aria-label") == "aria"

    def test_aria_wildcard_another_rule(self):
        assert code_rule_to_category("aria", "some-random-rule") == "aria"

    def test_aria_wildcard_empty_rule(self):
        assert code_rule_to_category("aria", "") == "aria"

    def test_unknown_category_defaults_to_structure(self):
        assert code_rule_to_category("unknown", "some-rule") == "structure"

    def test_unknown_rule_in_html_defaults_to_structure(self):
        assert code_rule_to_category("html", "unknown-rule") == "structure"

    def test_unknown_rule_in_css_defaults_to_structure(self):
        assert code_rule_to_category("css", "unknown-rule") == "structure"

    def test_case_insensitive_category(self):
        assert code_rule_to_category("HTML", "image-alt") == "alt_text"

    def test_case_insensitive_rule(self):
        assert code_rule_to_category("html", "IMAGE-ALT") == "alt_text"


class TestImpactToSeverity:
    """Tests for impact_to_severity()."""

    def test_critical(self):
        assert impact_to_severity("critical") == "critical"

    def test_serious(self):
        assert impact_to_severity("serious") == "high"

    def test_moderate(self):
        assert impact_to_severity("moderate") == "medium"

    def test_minor(self):
        assert impact_to_severity("minor") == "low"

    def test_unknown_defaults_to_medium(self):
        assert impact_to_severity("unknown") == "medium"

    def test_empty_defaults_to_medium(self):
        assert impact_to_severity("") == "medium"

    def test_case_insensitive(self):
        assert impact_to_severity("Critical") == "critical"
        assert impact_to_severity("SERIOUS") == "high"


class TestImpactToConfidence:
    """Tests for impact_to_confidence()."""

    def test_critical(self):
        assert impact_to_confidence("critical") == 0.9

    def test_serious(self):
        assert impact_to_confidence("serious") == 0.8

    def test_moderate(self):
        assert impact_to_confidence("moderate") == 0.7

    def test_minor(self):
        assert impact_to_confidence("minor") == 0.6

    def test_unknown_defaults_to_0_7(self):
        assert impact_to_confidence("unknown") == 0.7

    def test_empty_defaults_to_0_7(self):
        assert impact_to_confidence("") == 0.7

    def test_case_insensitive(self):
        assert impact_to_confidence("Critical") == 0.9
        assert impact_to_confidence("MINOR") == 0.6
