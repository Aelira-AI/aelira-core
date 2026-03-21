"""
Human-Friendly WCAG Descriptions

Maps WCAG criteria and rule IDs to plain-language descriptions that
non-technical users can understand. These descriptions explain:
- human_issue: What the accessibility problem is (in plain English)
- human_fixed: What was done to fix it (confirmation message)

Usage:
    from src.ai.wcag_human_descriptions import (
        get_human_description,
        WCAG_HUMAN_DESCRIPTIONS,
        RULE_HUMAN_DESCRIPTIONS
    )

    # Get description by WCAG criterion (e.g., "1.1.1")
    desc = get_human_description("1.1.1")
    # Returns: {"issue": "...", "fixed": "..."}

    # Get description by rule ID (e.g., "image-alt")
    desc = get_human_description(rule_id="image-alt")
"""

from typing import Dict, Optional, TypedDict


class HumanDescription(TypedDict):
    issue: str
    fixed: str


# Human-friendly descriptions indexed by WCAG criterion number
# Format: "X.Y.Z" -> {"issue": "...", "fixed": "..."}
WCAG_HUMAN_DESCRIPTIONS: Dict[str, HumanDescription] = {
    # Principle 1: Perceivable
    # Guideline 1.1: Text Alternatives
    "1.1.1": {
        "issue": "Images need descriptions for screen readers",
        "fixed": "Images now have descriptions for screen readers",
    },
    # Guideline 1.2: Time-based Media
    "1.2.1": {
        "issue": "Audio or video needs text alternatives",
        "fixed": "Audio/video now has text alternatives",
    },
    "1.2.2": {
        "issue": "Videos need captions for deaf users",
        "fixed": "Videos now have captions",
    },
    "1.2.3": {
        "issue": "Videos need audio descriptions for blind users",
        "fixed": "Videos now have audio descriptions",
    },
    "1.2.4": {
        "issue": "Live videos need real-time captions",
        "fixed": "Live videos now have real-time captions",
    },
    "1.2.5": {
        "issue": "Pre-recorded videos need audio descriptions",
        "fixed": "Videos now have audio descriptions",
    },
    # Guideline 1.3: Adaptable
    "1.3.1": {
        "issue": "Document structure isn't clear to screen readers",
        "fixed": "Screen readers can now follow the content correctly",
    },
    "1.3.2": {
        "issue": "Reading order may be confusing for screen readers",
        "fixed": "Reading order is now logical for screen readers",
    },
    "1.3.3": {
        "issue": "Instructions rely only on visual cues",
        "fixed": "Instructions no longer depend on visual appearance alone",
    },
    "1.3.4": {
        "issue": "Content requires a specific screen orientation",
        "fixed": "Content works in any screen orientation",
    },
    "1.3.5": {
        "issue": "Form fields don't indicate their purpose",
        "fixed": "Form fields now indicate their purpose for auto-fill",
    },
    # Guideline 1.4: Distinguishable
    "1.4.1": {
        "issue": "Information is conveyed by color alone",
        "fixed": "Color is no longer the only way to convey information",
    },
    "1.4.2": {
        "issue": "Audio plays automatically without controls",
        "fixed": "Audio can now be paused or stopped",
    },
    "1.4.3": {
        "issue": "Text is hard to read due to low contrast",
        "fixed": "Text now has sufficient contrast to read easily",
    },
    "1.4.4": {
        "issue": "Text can't be resized without losing functionality",
        "fixed": "Text can now be resized up to 200%",
    },
    "1.4.5": {
        "issue": "Text is shown as an image instead of real text",
        "fixed": "Real text is used instead of images of text",
    },
    "1.4.6": {
        "issue": "Text contrast doesn't meet enhanced standards",
        "fixed": "Text now meets enhanced contrast standards",
    },
    "1.4.10": {
        "issue": "Content requires horizontal scrolling on mobile",
        "fixed": "Content reflows without horizontal scrolling",
    },
    "1.4.11": {
        "issue": "UI elements have insufficient contrast",
        "fixed": "UI elements now have sufficient contrast",
    },
    "1.4.12": {
        "issue": "Text spacing can't be adjusted",
        "fixed": "Text spacing can now be customized",
    },
    "1.4.13": {
        "issue": "Hover content disappears too quickly or can't be dismissed",
        "fixed": "Hover content is now accessible and dismissible",
    },
    # Principle 2: Operable
    # Guideline 2.1: Keyboard Accessible
    "2.1.1": {
        "issue": "Some features can't be used with a keyboard",
        "fixed": "All features can now be used with a keyboard",
    },
    "2.1.2": {
        "issue": "Keyboard users can get trapped in a section",
        "fixed": "Keyboard users can now navigate freely",
    },
    "2.1.4": {
        "issue": "Single-key shortcuts may cause problems for some users",
        "fixed": "Single-key shortcuts can now be customized",
    },
    # Guideline 2.2: Enough Time
    "2.2.1": {
        "issue": "Time limits may not give users enough time",
        "fixed": "Users can now adjust or extend time limits",
    },
    "2.2.2": {
        "issue": "Moving content can't be paused",
        "fixed": "Moving content can now be paused",
    },
    # Guideline 2.3: Seizures
    "2.3.1": {
        "issue": "Flashing content could trigger seizures",
        "fixed": "Flashing content has been removed or reduced",
    },
    # Guideline 2.4: Navigable
    "2.4.1": {
        "issue": "No way to skip repetitive content",
        "fixed": "Users can now skip to main content",
    },
    "2.4.2": {
        "issue": "Page doesn't have a descriptive title",
        "fixed": "Page now has a descriptive title",
    },
    "2.4.3": {
        "issue": "Focus order is confusing or illogical",
        "fixed": "Focus order now follows a logical sequence",
    },
    "2.4.4": {
        "issue": "Link text doesn't describe where it goes",
        "fixed": "Links now clearly describe their destination",
    },
    "2.4.5": {
        "issue": "No alternative ways to find pages",
        "fixed": "Multiple ways to find pages are now available",
    },
    "2.4.6": {
        "issue": "Headings or labels aren't descriptive",
        "fixed": "Headings and labels are now descriptive",
    },
    "2.4.7": {
        "issue": "Keyboard focus isn't visible",
        "fixed": "Keyboard focus is now clearly visible",
    },
    "2.4.11": {
        "issue": "Focused elements are hidden behind other content",
        "fixed": "Focused elements are now always visible",
    },
    # Guideline 2.5: Input Modalities
    "2.5.1": {
        "issue": "Complex gestures are required without alternatives",
        "fixed": "Simple alternatives are now available for gestures",
    },
    "2.5.2": {
        "issue": "Touch actions can't be cancelled",
        "fixed": "Touch actions can now be cancelled",
    },
    "2.5.3": {
        "issue": "Visible labels don't match accessible names",
        "fixed": "Labels and accessible names now match",
    },
    "2.5.4": {
        "issue": "Motion-based actions have no alternatives",
        "fixed": "Motion actions now have button alternatives",
    },
    # Principle 3: Understandable
    # Guideline 3.1: Readable
    "3.1.1": {
        "issue": "Page language isn't specified",
        "fixed": "Page language is now specified",
    },
    "3.1.2": {
        "issue": "Language changes in content aren't marked",
        "fixed": "Language changes are now properly marked",
    },
    # Guideline 3.2: Predictable
    "3.2.1": {
        "issue": "Focusing on elements causes unexpected changes",
        "fixed": "Focus no longer causes unexpected changes",
    },
    "3.2.2": {
        "issue": "Changing settings causes unexpected behavior",
        "fixed": "Settings changes are now predictable",
    },
    "3.2.3": {
        "issue": "Navigation isn't consistent across pages",
        "fixed": "Navigation is now consistent",
    },
    "3.2.4": {
        "issue": "Similar items aren't identified consistently",
        "fixed": "Similar items are now identified consistently",
    },
    # Guideline 3.3: Input Assistance
    "3.3.1": {
        "issue": "Errors aren't clearly identified",
        "fixed": "Errors are now clearly identified",
    },
    "3.3.2": {
        "issue": "Form fields lack instructions",
        "fixed": "Form fields now have clear instructions",
    },
    "3.3.3": {
        "issue": "No suggestions provided for fixing errors",
        "fixed": "Helpful suggestions are now provided for errors",
    },
    "3.3.4": {
        "issue": "Legal or financial submissions can't be reviewed",
        "fixed": "Submissions can now be reviewed before finalizing",
    },
    # Principle 4: Robust
    # Guideline 4.1: Compatible
    "4.1.1": {
        "issue": "HTML code has errors that break assistive technology",
        "fixed": "HTML code is now valid and compatible",
    },
    "4.1.2": {
        "issue": "Interactive elements lack proper labels or roles",
        "fixed": "Interactive elements now have proper labels and roles",
    },
    "4.1.3": {
        "issue": "Status messages aren't announced to screen readers",
        "fixed": "Status messages are now announced automatically",
    },
}


