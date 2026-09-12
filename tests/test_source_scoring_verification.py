"""Regressions for source scores being replaced by generated-output estimates."""

from unittest.mock import Mock

import pytest

from src.education.latex_processor import LaTeXProcessor
from src.education.multimedia_processor import (
    AudioDescription,
    MultimediaProcessor,
    TranscriptionSegment,
)
from src.education.remediation.base import RemediationConfig
from src.education.remediation.html_remediator import HtmlRemediator
from src.education.remediation.latex_remediator import LatexRemediator
from src.education.remediation.multimedia_remediator import MultimediaRemediator
from src.education.remediation.source_verification import scan_code_source


def test_css_parser_failure_cannot_return_perfect_source_score(tmp_path, monkeypatch):
    from src.education.scan_completeness import IncompleteScanError

    source = tmp_path / "styles.css"
    source.write_text("p { font-size: 8px; }")
    measured = scan_code_source(str(source))
    assert measured["issues"]
    assert measured["compliance_score"] < 100
    monkeypatch.setattr(
        "src.education.code_scanner.cssutils.parseString",
        Mock(side_effect=RuntimeError("parse failed")),
    )
    with pytest.raises(IncompleteScanError):
        scan_code_source(str(source))


def test_latex_without_equations_still_grades_source(tmp_path):
    path = tmp_path / "document.tex"
    path.write_text(r"\documentclass{article}\begin{document}Hello\end{document}")
    processor = LaTeXProcessor(use_ai=False)
    result = processor.process_document(str(path))
    assert result.total_equations == 0
    assert result.conversion_success_rate == 100
    assert result.compliance_score < 100
    assert {issue["issue_type"] for issue in result.source_issues} >= {
        "missing_title",
        "missing_lang",
    }
    assert (
        processor.scan_source(path.read_text())["compliance_score"]
        == result.compliance_score
    )


@pytest.mark.asyncio
async def test_latex_async_and_file_source_score_match_when_conversion_fails(
    tmp_path, monkeypatch
):
    path = tmp_path / "document.tex"
    text = r"\documentclass{article}\begin{document}$x$\end{document}"
    path.write_text(text)
    processor = LaTeXProcessor(use_ai=False)
    from src.education.latex_processor import MathMLConversionResult

    monkeypatch.setattr(
        processor,
        "convert_equation",
        lambda *args: MathMLConversionResult(
            equation_id=1,
            latex_source="x",
            mathml_output="",
            conversion_success=False,
            wcag_compliant=False,
            error_message="Test conversion failed",
        ),
    )
    file_result = processor.process_document(str(path))
    async_result = await processor.process_latex(text)
    assert file_result.compliance_score == async_result["compliance"]["score"]
    assert (
        file_result.compliance_score == processor.scan_source(text)["compliance_score"]
    )
    assert file_result.conversion_success_rate == 0
    assert async_result["metadata"]["conversion_issues"]
    assert all(
        issue["type"] != "conversion_failed"
        for issue in async_result["compliance"]["issues"]
    )


def test_saved_html_rescan_can_detect_regression_beyond_lang_and_alt(tmp_path):
    original = tmp_path / "original.html"
    output = tmp_path / "output.html"
    original.write_text(
        '<html lang="en"><head><title>Title</title></head><body><h1>Title</h1><p>Text</p></body></html>'
    )
    output.write_text(
        original.read_text().replace("<p>Text</p>", '<input type="text">')
    )
    remediator = HtmlRemediator(str(original), [], RemediationConfig(use_ai=False))
    remediator._verify_fixes(str(output))
    assert remediator.result.score_provenance == "scanner_rescan"
    assert (
        remediator.result.original_compliance_score
        == scan_code_source(str(original))["compliance_score"]
    )
    assert (
        remediator.result.remediated_compliance_score
        == scan_code_source(str(output))["compliance_score"]
    )
    assert remediator.result.improvement < 0
    assert not remediator.result.verification_passed
    assert remediator.result.verification_result.regressions


def test_saved_latex_verifies_actual_source_and_rejects_other_export_format(tmp_path):
    original = tmp_path / "original.tex"
    output = tmp_path / "output.tex"
    original.write_text(r"\documentclass{article}\begin{document}Text\end{document}")
    output.write_text(
        original.read_text().replace(
            r"\begin{document}", r"\title{Document}\begin{document}"
        )
    )
    processor = LaTeXProcessor(use_ai=False)
    raw_issue = next(
        issue
        for issue in processor.scan_source(original.read_text())["issues"]
        if issue["issue_type"] == "missing_title"
    )
    raw_issue.update(id="title", type="missing_title")
    remediator = LatexRemediator(
        str(original), [raw_issue], RemediationConfig(use_ai=False)
    )
    remediator._add_fixed_issue(
        remediator.issues[0], fixed_content="Document", fix_method="rule"
    )
    remediator._verify_fixes(str(output))
    assert remediator.result.fixed_count == 1
    assert remediator.result.fixed_issues[0].verification_passed
    assert remediator.result.improvement == 5
    assert remediator.result.remediated_compliance_score < 100
    export_remediator = LatexRemediator(
        str(original), [raw_issue], RemediationConfig(use_ai=False)
    )
    export_remediator._verify_fixes(str(tmp_path / "export.pdf"))
    assert export_remediator.result.remediated_compliance_score is None
    assert not export_remediator.result.verification_passed


