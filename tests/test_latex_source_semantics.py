"""Source declarations are evidence of author intent, never exported repair."""

from unittest.mock import MagicMock

import pytest

from src.education.latex_processor import LaTeXProcessor
from src.education.remediation.base import (
    IssueCategory,
    IssueSeverity,
    RemediationConfig,
    RemediationIssue,
)
from src.education.remediation.latex_remediator import LatexRemediator


def findings(source):
    return LaTeXProcessor(use_ai=False, llm_client=False).detect_accessibility_issues(
        source
    )


def types(source):
    return {finding.issue_type for finding in findings(source)}


@pytest.mark.parametrize(
    "image",
    [
        r"\includegraphics{plot.png}",
        r"\includegraphics[alt={}]{plot.png}",
        r"\includegraphics[alt={\customdescription}]{plot.png}",
        r"\includegraphics[alt={first},alt={second}]{plot.png}",
        r"\includegraphics[artifact=false]{plot.png}",
        r"\includegraphics[artifact,alt={}]{plot.png}",
        r"\includegraphics[artifact,alt={Meaningful figure}]{plot.png}",
        r"\includegraphics[decorative=true]{plot.png}",
    ],
)
def test_caption_does_not_supply_image_alternative(image):
    source = (
        "\\begin{figure}\n"
        + image
        + "\n% Alt text: a rising trend\n"
        + r"\caption{A {nested} visible caption}\end{figure}"
    )
    assert "missing_alt_text" in types(source)


@pytest.mark.parametrize(
    "image",
    [
        r"\includegraphics[alt={A {nested} literal description}]{plot.png}",
        r"\includegraphics[alt={A [bounded] literal description}]{plot.png}",
        "\\includegraphics[\nalt={A multiline description}\n]{plot.png}",
        r"\includegraphics[artifact]{decoration.png}",
        r"\includegraphics[artifact=true]{decoration.png}",
    ],
)
def test_supported_authored_alternative_or_artifact_is_detected(image):
    assert "missing_alt_text" not in types(image)


async def test_source_declaration_is_not_exported_semantic_verification():
    source = r"\includegraphics[alt={A {nested} authored description}]{plot.png}"
    result = await LaTeXProcessor(use_ai=False, llm_client=False).process_latex(source)
    evidence = result["latex_evidence"]["tex"]
    assert evidence["accessibility_status"] == "not_verified"
    assert evidence["fidelity"]["status"] == "not_assessed"


@pytest.mark.parametrize("rules", [r"\hline", r"\toprule\midrule\bottomrule", ""])
def test_caption_and_rules_do_not_identify_headers(rules):
    source = (
        r"\begin{table}\caption{Authored table caption}\begin{tabular}{cc}"
        + rules
        + r"Name & Value \\ A & 1\end{tabular}\end{table}"
    )
    assert "complex_table_no_header" in types(source)


def test_supported_explicit_headers_are_detected():
    source = r"\tagpdfsetup{table/header-rows={1},table/header-columns={1}}\begin{tabular}{cc}Name & Value \\ A & 1\end{tabular}"
    assert "complex_table_no_header" not in types(source)


@pytest.mark.parametrize(
    "declaration",
    [
        r"\tagpdfsetup{table/header-rows={9}}",
        r"\tagpdfsetup{table/header-rows={\myheader}}",
        r"\tagpdfsetup{table/header-rows={1}}intervening text",
        "% \\tagpdfsetup{table/header-rows={1}}\n",
    ],
)
def test_unsupported_header_declarations_remain_unresolved(declaration):
    source = declaration + r"\begin{tabular}{cc}Name & Value \\ A & 1\end{tabular}"
    assert "complex_table_no_header" in types(source)


def test_comments_cannot_supply_declarations_or_create_images():
    source = (
        "% \\includegraphics[alt={fake}]{comment.png}\n" + r"\includegraphics{real.png}"
    )
    missing = [row for row in findings(source) if row.issue_type == "missing_alt_text"]
    assert len(missing) == 1
    assert "real.png" in missing[0].description
    assert missing[0].line_number == 2


