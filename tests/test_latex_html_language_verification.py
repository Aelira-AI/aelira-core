"""The narrow language observer checks the final saved HTML, never a score."""

import pytest

from src.education.latex_compatibility import ConversionDecision
from src.education.latex_diagnostics import ConversionDiagnostics, ConversionStage
from src.education.latex_evidence import LatexCheck, LatexRepresentationEvidence, digest
from src.education.remediation.latex_html_language_verification import (
    saved_html_has_authored_language,
    verify_saved_html_language_files,
)
from src.education.remediation.base import RemediationConfig
from src.education.remediation.latex_remediator import LatexRemediator

ORIGINAL = (
    r"\documentclass{article}"
    "\n"
    r"\usepackage{hyperref}"
    "\n"
    r"\hypersetup{pdflang={en}}"
    "\n"
    r"\begin{document}Text\end{document}"
).encode()
CONVERTED = ORIGINAL.replace(
    b"\\usepackage{hyperref}",
    b"\\usepackage[english]{babel}\n\\usepackage{hyperref}",
)
HTML = b'<html lang="en"><body>Text</body></html>'


def evidence(original=ORIGINAL, converted=CONVERTED, html=HTML):
    decision = ConversionDecision(
        source_sha256=digest(converted),
        selected_route="latexml",
        profile="single-source-html",
        tool_versions={"latexml": "0.8.8"},
    )
    diagnostics = ConversionDiagnostics(
        source_sha256=digest(converted),
        candidate_sha256=digest(html),
        status="accepted",
        decision=decision,
        stages=[
            ConversionStage(
                tool="latexmlpost",
                version="0.8.8",
                phase="postprocess",
                input_sha256=digest(converted),
                candidate_sha256=digest(html),
            )
        ],
    )
    receipt = LatexRepresentationEvidence(
        representation="html",
        source_sha256=digest(original),
        candidate_sha256=digest(html),
        conversion=LatexCheck(status="completed", method="latex-export-v1"),
        conversion_diagnostics=diagnostics,
    )
    return diagnostics, receipt


def test_exact_authored_language_is_observed_in_accepted_saved_html():
    diagnostics, receipt = evidence()
    assert saved_html_has_authored_language(
        ORIGINAL, CONVERTED, HTML, diagnostics, receipt
    )


@pytest.mark.parametrize(
    "html",
    [
        b"<html><body>Text</body></html>",
        b'<html lang="de"><body>Text</body></html>',
        b'<html lang="en" lang="en"><body>Text</body></html>',
        b'<html lang="en" xml:lang="de"><body>Text</body></html>',
        b'<html lang="en" xml:lang="en"><body>Text</body></html>',
        b'<template><html lang="en"><body>Text</body></html></template>',
        b'Extra<html lang="en"><body>Text</body></html>',
        b'<html lang="en"><html lang="en"></html></html>',
        b'<html lang="en"><body>Text</body></html><html lang="en"></html>',
        b'<html lang="en"><body>Text</body></html>\xff',
    ],
)
def test_wrong_missing_duplicate_or_malformed_final_language_is_refused(html):
    diagnostics, receipt = evidence(html=html)
    assert not saved_html_has_authored_language(
        ORIGINAL, CONVERTED, html, diagnostics, receipt
    )


def test_authored_language_must_be_explicit_and_unambiguous():
    diagnostics, receipt = evidence()
    for original in [
        ORIGINAL.replace(b"\\hypersetup{pdflang={en}}\n", b""),
        ORIGINAL.replace(b"\\hypersetup{pdflang={en}}", b"\\hypersetup{pdflang={de}}"),
        ORIGINAL.replace(
            b"\\hypersetup{pdflang={en}}",
            b"\\hypersetup{pdflang={en}}\n\\hypersetup{pdflang={de}}",
        ),
    ]:
        diagnostics, receipt = evidence(original=original)
        assert not saved_html_has_authored_language(
            original, CONVERTED, HTML, diagnostics, receipt
        )


def test_current_candidate_source_and_receipt_hashes_must_all_bind():
    diagnostics, receipt = evidence()
    assert not saved_html_has_authored_language(
        ORIGINAL, CONVERTED, HTML + b" ", diagnostics, receipt
    )
    assert not saved_html_has_authored_language(
        ORIGINAL, CONVERTED + b" ", HTML, diagnostics, receipt
    )
    assert not saved_html_has_authored_language(
        ORIGINAL + b" ", CONVERTED, HTML, diagnostics, receipt
    )
    assert not saved_html_has_authored_language(
        ORIGINAL, CONVERTED, HTML, None, receipt
    )
    assert not saved_html_has_authored_language(
        ORIGINAL, CONVERTED, HTML, diagnostics, None
    )
    assert not saved_html_has_authored_language(
        ORIGINAL,
        CONVERTED,
        HTML,
        diagnostics.model_copy(update={"status": "refused"}),
        receipt,
    )
    assert not saved_html_has_authored_language(
        ORIGINAL,
        CONVERTED,
        HTML,
        diagnostics,
        receipt.model_copy(update={"candidate_sha256": digest(b"other")}),
    )
    assert not saved_html_has_authored_language(
        ORIGINAL,
        CONVERTED,
        HTML,
        diagnostics,
        receipt.model_copy(update={"conversion_diagnostics": None}),
    )


