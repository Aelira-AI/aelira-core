"""Local caption remediation does not depend on an AI provider."""

from src.education.remediation.base import RemediationConfig
from src.education.remediation.multimedia_remediator import MultimediaRemediator

# The findings a video scan records for media without captions or descriptions.
SCAN_ISSUES = [
    {
        "rule": "WCAG 1.2.2",
        "severity": "critical",
        "message": "Missing captions/transcription for multimedia content",
        "impact": "Deaf and hard-of-hearing users cannot access audio content",
    },
    {
        "rule": "WCAG 1.2.3",
        "severity": "high",
        "message": "Audio description not provided for visual content",
        "impact": "Blind and visually impaired users cannot access visual information",
        "recommendation": "Enable audio description generation or add manually",
    },
]


def _remediate_without_ai(tmp_path, monkeypatch):
    clip = tmp_path / "lecture.mp4"
    clip.write_bytes(b"not decoded in this test")
    # Mirrors the remediation subprocess when no AI provider is configured.
    config = RemediationConfig(
        use_ai=False,
        allow_legacy_nested_ai=False,
        fix_alt_text=False,
        verify_fixes=True,
        create_backup=False,
        output_directory=str(tmp_path),
    )
    remediator = MultimediaRemediator(
        file_path=str(clip), issues=SCAN_ISSUES, config=config
    )
    attempts = []

    def fake_captions():
        attempts.append("captions")
        caption_file = tmp_path / "lecture_remediated.vtt"
        caption_file.write_text("WEBVTT\n\n1\n00:00:00.000 --> 00:00:01.000\nHello\n")
        remediator._caption_file = str(caption_file)
        return True

    monkeypatch.setattr(remediator, "_generate_captions", fake_captions)
    return remediator.remediate(), attempts


def test_captions_are_attempted_without_an_ai_provider(tmp_path, monkeypatch):
    result, attempts = _remediate_without_ai(tmp_path, monkeypatch)

    assert attempts == ["captions"]
    reasons = {issue.description: issue.reason for issue in result.manual_issues}
    caption_reason = reasons.get(SCAN_ISSUES[0]["message"])
    assert caption_reason != "Category disabled in configuration"


def test_audio_description_still_requires_an_ai_provider(tmp_path, monkeypatch):
    result, _ = _remediate_without_ai(tmp_path, monkeypatch)

    manual = [issue.description for issue in result.manual_issues]
    assert SCAN_ISSUES[1]["message"] in manual
    assert not any(
        SCAN_ISSUES[1]["message"] == fixed.description for fixed in result.fixed_issues
    )