def test_multimedia_generating_companions_never_inflates_input_score(
    tmp_path, monkeypatch
):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"fixture")
    processor = MultimediaProcessor(use_gemini=False)
    monkeypatch.setattr(processor, "_get_media_info", lambda path: ("video", 10.0))
    monkeypatch.setattr(processor, "_check_existing_captions", lambda path: False)
    monkeypatch.setattr(processor, "_extract_audio", lambda path: path)
    monkeypatch.setattr(
        processor,
        "_transcribe_audio",
        lambda *args, **kw: [
            TranscriptionSegment(start_time=0, end_time=5, text="Hello")
        ],
    )
    monkeypatch.setattr(
        processor,
        "_generate_audio_descriptions",
        lambda *args: [
            AudioDescription(timestamp=1, description="A tree", scene_type="landscape")
        ],
    )
    monkeypatch.setattr(processor, "create_accessible_video", lambda **kw: None)
    before = processor.process_media(
        str(path), generate_captions=False, detect_flashing=False
    )
    generated = processor.process_media(
        str(path),
        generate_captions=True,
        generate_audio_descriptions=True,
        detect_flashing=False,
        enhance_captions=False,
    )
    assert generated.caption_formats
    assert generated.audio_descriptions
    assert not generated.has_captions
    assert generated.compliance_score == before.compliance_score
    assert generated.issues == before.issues


def test_companion_file_presence_cannot_verify_a_media_fix(tmp_path):
    original = tmp_path / "audio.mp3"
    original.write_bytes(b"media")
    caption = tmp_path / "captions.vtt"
    caption.write_text(
        "WEBVTT\n\n00:00:00.000 --> 00:00:05.000\nSome generated caption text.\n"
    )
    remediator = MultimediaRemediator(
        str(original),
        [{"id": "captions", "description": "Missing captions", "category": "aria"}],
        RemediationConfig(use_ai=False),
    )
    remediator._caption_file = str(caption)
    remediator._add_fixed_issue(
        remediator.issues[0], fixed_content="Generated captions", fix_method="rule"
    )
    remediator._verify_fixes(str(caption))
    assert remediator.result.fixed_count == 0
    assert remediator.result.manual_count == 1
    assert not remediator.result.verification_passed
    assert remediator.result.remediated_compliance_score is None


@pytest.mark.parametrize(
    "approved, saved_language",
    [(True, "en-AU"), (True, "fr"), (False, "en-AU")],
)
def test_semantic_language_change_requires_review_even_when_supplied(
    tmp_path, approved, saved_language
):
    original = tmp_path / "language.html"
    saved = tmp_path / "saved.html"
    original.write_text('<html lang="fr"><body>Text</body></html>')
    saved.write_text(f'<html lang="{saved_language}"><body>Text</body></html>')
    remediator = HtmlRemediator(
        str(original),
        [
            {
                "id": "language",
                "category": "language",
                "description": "Language is incorrect",
                "original_content": "fr",
                "fix_suggestion": "en-AU",
            }
        ],
        RemediationConfig(use_ai=False, use_supplied_fixes=approved),
    )
    remediator._add_fixed_issue(
        remediator.issues[0], fixed_content="en-AU", fix_method="approved_snapshot"
    )
    remediator._verify_fixes(str(saved))
    assert remediator.result.fixed_count == 0
    assert remediator.result.improvement == 0
    assert remediator.result.manual_count == 1


def test_existing_replacement_fragment_does_not_prove_approved_target_was_fixed(
    tmp_path,
):
    original = tmp_path / "heading.html"
    saved = tmp_path / "saved.html"
    original.write_text(
        '<html lang="en"><body><h3>Services</h3><h2>Services</h2></body></html>'
    )
    # Removing the original while retaining an unrelated pre-existing replacement
    # must not count as applying the approved edit.
    saved.write_text('<html lang="en"><body><h2>Services</h2></body></html>')
    remediator = HtmlRemediator(
        str(original),
        [
            {
                "id": "heading",
                "category": "heading",
                "description": "Heading needs human review",
                "original_content": "<h3>Services</h3>",
                "fix_suggestion": "<h2>Services</h2>",
            }
        ],
        RemediationConfig(use_ai=False, use_supplied_fixes=True),
    )
    remediator._add_fixed_issue(
        remediator.issues[0],
        fixed_content="<h2>Services</h2>",
        fix_method="approved_snapshot",
    )
    remediator._verify_fixes(str(saved))
    assert remediator.result.fixed_count == 0
    assert remediator.result.manual_count == 1


@pytest.mark.parametrize("kind", ["html", "latex"])
@pytest.mark.parametrize("alias", ["same", "symlink", "hardlink"])
def test_source_save_rejects_original_alias_before_writing(
    tmp_path, monkeypatch, kind, alias
):
    ext = "html" if kind == "html" else "tex"
    original = tmp_path / f"original.{ext}"
    content = (
        '<html lang="fr"><body>Text</body></html>'
        if kind == "html"
        else r"\documentclass{article}\begin{document}Text\end{document}"
    )
    original.write_text(content)
    output = original if alias == "same" else tmp_path / f"alias.{ext}"
    if alias == "symlink":
        output.symlink_to(original)
    elif alias == "hardlink":
        output.hardlink_to(original)
    cls = HtmlRemediator if kind == "html" else LatexRemediator
    remediator = cls(str(original), [], RemediationConfig(use_ai=False))
    document = remediator._load_document()
    monkeypatch.setattr(remediator, "_get_output_path", lambda: str(output))
    with pytest.raises(ValueError, match="must not overwrite"):
        remediator._save_document(document)
    assert original.read_text() == content
