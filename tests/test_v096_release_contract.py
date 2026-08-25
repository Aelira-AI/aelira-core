"""Contracts for the checked-in v0.9.6 release body."""

import re
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BODY = ROOT / "docs" / "releases" / "v0.9.6.md"
MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
INCLUDED_PULL_REQUESTS = {163, 164, 165, 166, 168, 171, 172, 173, 174, 175}


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_v096_body_is_release_candidate_not_planning_copy():
    body = _text(BODY)

    assert body.startswith("# Aelira v0.9.6\n")
    assert "Draft release scope" not in body
    assert "planned, not implemented" not in body
    assert "v0.9.5 is the latest immutable release" not in body
    assert "Publication remains blocked until the exact merged release commit" in body


def test_v096_body_lists_the_complete_post_v095_change_set():
    body = _text(BODY)
    listed = {
        int(number)
        for number in re.findall(
            r"https://github\.com/Aelira-AI/aelira-core/pull/(\d+)", body
        )
    }

    assert listed == INCLUDED_PULL_REQUESTS


def test_v096_body_preserves_fail_closed_equation_and_table_boundaries():
    body = _text(BODY)

    for phrase in (
        "purpose-authorized vision provider",
        "confidence at 0.55",
        "always requires human acceptance",
        "64 columns, 10,000 cells, and 200 tables",
        "ragged, merged, unbound, or oversized cases",
        "Missing purpose-bound vision configuration degrades to detection plus manual remediation",
        "Recognition alone never approves or publishes an artifact",
    ):
        assert phrase in body


def test_v095_release_material_remains_immutable():
    assert (
        sha256((ROOT / "docs" / "releases" / "v0.9.5.md").read_bytes()).hexdigest()
        == "e5597b26ea8061e8d9afdb38af1f1f0a5886dbe23f55eda0b916b928acea8193"
    )

    changelog = _text(ROOT / "CHANGELOG.md")
    v095 = changelog.index("## [0.9.5] - 2026-08-22")
    v094 = changelog.index("## [0.9.4] - 2026-08-19", v095)
    assert (
        sha256(changelog[v095:v094].encode()).hexdigest()
        == "9172e9e4c8376e5e42d831c697598dea4ef1305c6d4b740ef0655ecf3ee905ab"
    )


def test_v096_release_body_local_links_resolve():
    failures = []
    for target in MARKDOWN_LINK.findall(_text(BODY)):
        target = target.strip().split()[0].strip("<>")
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        relative_target = target.split("#", 1)[0]
        if relative_target and not (BODY.parent / relative_target).resolve().exists():
            failures.append(target)
    assert not failures, "broken relative links: " + ", ".join(failures)
