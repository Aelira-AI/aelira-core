"""PDF findings record the category the Issues page groups them by.

Regression for #873. Word, Excel and PowerPoint findings record a category,
but PDF findings recorded only a rule, a message and sometimes an issue type,
so the Issues page showed every PDF finding as "Other".

A finding's category also feeds the remediation classifier, which reads every
category-bearing field and fails closed when they disagree. Each PDF finding
therefore records the category the classifier already derives from its other
fields, and the tests below hold that recording it changes no classification.
Findings whose fields classify as Other keep no category until their
remediation category is decided on its own.
"""

import ast
import importlib
from pathlib import Path

import pikepdf
import pytest

from src.education.pdf_checks.structure_checker import StructureTreeChecker
from src.education.remediation.base import IssueCategory, classify_issue_category

ROOT = Path(__file__).resolve().parents[1]
PDF_FINDING_MODULES = [ROOT / "src/education/pdf_processor.py"] + sorted(
    (ROOT / "src/education/pdf_checks").glob("*.py")
)

# Findings whose other fields classify as Other. Recording a category for
# these would change how remediation treats them, so they keep none (#887).
UNCATEGORISED = {
    "ai_alt_quality_review",
    "Document should start with H1 heading",
    "Missing lang attribute on html element",
    "Missing or empty page title",
    "No content extracted from PDF",
}


def _constant(node, module):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return getattr(module, node.id, None)
    if isinstance(node, ast.IfExp):
        return [_constant(node.body, module), _constant(node.orelse, module)]
    return None


def recorded_findings():
    """Every finding dict literal in the PDF scanner, with constant fields."""
    findings = []
    for path in PDF_FINDING_MODULES:
        relative = path.relative_to(ROOT).with_suffix("")
        module = importlib.import_module(".".join(relative.parts))
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Dict):
                continue
            fields = {
                key.value: value
                for key, value in zip(node.keys, node.values)
                if isinstance(key, ast.Constant)
            }
            if "severity" not in fields or "rule" not in fields:
                continue
            finding = {
                name: _constant(fields[name], module)
                for name in ("rule", "issue_type", "message", "category")
                if name in fields
            }
            finding["site"] = f"{path.name}:{node.lineno}"
            findings.append(finding)
    return findings


def variants(finding):
    """One concrete finding per branch of a conditional issue type."""
    issue_types = finding.get("issue_type")
    categories = finding.get("category")
    count = len(issue_types) if isinstance(issue_types, list) else 1
    for index in range(count):
        concrete = {
            key: value
            for key, value in finding.items()
            if key not in {"site", "issue_type", "category"} and value is not None
        }
        issue_type = issue_types[index] if count > 1 else issue_types
        category = categories[index] if isinstance(categories, list) else categories
        if issue_type is not None:
            concrete["issue_type"] = issue_type
        if category is not None:
            concrete["category"] = category
        yield concrete


def exempt(finding):
    return finding.get("issue_type") in UNCATEGORISED or (
        isinstance(finding.get("message"), str) and finding["message"] in UNCATEGORISED
    )


FINDINGS = recorded_findings()


def test_every_pdf_finding_site_was_found():
    assert len(FINDINGS) >= 34


@pytest.mark.parametrize("finding", FINDINGS, ids=lambda f: f["site"])
def test_pdf_finding_records_a_known_category(finding):
    for concrete in variants(finding):
        if exempt(concrete):
            assert "category" not in concrete, concrete
            continue
        assert IssueCategory(concrete["category"]) is not IssueCategory.OTHER


@pytest.mark.parametrize("finding", FINDINGS, ids=lambda f: f["site"])
@pytest.mark.parametrize("authoritative", [True, False])
def test_recorded_category_changes_no_classification(finding, authoritative):
    for concrete in variants(finding):
        category = concrete.pop("category", None)
        without = classify_issue_category(concrete, authoritative=authoritative)
        if category is None:
            assert exempt(concrete), concrete
            assert without.category is IssueCategory.OTHER
            continue
        with_category = classify_issue_category(
            {**concrete, "category": category}, authoritative=authoritative
        )
        assert with_category == without, concrete
        if without.manual_reason is None:
            assert with_category.category.value == category


def test_structure_checker_findings_carry_categories(tmp_path):
    path = tmp_path / "untagged.pdf"
    pdf = pikepdf.new()
    for _ in range(3):
        pdf.add_blank_page(page_size=(612, 792))
    pdf.save(path)

    findings = {
        finding["issue_type"]: finding.get("category")
        for finding in StructureTreeChecker().check(str(path))
    }

    assert findings["missing_language"] == "language"
    assert findings["missing_title"] == "title"
    assert findings["missing_structure_tree"] == "structure"
