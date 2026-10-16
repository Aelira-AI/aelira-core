"""Contracts for the real faster-whisper download and offline smoke script."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "smoke_whisper_transcription", ROOT / "scripts/smoke_whisper_transcription.py"
)
assert SPEC is not None and SPEC.loader is not None
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


def test_expected_words_ignore_case_and_punctuation() -> None:
    text = "Good captions, make learning ACCESSIBLE for every student!"
    assert smoke.missing_words(text) == []


def test_missing_words_are_reported() -> None:
    assert smoke.missing_words("Good captions make learning easy.") == [
        "accessible",
        "every",
        "student",
    ]


def test_every_expected_word_is_spoken() -> None:
    assert smoke.missing_words(smoke.SPEECH) == []


def test_ci_runs_download_offline_and_refusal_phases_in_the_built_image() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text().replace("\\\n", " ")
    commands = {
        phase: [
            line
            for line in workflow.splitlines()
            if f"smoke_whisper_transcription.py {phase} " in line
        ]
        for phase in ("download", "offline", "offline-refused")
    }
    assert all(len(lines) == 1 for lines in commands.values()), commands
    assert "--network none" not in commands["download"][0]
    for phase in ("offline", "offline-refused"):
        assert "--network none" in commands[phase][0]