def test_converted_source_language_cannot_override_authored_language():
    diagnostics, receipt = evidence()
    changed = CONVERTED.replace(b"pdflang={en}", b"pdflang={de}")
    assert not saved_html_has_authored_language(
        ORIGINAL, changed, HTML, diagnostics, receipt
    )


def test_loaded_original_snapshot_must_still_be_current(tmp_path):
    source, tex, html = (
        tmp_path / name for name in ("source.tex", "fixed.tex", "saved.html")
    )
    source.write_bytes(ORIGINAL)
    tex.write_bytes(CONVERTED)
    html.write_bytes(HTML)
    diagnostics, receipt = evidence()
    assert verify_saved_html_language_files(
        str(source),
        str(tex),
        str(html),
        diagnostics,
        receipt,
        ORIGINAL,
    )
    assert not verify_saved_html_language_files(
        str(source),
        str(tex),
        str(html),
        diagnostics,
        receipt,
        ORIGINAL + b" ",
    )


def prepared_remediator(
    tmp_path, issue_type="missing_lang", description=None, original=ORIGINAL
):
    source, tex, html = (
        tmp_path / name for name in ("source.tex", "fixed.tex", "saved.html")
    )
    source.write_bytes(original)
    tex.write_bytes(CONVERTED)
    html.write_bytes(HTML)
    issue = {
        "id": "synthetic-issue",
        "type": issue_type,
        "description": description or "Document class doesn't specify language",
        "wcag": "3.1.1",
    }
    remediator = LatexRemediator(
        str(source),
        [issue],
        RemediationConfig(use_ai=False, create_backup=False),
    )
    remediator._load_document()
    remediator._output_files = {"tex": str(tex), "html": str(html)}
    diagnostics, receipt = evidence(original=original)
    remediator._conversion_receipts = {"html": diagnostics}
    remediator.result.latex_evidence = {"html": receipt}
    remediator._add_fixed_issue(
        remediator.issues[0],
        "en",
        "rule_based",
    )
    return remediator, html


def test_remediator_credits_only_bounded_saved_html_language_fix(tmp_path):
    remediator, html = prepared_remediator(tmp_path)
    result = remediator._verify_fixes(str(html))
    assert result.passed
    assert remediator.result.fixed_count == 1
    assert remediator.result.manual_count == 0
    assert remediator.result.fixed_issues[0].needs_review
    assert remediator.result.score_measurement is None
    assert remediator.result.remediated_compliance_score is None
    assert (
        remediator.result.latex_evidence["html"].accessibility_status == "not_verified"
    )
    assert remediator.result.latex_evidence["html"].human_review_required


def test_unchanged_crlf_source_retains_raw_byte_authority(tmp_path):
    original = ORIGINAL.replace(b"\n", b"\r\n")
    remediator, html = prepared_remediator(tmp_path, original=original)
    assert remediator._raw_original_content == original
    assert remediator._original_content == ORIGINAL.decode()
    assert remediator._verify_fixes(str(html)).passed


def test_source_changed_after_load_is_not_credited(tmp_path):
    remediator, html = prepared_remediator(tmp_path)
    source = tmp_path / "source.tex"
    source.write_bytes(ORIGINAL + b" ")
    assert not remediator._verify_fixes(str(html)).passed
    assert remediator.result.fixed_count == 0
    assert remediator.result.manual_count == 1


def test_unsupported_or_manual_source_issue_stays_withheld(tmp_path):
    remediator, html = prepared_remediator(
        tmp_path,
        "missing_alt_text",
        "Figure lacks authored alternative",
    )
    result = remediator._verify_fixes(str(html))
    assert not result.passed
    assert remediator.result.fixed_count == 0
    assert remediator.result.manual_count == 1
    assert remediator.result.score_measurement is None


def test_existing_manual_finding_cannot_be_hidden_by_language_observation(tmp_path):
    remediator, html = prepared_remediator(tmp_path)
    remediator._add_manual_issue(
        remediator.issues[0],
        "Requires review",
        "Review source",
    )
    result = remediator._verify_fixes(str(html))
    assert not result.passed
    assert remediator.result.fixed_count == 0
    assert remediator.result.manual_count == 1
