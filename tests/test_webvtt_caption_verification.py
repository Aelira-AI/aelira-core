"""Generated captions publish for review only when the saved WebVTT matches the transcription."""

import pytest

from src.education.remediation.base import RemediationConfig
from src.education.remediation.multimedia_remediator import MultimediaRemediator
from src.education.remediation.webvtt_verification import (
    caption_file_problem,
    parse_webvtt,
)

SEGMENTS = [
    {"start": 0.0, "end": 2.0, "text": " Welcome to the biology lab."},
    {"start": 2.0, "end": 5.0, "text": "Today we measure plant growth."},
]
VTT = (
    "WEBVTT\n\n"
    "1\n00:00:00.000 --> 00:00:02.000\nWelcome to the biology lab.\n\n"
    "2\n00:00:02.000 --> 00:00:05.000\nToday we measure plant growth.\n\n"
)


def test_parses_generated_captions():
    cues = parse_webvtt(VTT)
    assert [(c.start, c.end, c.text) for c in cues] == [
        (0.0, 2.0, "Welcome to the biology lab."),
        (2.0, 5.0, "Today we measure plant growth."),
    ]


@pytest.mark.parametrize(
    "content",
    [
        "WEBVTTX\n\n00:00:00.000 --> 00:00:01.000\nHi\n",
        "\n\nWEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHi\n",
        "WEBVTT\n\n00:00:01.000 --> 00:00:00.500\nBackwards\n",
        "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n\n",
        "WEBVTT\n\n00:00:00.000 --> 00:00:01.000 align:start\nSettings\n",
        "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nA --> B\n",
        "WEBVTT\n\n0:00:00.000 --> 00:00:01.000\nShort hours\n",
        "WEBVTT\n\nNOTE generated\n\n00:00:00.000 --> 00:00:01.000\nHi\n",
    ],
)
def test_rejects_malformed_or_unexpected_webvtt(content):
    with pytest.raises(ValueError):
        parse_webvtt(content)


def test_caption_file_must_match_the_transcription(tmp_path):
    path = tmp_path / "captions.vtt"
    path.write_text(VTT, encoding="utf-8")
    assert caption_file_problem(str(path), SEGMENTS, duration=5.2) is None

    path.write_text(VTT.replace("plant growth", "plant height"), encoding="utf-8")
    assert caption_file_problem(str(path), SEGMENTS, duration=5.2)

    path.write_text(VTT, encoding="utf-8")
    assert caption_file_problem(str(path), SEGMENTS, duration=3.0)
    assert caption_file_problem(str(path), SEGMENTS[:1], duration=5.2)
    assert caption_file_problem(str(path), [], duration=5.2)


def _remediator_with_captions(tmp_path, content=VTT):
    original = tmp_path / "source.mp4"
    original.write_bytes(b"media")
    remediator = MultimediaRemediator(
        str(original),
        [
            {
                "id": "captions",
                "rule": "WCAG 1.2.2",
                "severity": "critical",
                "message": "Missing captions/transcription for multimedia content",
            },
            {
                "id": "description",
                "rule": "WCAG 1.2.3",
                "severity": "high",
                "message": "Audio description not provided for visual content",
            },
        ],
        RemediationConfig(use_ai=False),
    )
    caption = tmp_path / "source_remediated.vtt"
    caption.write_text(content, encoding="utf-8")
    remediator._caption_file = str(caption)
    remediator._transcription = [dict(segment) for segment in SEGMENTS]
    remediator._media_duration = 5.2
    remediator._add_fixed_issue(
        remediator.issues[0], fixed_content="Generated captions", fix_method="rule"
    )
    remediator._add_fixed_issue(
        remediator.issues[1], fixed_content="Generated descriptions", fix_method="rule"
    )
    return remediator, str(caption)