@pytest.mark.parametrize(
    "category,fix",
    [
        (IssueCategory.ALT_TEXT, "auto"),
        (IssueCategory.ALT_TEXT, "An invented description"),
        (IssueCategory.TABLE, "table_caption:Data table"),
        (IssueCategory.TABLE, "table_header:add_structure"),
    ],
)
def test_no_automatic_semantic_fix_path_changes_source(tmp_path, category, fix):
    source = (
        r"\begin{figure}\includegraphics{mystery.png}\end{figure}"
        + r"\begin{table}\begin{tabular}{cc}Name & Value \\ A & 1\end{tabular}\end{table}"
    )
    path = tmp_path / "authored.tex"
    path.write_text(source)
    client = MagicMock()
    remediator = LatexRemediator(
        str(path),
        [],
        RemediationConfig(use_ai=True),
        ai_client=client,
        alt_text_client=client,
    )
    remediator._load_document()
    issue = RemediationIssue(
        id="semantic",
        category=category,
        severity=IssueSeverity.HIGH,
        description="Figure or table lacks a trustworthy description or header",
    )
    assert not remediator.can_auto_fix(issue)
    assert remediator._get_rule_based_fix(issue, source) is None
    assert remediator._get_template_fix(issue) is None
    assert remediator._get_ai_generated_fix(issue, source, client=client) is None
    assert not remediator.apply_fix(issue, source, fix)
    remediator._process_issue(issue, source)
    assert remediator.result.fixed_count == 0
    assert remediator.result.manual_count == 1
    assert remediator.result.ai_calls_made == 0
    assert remediator._modified_content == source
    assert path.read_text() == source
    client.generate_text_sync.assert_not_called()


def test_auto_path_preserves_figures_tables_and_manual_findings(tmp_path):
    body = (
        r"\begin{figure}\includegraphics{mystery.png}\end{figure}"
        + r"\begin{table}\begin{tabular}{cc}Name & Value \\ A & 1\end{tabular}\end{table}"
    )
    source = r"\documentclass{article}\begin{document}" + body + r"\end{document}"
    path = tmp_path / "source.tex"
    path.write_text(source)
    remediator = LatexRemediator(str(path), [])
    remediator.auto_remediate()
    assert body in remediator._modified_content
    assert path.read_text() == source
    assert remediator.result.manual_count == 4
    assert not any(
        "caption" in change or "header separation" in change
        for change in remediator._modifications
    )


def test_auto_path_preserves_authored_alternatives_and_artifacts(tmp_path):
    source = (
        r"\includegraphics[alt={A {nested} authored description}]{plot.png}"
        + r"\includegraphics[artifact]{border.png}"
        + r"\tagpdfsetup{table/header-rows={1}}\begin{tabular}{cc}Name & Value \\ A & 1\end{tabular}"
    )
    path = tmp_path / "source.tex"
    path.write_text(source)
    remediator = LatexRemediator(str(path), [])
    assert not remediator.auto_remediate()
    assert remediator._modified_content == source
    assert path.read_text() == source
    assert remediator.result.fixed_count == 0
    assert remediator.result.manual_count == 0


@pytest.mark.parametrize(
    "environment", ["tabular*", "tabularx", "longtable", "tabulary"]
)
def test_unsupported_tables_remain_manual(environment):
    source = (
        r"\tagpdfsetup{table/header-rows={1}}\begin{"
        + environment
        + r"}{cc}Name & Value \\ A & 1\end{"
        + environment
        + "}"
    )
    assert "complex_table_no_header" in types(source)


def test_caption_only_drawing_remains_unresolved():
    source = r"\begin{figure}\begin{tikzpicture}\draw (0,0) -- (1,1);\end{tikzpicture}\caption{A diagram}\end{figure}"
    assert "missing_alt_text" in types(source)


def test_alternative_does_not_leak_to_other_images():
    source = r"\includegraphics[alt={A rising curve}]{one.png}\includegraphics{two.png}"
    missing = [row for row in findings(source) if row.issue_type == "missing_alt_text"]
    assert len(missing) == 1
    assert "two.png" in missing[0].description
