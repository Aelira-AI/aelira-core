"""Contracts for the unreleased v0.9.6 planning document."""

import json
import re
import tomllib
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DRAFT = ROOT / "docs" / "releases" / "v0.9.6.md"
STATUS = (
    "Draft release scope. v0.9.6 has not been released. Completed entries below "
    "are present on main after v0.9.5; planned entries remain unavailable until "
    "implemented, reviewed, merged, and included in an authorized immutable release."
)
MERGED_PULL_REQUESTS = {
    163: (
        "fix(release): make SBOM downloads rerun-safe",
        "c413aac6b698e68dd96ae2f4b45c874cebd6e48d",
    ),
    164: (
        "docs: add public document remediation guides",
        "de1ea052c9b6e42572577599bbc08403540c5323",
    ),
    165: (
        "fix(pdf): preserve OCR layer in remediated output",
        "4892ac9eac1cc2713d16468c99e38637d46f65fe",
    ),
    166: (
        "fix(pdf): sanitize accessible HTML output",
        "46be8e4e0faa59ff824281b70dc3749e24f73a34",
    ),
    168: (
        "fix(pdf): bind remediation output publication",
        "e996e082db80058fdd55e3bcab4b9d5617e89ce2",
    ),
}
SNAPSHOT_MAIN_HEAD = "e996e082db80058fdd55e3bcab4b9d5617e89ce2"
MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _section(document: str, heading: str) -> str:
    start = document.index(heading)
    end = document.find("\n## ", start + len(heading))
    return document[start:] if end == -1 else document[start:end]


def test_v096_draft_is_prominently_unreleased_and_keeps_state_boundaries():
    draft = _text(DRAFT)

    assert draft.startswith(
        "# Draft Aelira v0.9.6 release scope\n\n> **Status:** " + STATUS
    )
    for heading in (
        "## Completed on main after v0.9.5",
        "## Planned for v0.9.6",
        "## Future work planned for v0.9.7",
        "## Release and deployment boundary",
    ):
        assert heading in draft

    boundary = _section(draft, "## Release and deployment boundary").lower()
    for phrase in (
        "v0.9.5 is the latest immutable release",
        "does not change version metadata",
        "does not create or publish v0.9.6",
        "does not describe deployed or live behavior",
    ):
        assert phrase in boundary

    lower = draft.lower()
    for overclaim in (
        "v0.9.6 is released",
        "released in v0.9.6",
        "available now in v0.9.6",
        "v0.9.6 is deployed",
        "v0.9.6 is live",
    ):
        assert overclaim not in lower


def test_known_post_v095_snapshot_through_expected_head_is_represented():
    draft = _text(DRAFT)
    completed = _section(draft, "## Completed on main after v0.9.5")
    planned = _section(draft, "## Planned for v0.9.6")

    listed_prs = {
        int(number)
        for number in re.findall(
            r"https://github\.com/Aelira-AI/aelira-core/pull/(\d+)", completed
        )
    }
    assert listed_prs == set(MERGED_PULL_REQUESTS)
    assert (
        "Snapshot basis (verified 2026-08-24): `origin/main` at "
        f"`{SNAPSHOT_MAIN_HEAD}`."
    ) in draft

    for number, (title, commit) in MERGED_PULL_REQUESTS.items():
        url = f"https://github.com/Aelira-AI/aelira-core/pull/{number}"
        assert url in completed
        assert title in completed
        assert commit in completed
        assert url not in planned

    for phrase in (
        "intended SBOM/publication artifacts",
        "canonical public PDF, Office, and LaTeX remediation documentation",
        "OCR-generated searchable text survives",
        "passive allowlist",
        "private, unlinked output claim",
        "Direct, queued, and Brightspace",
    ):
        assert phrase in completed