# Human-friendly descriptions indexed by axe-core rule ID
# These map to specific rules that may not have a direct 1:1 WCAG mapping
RULE_HUMAN_DESCRIPTIONS: Dict[str, HumanDescription] = {
    # Images
    "image-alt": {
        "issue": "Images need descriptions for screen readers",
        "fixed": "Images now have descriptions for screen readers",
    },
    "input-image-alt": {
        "issue": "Image buttons need descriptions",
        "fixed": "Image buttons now have descriptions",
    },
    "image-redundant-alt": {
        "issue": "Image description repeats surrounding text",
        "fixed": "Image description is now unique and meaningful",
    },
    "object-alt": {
        "issue": "Embedded objects need text alternatives",
        "fixed": "Embedded objects now have text alternatives",
    },
    "svg-img-alt": {
        "issue": "SVG images need accessible names",
        "fixed": "SVG images now have accessible names",
    },
    # Buttons and Links
    "button-name": {
        "issue": "Buttons need labels for screen readers",
        "fixed": "Buttons now have labels for screen readers",
    },
    "link-name": {
        "issue": "Links need descriptive text",
        "fixed": "Links now have descriptive text",
    },
    "link-in-text-block": {
        "issue": "Links in text aren't distinguishable without color",
        "fixed": "Links are now distinguishable without relying on color",
    },
    "identical-links-same-purpose": {
        "issue": "Links with same text go to different places",
        "fixed": "Link text now matches destinations",
    },
    # Forms
    "label": {
        "issue": "Form fields need labels",
        "fixed": "Form fields now have labels",
    },
    "label-title-only": {
        "issue": "Form labels use only title attribute",
        "fixed": "Form fields now have proper visible labels",
    },
    "select-name": {
        "issue": "Dropdown menus need labels",
        "fixed": "Dropdown menus now have labels",
    },
    "input-button-name": {
        "issue": "Submit buttons need descriptive text",
        "fixed": "Submit buttons now have descriptive text",
    },
    "autocomplete-valid": {
        "issue": "Autocomplete attribute is invalid",
        "fixed": "Autocomplete is now configured correctly",
    },
    # Structure and Semantics
    "heading-order": {
        "issue": "Headings skip levels (e.g., h1 to h3)",
        "fixed": "Headings now follow a logical order",
    },
    "empty-heading": {
        "issue": "Headings are empty",
        "fixed": "Headings now have content",
    },
    "document-title": {
        "issue": "Page is missing a title",
        "fixed": "Page now has a descriptive title",
    },
    "html-has-lang": {
        "issue": "Page language isn't specified",
        "fixed": "Page language is now specified",
    },
    "html-lang-valid": {
        "issue": "Page language code is invalid",
        "fixed": "Page language code is now valid",
    },
    "valid-lang": {
        "issue": "Language code is invalid",
        "fixed": "Language code is now valid",
    },
    "region": {
        "issue": "Content isn't organized in landmark regions",
        "fixed": "Content is now organized in accessible regions",
    },
    "landmark-one-main": {
        "issue": "Page needs exactly one main landmark",
        "fixed": "Page now has one main content area",
    },
    "landmark-unique": {
        "issue": "Landmarks need unique labels",
        "fixed": "Landmarks now have unique labels",
    },
    "bypass": {
        "issue": "No way to skip to main content",
        "fixed": "Users can now skip to main content",
    },
    # Tables
    "table-fake-caption": {
        "issue": "Table caption isn't properly marked",
        "fixed": "Table caption is now properly marked",
    },
    "td-headers-attr": {
        "issue": "Table cell headers are incorrectly associated",
        "fixed": "Table cell headers are now correctly associated",
    },
    "th-has-data-cells": {
        "issue": "Table headers don't have data cells",
        "fixed": "Table headers now have associated data cells",
    },
    "scope-attr-valid": {
        "issue": "Table scope attribute is invalid",
        "fixed": "Table scope is now valid",
    },
    # Color and Contrast
    "color-contrast": {
        "issue": "Text is hard to read due to low contrast",
        "fixed": "Text now has sufficient contrast to read easily",
    },
    "color-contrast-enhanced": {
        "issue": "Text contrast doesn't meet enhanced standards",
        "fixed": "Text now meets enhanced contrast standards",
    },
    # ARIA
    "aria-allowed-attr": {
        "issue": "ARIA attributes are incorrectly used",
        "fixed": "ARIA attributes are now used correctly",
    },
    "aria-required-attr": {
        "issue": "Required ARIA attributes are missing",
        "fixed": "Required ARIA attributes are now present",
    },
    "aria-valid-attr": {
        "issue": "ARIA attribute names are invalid",
        "fixed": "ARIA attributes are now valid",
    },
    "aria-valid-attr-value": {
        "issue": "ARIA attribute values are invalid",
        "fixed": "ARIA attribute values are now valid",
    },
    "aria-roles": {
        "issue": "ARIA roles are invalid",
        "fixed": "ARIA roles are now valid",
    },
    "aria-hidden-focus": {
        "issue": "Hidden elements can still receive focus",
        "fixed": "Hidden elements no longer receive focus",
    },
    "aria-hidden-body": {
        "issue": "Page body is hidden from screen readers",
        "fixed": "Page body is now accessible to screen readers",
    },
    "aria-input-field-name": {
        "issue": "ARIA input fields need accessible names",
        "fixed": "ARIA input fields now have accessible names",
    },
    "aria-toggle-field-name": {
        "issue": "Toggle controls need accessible names",
        "fixed": "Toggle controls now have accessible names",
    },
    # Media
    "video-caption": {
        "issue": "Videos need captions",
        "fixed": "Videos now have captions",
    },
    "audio-caption": {
        "issue": "Audio content needs captions or transcripts",
        "fixed": "Audio content now has captions or transcripts",
    },
    # Focus and Keyboard
    "focus-order-semantics": {
        "issue": "Focus order is illogical",
        "fixed": "Focus order is now logical",
    },
    "tabindex": {
        "issue": "Tab order uses positive values",
        "fixed": "Tab order now follows document structure",
    },
    "scrollable-region-focusable": {
        "issue": "Scrollable areas can't be focused with keyboard",
        "fixed": "Scrollable areas can now be focused with keyboard",
    },
    # Other
    "meta-refresh": {
        "issue": "Page refreshes automatically",
        "fixed": "Page no longer refreshes automatically",
    },
    "meta-viewport": {
        "issue": "Zooming is disabled or limited",
        "fixed": "Users can now zoom the page",
    },
    "frame-title": {
        "issue": "Frames need descriptive titles",
        "fixed": "Frames now have descriptive titles",
    },
    "frame-tested": {
        "issue": "Frame content wasn't tested",
        "fixed": "Frame content is now tested",
    },
    "duplicate-id": {
        "issue": "Multiple elements have the same ID",
        "fixed": "Element IDs are now unique",
    },
    "duplicate-id-active": {
        "issue": "Active elements have duplicate IDs",
        "fixed": "Active element IDs are now unique",
    },
    "duplicate-id-aria": {
        "issue": "ARIA references have duplicate IDs",
        "fixed": "ARIA reference IDs are now unique",
    },
    # PDF-specific (common in document remediation)
    "pdf-lang": {
        "issue": "PDF document language isn't specified",
        "fixed": "PDF document language is now specified",
    },
    "pdf-title": {
        "issue": "PDF document title is missing",
        "fixed": "PDF document now has a title",
    },
    "pdf-alt-text": {
        "issue": "PDF images are missing descriptions",
        "fixed": "PDF images now have descriptions",
    },
    "pdf-headings": {
        "issue": "PDF headings aren't properly tagged",
        "fixed": "PDF headings are now properly tagged",
    },
    "pdf-reading-order": {
        "issue": "PDF reading order is incorrect",
        "fixed": "PDF reading order is now correct",
    },
    "pdf-tables": {
        "issue": "PDF tables aren't properly structured",
        "fixed": "PDF tables are now properly structured",
    },
    "pdf-lists": {
        "issue": "PDF lists aren't properly tagged",
        "fixed": "PDF lists are now properly tagged",
    },
    "pdf-bookmarks": {
        "issue": "PDF lacks navigation bookmarks",
        "fixed": "PDF now has navigation bookmarks",
    },
    "pdf-tagged": {
        "issue": "PDF isn't tagged for accessibility",
        "fixed": "PDF is now tagged for accessibility",
    },
}


