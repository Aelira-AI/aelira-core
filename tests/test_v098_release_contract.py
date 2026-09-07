"""Contracts for the checked-in v0.9.8 release body."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BODY = ROOT / "docs" / "releases" / "v0.9.8.md"
MILESTONE_ISSUES = {
    82,
    83,
    297,
    298,
    299,
    300,
    301,
    302,
    303,
    304,
    305,
    306,
    307,
    308,
    309,
    310,
    330,
    331,
    333,
}


def _body() -> str:
    return BODY.read_text(encoding="utf-8")


def test_v098_body_names_the_complete_milestone_scope():
    listed = {
        int(number)
        for number in re.findall(
            r"https://github\.com/Aelira-AI/aelira-core/issues/(\d+)", _body()
        )
    }
    assert listed == MILESTONE_ISSUES


def test_v098_body_preserves_fail_closed_product_boundaries():
    body = _body()
    for phrase in (
        "fail closed",
        "review-required",
        "without inventing unavailable per-issue attribution",
        "must not be promoted as fixed",
        "exact source bytes",
    ):
        assert phrase in body


def test_v098_body_names_release_and_operator_gates():
    body = _body()
    for phrase in (
        "20260905_visual_analysis",
        "PUBLIC_API_URL",
        "PUBLIC_DASHBOARD_URL",
        "CORS_ORIGINS",
        "same-origin `/api/live`",
        "GitHub-verified signed annotated tag",
        "linux/amd64",
        "linux/arm64",
        "seven-file SBOM",
        "manual browser walkthrough",
        "consumed verbatim",
    ):
        assert phrase in body
