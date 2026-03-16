"""
WCAG Knowledge Base Seed Data

This module contains seed data for the WCAG guidelines knowledge base.
Based on axe-core rules with severity classification criteria.

To add more rules, see:
- axe-core rules: https://github.com/dequelabs/axe-core/blob/develop/doc/rule-descriptions.md
- WCAG 2.2: https://www.w3.org/TR/WCAG22/

Human-friendly descriptions are maintained in wcag_human_descriptions.py
and populated via scripts/populate_human_descriptions.py
"""

from .wcag_human_descriptions import get_human_description_pair


def _add_human_descriptions(guidelines: list) -> list:
    """Add human_issue and human_fixed fields to guidelines."""
    for guideline in guidelines:
        desc = get_human_description_pair(
            wcag_criterion=guideline.get("wcag_criterion"),
            rule_id=guideline.get("rule_id"),
        )
        if desc:
            guideline["human_issue"] = desc["issue"]
            guideline["human_fixed"] = desc["fixed"]
        else:
            guideline["human_issue"] = None
            guideline["human_fixed"] = None
    return guidelines


_WCAG_GUIDELINES_RAW = [
    {
        "rule_id": "button-name",
        "wcag_criterion": "4.1.2",
        "wcag_level": "A",
        "title": "Buttons must have discernible text",
        "description": "Ensures buttons have discernible text. Buttons without text labels can be confusing or impossible to use for people using screen readers or voice control software.",
        "principle": "Robust",
        "guideline": "4.1 Compatible",
        "severity_criteria": {
            "critical": "Button is a primary action (submit, purchase, confirm, delete, login) AND has no accessible name",
            "high": "Button is a secondary action (cancel, back, next, more, close) AND has no accessible name",
            "medium": "Button is a tertiary action (info, help, expand) AND has no accessible name OR button has aria-label but text content also exists (redundant)",
            "low": "Button has accessible name but could be more descriptive",
        },
        "business_impact_template": "Users relying on assistive technology cannot activate {button_type} button, preventing them from {action}. This violates WCAG 2.2 Level A (4.1.2 Name, Role, Value) and may result in ADA/Section 508 non-compliance. Potential legal risk: High.",
        "technical_impact": "Screen readers announce 'button' without a label, voice control users cannot activate the button by name, automated testing tools flag as critical violation. WCAG 2.2 Level A failure.",
        "fix_examples": [
            {
                "before": "<button class='icon-button'><svg>...</svg></button>",
                "after": "<button aria-label='Submit form' class='icon-button'><svg>...</svg></button>",
                "explanation": "Add aria-label to provide accessible name for icon-only button",
            },
            {
                "before": "<button><i class='fa fa-trash'></i></button>",
                "after": "<button><i class='fa fa-trash' aria-hidden='true'></i>Delete</button>",
                "explanation": "Add visible text label and hide decorative icon from screen readers",
            },
        ],
        "best_practices": [
            "Prefer visible text labels over aria-label when possible",
            "Use aria-label for icon-only buttons where text label would be redundant",
            "Ensure button labels describe the action clearly (avoid 'Click here')",
            "Test with screen readers (NVDA, JAWS, VoiceOver) to verify announcement",
        ],
        "tags": ["wcag2a", "wcag412", "section508", "ada", "axe-core"],
        "act_rule_ids": ["97a4e1"],
        "related_rules": ["link-name", "input-button-name", "aria-command-name"],
    },
    {
        "rule_id": "color-contrast",
        "wcag_criterion": "1.4.3",
        "wcag_level": "AA",
        "title": "Elements must meet minimum color contrast ratio thresholds",
        "description": "Ensures the contrast between foreground and background colors meets WCAG 2.2 AA minimum thresholds (4.5:1 for normal text, 3:1 for large text). Low contrast makes content difficult or impossible to read for people with low vision or color blindness.",
        "principle": "Perceivable",
        "guideline": "1.4 Distinguishable",
        "severity_criteria": {
            "critical": "Contrast ratio < 2:1 (extremely low, nearly invisible) OR critical text (error messages, warnings, CTAs) with contrast < 3:1",
            "high": "Normal text with contrast < 4.5:1 OR large text with contrast < 3:1",
            "medium": "Text has 4:1-4.4:1 contrast (passes WCAG 2.0 but fails 2.2) OR non-text elements (icons, buttons) with contrast < 3:1",
            "low": "Decorative text or disabled elements with low contrast (not required to meet threshold)",
        },
        "business_impact_template": "Content with insufficient contrast is difficult or impossible to read for {percent}% of users with visual impairments. This violates WCAG 2.2 Level AA (1.4.3 Contrast Minimum) and may result in ADA lawsuits. Potential legal risk: High.",
        "technical_impact": "Automated testing tools flag as WCAG 2.2 AA violation. Users with low vision, color blindness, or viewing content in bright sunlight cannot read text. May fail Section 508 requirements.",
        "fix_examples": [
            {
                "before": "color: #777; background: #fff; /* 4.47:1 - fails */",
                "after": "color: #595959; background: #fff; /* 7:1 - passes */",
                "explanation": "Darken text color to meet 4.5:1 threshold for normal text",
            },
            {
                "before": "color: #999; background: #fff; /* 2.85:1 - fails for large text */",
                "after": "color: #767676; background: #fff; /* 4.54:1 - passes */",
                "explanation": "Ensure large text (18pt+) meets 3:1 minimum threshold",
            },
        ],
        "best_practices": [
            "Use contrast checking tools (WebAIM, Axe DevTools) during design",
            "Test in browser DevTools (Chrome Lighthouse, Firefox Accessibility Inspector)",
            "Consider users viewing content in bright sunlight (aim for 7:1 AAA)",
            "Avoid relying on color alone to convey information",
        ],
        "tags": ["wcag2aa", "wcag143", "section508", "ada", "axe-core"],
        "act_rule_ids": ["afw4f7"],
        "related_rules": ["link-contrast", "color-contrast-enhanced"],
    },
    {
        "rule_id": "image-alt",
        "wcag_criterion": "1.1.1",
        "wcag_level": "A",
        "title": "Images must have alternate text",
        "description": "Ensures <img> elements have alternate text. Screen readers cannot interpret images without alt text, making visual content inaccessible to blind users.",
        "principle": "Perceivable",
        "guideline": "1.1 Text Alternatives",
        "severity_criteria": {
            "critical": "Content images (photos, diagrams, charts) with no alt text OR alt='' (empty) on informative images",
            "high": "Functional images (buttons, links) with no alt text OR alt text does not describe function",
            "medium": "Alt text exists but is non-descriptive (e.g., 'image', 'photo', filename) OR decorative images with non-empty alt text",
            "low": "Alt text is adequate but could be more concise or descriptive",
        },
        "business_impact_template": "Screen reader users cannot access visual content, excluding blind users from {content_type}. This violates WCAG 2.2 Level A (1.1.1 Non-text Content) and creates high ADA lawsuit risk. Many lawsuits cite missing alt text as primary violation.",
        "technical_impact": "Screen readers skip image or announce unhelpful text ('image', filename). Voice control users cannot target functional images. Automated scans flag as critical WCAG 2.2 Level A failure.",
        "fix_examples": [
            {
                "before": "<img src='chart.png'>",
                "after": "<img src='chart.png' alt='Bar chart showing 45% increase in sales from Q1 to Q2 2025'>",
                "explanation": "Add descriptive alt text explaining chart content and key takeaways",
            },
            {
                "before": "<img src='logo.png' alt=''>",
                "after": "<img src='logo.png' alt='Aelira - Accessibility Testing Platform'>",
                "explanation": "Functional logo image needs descriptive alt text (not decorative)",
            },
            {
                "before": "<img src='decorative-border.png' alt='decorative border'>",
                "after": "<img src='decorative-border.png' alt='' role='presentation'>",
                "explanation": "Decorative images should have empty alt and presentation role",
            },
        ],
        "best_practices": [
            "Describe the purpose/content, not the appearance (avoid 'image of...')",
            "Keep alt text concise (under 150 characters when possible)",
            "For complex images, use longdesc or aria-describedby for detailed description",
            "Use alt='' (empty) only for purely decorative images",
            "For functional images (buttons/links), describe the action, not the image",
        ],
        "tags": [
            "wcag2a",
            "wcag111",
            "section508",
            "ada",
            "axe-core",
            "cat.text-alternatives",
        ],
        "act_rule_ids": ["23a2a8"],
        "related_rules": ["image-redundant-alt", "input-image-alt", "object-alt"],
    },
    {
        "rule_id": "link-name",
        "wcag_criterion": "4.1.2",
        "wcag_level": "A",
        "title": "Links must have discernible text",
        "description": "Ensures links have discernible text. Links without accessible names are confusing for screen reader users and voice control software users.",
        "principle": "Robust",
        "guideline": "4.1 Compatible",
        "severity_criteria": {
            "critical": "Primary navigation or CTA links with no accessible name OR links in forms/checkout flows with no name",
            "high": "Secondary navigation or content links with no accessible name",
            "medium": "Link has accessible name but it's non-descriptive ('click here', 'read more', 'here') OR multiple links with identical text pointing to different URLs",
            "low": "Link text could be more descriptive but is understandable in context",
        },
        "business_impact_template": "Screen reader users cannot understand link purpose, voice control users cannot activate links. This violates WCAG 2.2 Level A (4.1.2 Name, Role, Value) and creates navigation barriers for blind users. High ADA lawsuit risk.",
        "technical_impact": "Screen readers announce 'link' without destination, voice control software cannot target link by name. Automated testing flags as WCAG 2.2 Level A violation.",
        "fix_examples": [
            {
                "before": "<a href='/products'><i class='icon-shop'></i></a>",
                "after": "<a href='/products'><i class='icon-shop' aria-hidden='true'></i><span class='sr-only'>Shop Products</span></a>",
                "explanation": "Add visually hidden text for icon-only link",
            },
            {
                "before": "<a href='/article-1'>Read more</a><a href='/article-2'>Read more</a>",
                "after": "<a href='/article-1'>Read more about WCAG 2.2 Updates</a><a href='/article-2'>Read more about ADA Compliance</a>",
                "explanation": "Make each 'read more' link unique and descriptive",
            },
        ],
        "best_practices": [
            "Link text should make sense out of context (avoid 'click here')",
            "Include keywords indicating link destination",
            "For icon links, use aria-label or visually-hidden text",
            "Ensure each link has unique, descriptive text when possible",
            "Test with screen reader to verify link announcement makes sense",
        ],
        "tags": ["wcag2a", "wcag412", "section508", "ada", "axe-core"],
        "act_rule_ids": ["c487ae"],
        "related_rules": ["link-in-text-block", "identical-links-same-purpose"],
    },
    {
        "rule_id": "label",
        "wcag_criterion": "1.3.1",
        "wcag_level": "A",
        "title": "Form elements must have labels",
        "description": "Ensures every form element has a label. Screen readers rely on labels to announce the purpose of form fields.",
        "principle": "Perceivable",
        "guideline": "1.3 Adaptable",
        "severity_criteria": {
            "critical": "Required form fields (email, password, name) with no label OR multi-step form fields with no labels",
            "high": "Optional form fields with no label OR form fields in checkout/payment flows with no labels",
            "medium": "Form field has label but label not programmatically associated (<label for> missing) OR placeholder used as label (disappears on focus)",
            "low": "Label exists and is associated but could be more descriptive",
        },
        "business_impact_template": "Screen reader users cannot complete forms, blocking {action} (signups, purchases, contact). Violates WCAG 2.2 Level A (1.3.1 Info and Relationships) and creates major accessibility barrier. High ADA lawsuit risk, especially for e-commerce.",
        "technical_impact": "Screen readers announce 'Edit text' without purpose. Voice control users cannot target field by label. Form auto-fill breaks. Automated testing flags as critical WCAG 2.2 Level A violation.",
        "fix_examples": [
            {
                "before": "<input type='email' placeholder='Enter email'>",
                "after": "<label for='email-input'>Email Address</label><input type='email' id='email-input' placeholder='your@email.com'>",
                "explanation": "Add proper <label> element with for attribute, use placeholder for example only",
            },
            {
                "before": "<div>Username</div><input type='text'>",
                "after": "<label for='username-input'>Username</label><input type='text' id='username-input'>",
                "explanation": "Use <label> element (not <div>) with for attribute to associate label with input",
            },
        ],
        "best_practices": [
            "Always use <label> elements, not placeholder text as labels",
            "Associate labels with inputs using for/id attributes",
            "Keep labels visible at all times (don't hide on focus)",
            "For complex forms, use <fieldset> and <legend> to group related fields",
            "Required fields should have visible indicator and aria-required='true'",
        ],
        "tags": ["wcag2a", "wcag131", "section508", "ada", "axe-core", "cat.forms"],
        "act_rule_ids": ["e086e5"],
        "related_rules": ["label-title-only", "select-name"],
    },
    {
        "rule_id": "region",
        "wcag_criterion": "1.3.1",
        "wcag_level": "A",
        "title": "All content should be contained in a landmark region",
        "description": "Ensures all page content is contained within ARIA landmarks or HTML5 sectioning elements. Landmark regions help screen reader users navigate page structure efficiently.",
        "principle": "Perceivable",
        "guideline": "1.3 Adaptable",
        "severity_criteria": {
            "critical": "Main content (articles, primary page content) not in landmark region OR critical interactive elements (forms, buttons) outside landmarks",
            "high": "Navigation or header/footer content not in appropriate landmark regions",
            "medium": "Secondary content not in landmark OR excessive use of generic <div> without semantic structure",
            "low": "Content is in landmarks but landmark structure could be optimized",
        },
        "business_impact_template": "Screen reader users cannot efficiently navigate page structure, wasting time searching for content. Violates WCAG 2.2 Level A (1.3.1 Info and Relationships) and creates poor user experience for blind users. May contribute to ADA complaints.",
        "technical_impact": "Screen readers cannot skip to main content or navigate by landmarks. Keyboard users must tab through entire page to reach content. Automated scans flag as WCAG 2.2 Level A violation.",
        "fix_examples": [
            {
                "before": "<div class='main-content'>...</div>",
                "after": "<main role='main'>...</main>",
                "explanation": "Use semantic <main> element for primary content",
            },
            {
                "before": "<div class='header'>...</div>",
                "after": "<header role='banner'>...</header>",
                "explanation": "Use <header> element with banner role for site header",
            },
            {
                "before": "<div class='sidebar'>...</div>",
                "after": "<aside role='complementary'>...</aside>",
                "explanation": "Use <aside> element for supplementary content",
            },
        ],
        "best_practices": [
            "Use semantic HTML5 elements (main, header, nav, aside, footer)",
            "Include role attributes for better backwards compatibility",
            "Ensure one <main> landmark per page",
            "Use navigation landmark (<nav>) for site navigation",
            "Label multiple landmarks of same type with aria-label",
        ],
        "tags": ["wcag2a", "wcag131", "section508", "axe-core", "cat.keyboard"],
        "act_rule_ids": [],
        "related_rules": ["landmark-one-main", "landmark-unique"],
    },
    {
        "rule_id": "html-has-lang",
        "wcag_criterion": "3.1.1",
        "wcag_level": "A",
        "title": "HTML element must have a lang attribute",
        "description": "Ensures <html> element has lang attribute. Screen readers use lang to select appropriate pronunciation and voice.",
        "principle": "Understandable",
        "guideline": "3.1 Readable",
        "severity_criteria": {
            "critical": "<html> element completely missing lang attribute",
            "high": "lang attribute present but empty (lang='') or invalid language code",
            "medium": "lang attribute uses non-standard format (e.g., 'English' instead of 'en')",
            "low": "lang attribute correct but could specify dialect (en-US vs en-GB)",
        },
        "business_impact_template": "Screen readers cannot select appropriate voice/pronunciation, causing mispronunciation for blind users. Violates WCAG 2.2 Level A (3.1.1 Language of Page) and creates poor experience for international users. Required for ADA/Section 508 compliance.",
        "technical_impact": "Screen readers use wrong voice/pronunciation. Translation tools may fail. Search engines may misidentify language. Automated testing flags as WCAG 2.2 Level A violation.",
        "fix_examples": [
            {
                "before": "<!DOCTYPE html><html><head>...",
                "after": "<!DOCTYPE html><html lang='en'><head>...",
                "explanation": "Add lang attribute to <html> element with appropriate language code",
            },
            {
                "before": "<html lang=''>",
                "after": "<html lang='en-US'>",
                "explanation": "Provide valid language code (ISO 639-1) and optionally specify dialect",
            },
        ],
        "best_practices": [
            "Use ISO 639-1 language codes (en, es, fr, de, etc.)",
            "Include region code when relevant (en-US, en-GB, fr-CA)",
            "For multilingual pages, use lang on specific elements (<span lang='es'>Hola</span>)",
            "Test with screen reader to verify pronunciation",
            "Validate language codes against ISO 639-1 standard",
        ],
        "tags": ["wcag2a", "wcag311", "section508", "ada", "axe-core", "cat.language"],
        "act_rule_ids": ["bf051a"],
        "related_rules": ["valid-lang", "html-lang-valid"],
    },
]

# Export with human-friendly descriptions added
WCAG_GUIDELINES = _add_human_descriptions(_WCAG_GUIDELINES_RAW)