def get_human_description(
    wcag_criterion: Optional[str] = None,
    rule_id: Optional[str] = None,
    is_fixed: bool = False,
) -> Optional[str]:
    """
    Get human-friendly description for a WCAG criterion or rule.

    Args:
        wcag_criterion: WCAG criterion number (e.g., "1.1.1", "WCAG 1.1.1")
        rule_id: axe-core rule ID (e.g., "image-alt", "button-name")
        is_fixed: If True, return the "fixed" message; otherwise return "issue"

    Returns:
        Human-friendly description string, or None if not found

    Examples:
        >>> get_human_description(wcag_criterion="1.1.1")
        "Images need descriptions for screen readers"

        >>> get_human_description(rule_id="button-name", is_fixed=True)
        "Buttons now have labels for screen readers"
    """
    desc = None

    # Try rule_id first (more specific)
    if rule_id:
        desc = RULE_HUMAN_DESCRIPTIONS.get(rule_id)

    # Fall back to WCAG criterion
    if not desc and wcag_criterion:
        # Normalize criterion (remove "WCAG " prefix if present)
        normalized = wcag_criterion.replace("WCAG ", "").replace("wcag ", "").strip()
        desc = WCAG_HUMAN_DESCRIPTIONS.get(normalized)

    if not desc:
        return None

    return desc["fixed"] if is_fixed else desc["issue"]


def get_human_description_pair(
    wcag_criterion: Optional[str] = None, rule_id: Optional[str] = None
) -> Optional[HumanDescription]:
    """
    Get both issue and fixed descriptions as a dictionary.

    Args:
        wcag_criterion: WCAG criterion number
        rule_id: axe-core rule ID

    Returns:
        Dictionary with "issue" and "fixed" keys, or None if not found
    """
    # Try rule_id first (more specific)
    if rule_id:
        desc = RULE_HUMAN_DESCRIPTIONS.get(rule_id)
        if desc:
            return desc

    # Fall back to WCAG criterion
    if wcag_criterion:
        normalized = wcag_criterion.replace("WCAG ", "").replace("wcag ", "").strip()
        return WCAG_HUMAN_DESCRIPTIONS.get(normalized)

    return None
