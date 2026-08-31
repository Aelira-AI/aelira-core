"""Contracts for the checked-in v0.9.7 release body."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BODY = ROOT / "docs" / "releases" / "v0.9.7.md"
MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
INCLUDED_PULL_REQUESTS = {
    169,
    188,
    189,
    191,
    193,
    194,
    201,
    202,
    203,
    204,
    206,
    207,
    210,
    211,
    239,
    241,
    242,
    243,
    245,
    246,
    249,
    259,
    261,
    264,
    265,
    266,
    267,
    268,
    269,
    270,
    271,
    272,
    273,
    274,
    275,
    276,
    277,
    278,
    279,
    280,
    281,
    282,
    283,
    284,
    285,
    286,
    287,
    288,
    289,
    290,
    291,
}


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_v097_body_is_release_candidate_not_planning_copy():
    body = _text(BODY)

    assert body.startswith("# Aelira v0.9.7\n")
    assert "Draft release scope" not in body
    assert "planned, not implemented" not in body
    assert "v0.9.6 is the latest immutable release" not in body
    assert "Publication remains blocked until the exact merged release commit" in body


def test_v097_body_lists_the_complete_post_v096_change_set():
    body = _text(BODY)
    listed = {
        int(number)
        for number in re.findall(
            r"https://github\.com/Aelira-AI/aelira-core/pull/(\d+)", body
        )
    }

    assert listed == INCLUDED_PULL_REQUESTS


def test_v097_body_preserves_fail_closed_stem_boundaries():
    body = _text(BODY)

    for phrase in (
        "source-bound and fail closed",
        "specialist-specific verification",
        "saved-file reverse verification",
        "approval invalidation",
        "fully resolved typed region graph",
        "Unsupported or ambiguous STEM content remains open for human review",
        "Recognition alone never approves or publishes an artifact",
        "exact candidate at consumption time",
    ):
        assert phrase in body


def test_v097_body_names_breaking_contracts_and_operator_actions():
    body = _text(BODY)

    for phrase in (
        "asynchronous scan handle",
        "HTTP `202` job descriptors",
        "/education/focus-order/analyze",
        "20260831_institution_scope",
        "BYOK_ENCRYPTION_KEY",
        "TRUSTED_PROXY_CIDRS",
        "same 0.9.7 release",
        "manual browser walkthrough",
    ):
        assert phrase in body


def test_v097_release_body_local_links_resolve():
    failures = []
    for target in MARKDOWN_LINK.findall(_text(BODY)):
        target = target.strip().split()[0].strip("<>")
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        relative_target = target.split("#", 1)[0]
        if relative_target and not (BODY.parent / relative_target).resolve().exists():
            failures.append(target)
    assert not failures, "broken relative links: " + ", ".join(failures)