def test_verified_captions_are_kept_as_fixed_and_flagged_for_review(tmp_path):
    remediator, caption = _remediator_with_captions(tmp_path)
    verification = remediator._verify_fixes(caption)

    result = remediator.result
    assert verification.passed and result.verification_passed
    assert [fix.issue_id for fix in result.fixed_issues] == ["captions"]
    assert result.fixed_count == 1
    fix = result.fixed_issues[0]
    assert fix.verification_passed is True
    assert fix.needs_review is True
    assert fix.saved_file_verification is None
    assert [issue.issue_id for issue in result.manual_issues] == ["description"]
    assert result.remediated_compliance_score is None
    assert result.score_verification_reason == "incomplete_comparison"


@pytest.mark.parametrize("tamper", ["content", "other_output"])
def test_unverified_captions_stay_manual(tmp_path, tamper):
    content = VTT.replace("biology", "chemistry") if tamper == "content" else VTT
    remediator, caption = _remediator_with_captions(tmp_path, content)
    output = caption if tamper == "content" else str(tmp_path / "source.mp4")
    verification = remediator._verify_fixes(output)

    result = remediator.result
    assert not verification.passed and not result.verification_passed
    assert result.fixed_count == 0
    assert {issue.issue_id for issue in result.manual_issues} == {
        "captions",
        "description",
    }


class _Segment:
    def __init__(self, start, end, text):
        self.start_time, self.end_time, self.text = start, end, text


class _CountingProcessor:
    """Stands in for speech recognition; counts transcriptions."""

    def __init__(self):
        self.transcriptions = 0

    def process_media(self, path, **options):
        if options.get("generate_captions"):
            self.transcriptions += 1
        return type(
            "Result",
            (),
            {
                "duration": 5.2,
                "transcription": [
                    _Segment(0.0, 0.4, "Okay."),
                    _Segment(0.4, 2.0, "Welcome to the <biology> lab & more."),
                    _Segment(2.0, 5.0, "Today we measure plant growth."),
                ],
                "audio_descriptions": [],
            },
        )()


def test_scanner_finding_set_publishes_only_the_missing_caption_fix(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")
    issues = [
        {
            "rule": "WCAG 1.2.2",
            "severity": "critical",
            "message": "Missing captions/transcription for multimedia content",
        },
        {
            "rule": "WCAG 1.2.3",
            "severity": "high",
            "message": "Audio description not provided for visual content",
        },
        {
            "rule": "Caption Quality",
            "severity": "low",
            "message": 'Very short caption segment: "Okay."',
        },
        {
            "rule": "Caption Quality",
            "severity": "low",
            "message": 'Very short caption segment: "Yes."',
        },
    ]
    config = RemediationConfig(
        use_ai=True,
        fix_alt_text=True,
        verify_fixes=True,
        create_backup=False,
        output_directory=str(tmp_path),
    )
    remediator = MultimediaRemediator(str(source), issues, config)
    processor = _CountingProcessor()
    remediator._processor = processor

    result = remediator.remediate()

    assert processor.transcriptions == 1
    assert result.fixed_count == 1
    assert result.fixed_issues[0].description.startswith("Missing captions")
    manual = sorted(issue.description for issue in result.manual_issues)
    assert [d for d in manual if d.startswith("Very short caption")] == [
        'Very short caption segment: "Okay."',
        'Very short caption segment: "Yes."',
    ]
    assert result.output_file.endswith(".vtt")
    content = open(result.output_file, encoding="utf-8").read()
    assert "Welcome to the &lt;biology&gt; lab &amp; more." in content
    assert "<biology>" not in content


def test_raw_cue_markup_is_never_accepted():
    with pytest.raises(ValueError):
        parse_webvtt("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n<b>Hi</b>\n")


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (2.9999999999999996, "00:00:03.000"),
        (0.29, "00:00:00.290"),
        (86400.5, "24:00:00.500"),
    ],
)
def test_timestamps_are_formatted_from_whole_milliseconds(tmp_path, seconds, expected):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")
    remediator = MultimediaRemediator(str(source), [], RemediationConfig(use_ai=False))
    assert remediator._format_timestamp(seconds) == expected