def test_issue_167_is_planned_unavailable_and_fail_closed():
    draft = _text(DRAFT)
    planned = _section(draft, "## Planned for v0.9.6")

    assert "https://github.com/Aelira-AI/aelira-core/issues/167" in planned
    for phrase in (
        "Verified image-equation to MathML remediation for PDFs",
        "not implemented",
        "page, xref, occurrence identity, image index, and bbox",
        "scanner/category/API normalization",
        "bounded source image validation",
        "purpose-bound",
        "strict image-to-LaTeX response parsing",
        "latex2mathml",
        "round-trip verification",
        "parse, provider, conversion, renderer, comparison, policy, or audit failure",
        "no `<mtext>` fallback",
        "content-associated `/Formula`, `/Alt`, and embedded MathML `/AF`",
        "parity with text-layer LaTeX output",
        "0.55",
        "needs_review=True",
        "explicit human acceptance",
        "hosted and local-provider support",
        "detection-plus-manual",
        "exact-byte publication",
        "printed, standalone, single-equation images",
        "addressable by xref/bbox",
        "Full-page region discovery",
        "multi-equation screenshots",
        "vector-only regions without localization",
        "handwriting",
        "chemical structures",
        "commutative diagrams",
        "mixed STEM visuals",
    ):
        assert phrase in planned

    for false_state in ("merged", "available", "implemented on main"):
        assert false_state not in planned.lower()


def test_v097_general_stem_scope_is_explicitly_future_work():
    future = _section(_text(DRAFT), "## Future work planned for v0.9.7")

    assert "not part of v0.9.6" in future
    assert "not implemented or available" in future
    for candidate in (
        "printed_equation",
        "handwritten_equation",
        "multi_equation_region",
        "vector_equation",
        "chemical_formula",
        "chemical_structure",
        "commutative_diagram",
        "unknown_math_visual",
    ):
        assert candidate in future


def test_version_fields_and_immutable_v095_material_remain_unchanged():
    project = tomllib.loads(_text(ROOT / "pyproject.toml"))
    cli_package = json.loads(_text(ROOT / "cli/package.json"))
    cli_lock = json.loads(_text(ROOT / "cli/package-lock.json"))
    settings = _text(ROOT / "src/config/settings.py")
    compose = _text(ROOT / "docker-compose.prod.yml")

    assert project["project"]["version"] == "0.9.5"
    assert cli_package["version"] == "0.9.5"
    assert cli_lock["version"] == "0.9.5"
    assert cli_lock["packages"][""]["version"] == "0.9.5"
    assert 'api_version: str = "0.9.5"' in settings
    assert compose.count("${AELIRA_VERSION:-0.9.5}") == 3

    assert (
        sha256((ROOT / "docs" / "releases" / "v0.9.5.md").read_bytes()).hexdigest()
        == "e5597b26ea8061e8d9afdb38af1f1f0a5886dbe23f55eda0b916b928acea8193"
    )

    changelog = _text(ROOT / "CHANGELOG.md")
    v095 = changelog.index("## [0.9.5] - 2026-08-22")
    v094 = changelog.index("## [0.9.4] - 2026-08-19", v095)
    assert "## [0.9.6]" not in changelog
    assert (
        sha256(changelog[v095:v094].encode()).hexdigest()
        == "9172e9e4c8376e5e42d831c697598dea4ef1305c6d4b740ef0655ecf3ee905ab"
    )


def test_release_planning_preserves_public_terminology_contracts():
    readme = _text(ROOT / "README.md")
    branding = _text(ROOT / "BRANDING.md")

    assert "**Status: 0.9.5 beta.**" in readme
    assert "LMS integration maturity varies by platform" in readme
    assert "## Four equal product pillars" in readme
    for pillar in ("**documents**", "**LMS**", "**web**", "**media**"):
        assert pillar in readme
    assert "the point of the open core" in branding


def test_v096_draft_local_links_resolve():
    failures = []
    for target in MARKDOWN_LINK.findall(_text(DRAFT)):
        target = target.strip().split()[0].strip("<>")
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        relative_target = target.split("#", 1)[0]
        if relative_target and not (DRAFT.parent / relative_target).resolve().exists():
            failures.append(target)
    assert not failures, "broken relative links: " + ", ".join(failures)
