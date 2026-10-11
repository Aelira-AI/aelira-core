"""Release-candidate metadata and operator notices must agree for v0.9.12."""

import ast
import hashlib
import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]
VERSION = "0.9.12"
RELEASE_HEADING = "## [0.9.12] - 2026-10-11"
RELEASE_BODY = ROOT / "docs/releases/v0.9.12.md"
PREVIOUS_HEADING = "## [0.9.11] - 2026-09-12"


def test_authoritative_release_versions_are_0_9_12():
    readme = (ROOT / "README.md").read_text()
    settings = (ROOT / "src/config/settings.py").read_text()
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    cli_package = json.loads((ROOT / "cli/package.json").read_text())
    cli_lock = json.loads((ROOT / "cli/package-lock.json").read_text())
    cli_readme = (ROOT / "cli/README.md").read_text()
    compose = (ROOT / "docker-compose.prod.yml").read_text()

    assert f"**Status: {VERSION} beta.**" in readme
    assert "A future release may include them" not in readme
    assert f'api_version: str = "{VERSION}"' in settings
    assert project["project"]["version"] == VERSION
    assert cli_package["version"] == VERSION
    assert cli_lock["version"] == VERSION
    assert cli_lock["packages"][""]["version"] == VERSION
    assert f"**Version:** {VERSION}" in cli_readme
    assert compose.count(f"${{AELIRA_VERSION:-{VERSION}}}") == 3


def test_long_form_cli_docs_match_the_release_version():
    for relative in (
        "cli/docs/COMMANDS.md",
        "cli/docs/EXAMPLES.md",
        "cli/docs/TROUBLESHOOTING.md",
    ):
        text = (ROOT / relative).read_text()
        assert f"**Version:** v{VERSION}" in text
        assert f"**CLI Version:** v{VERSION}" in text
        assert "v0.4.0" not in text
        assert "March 17, 2026" not in text
        assert "All features complete - Production ready" not in text


def test_security_policy_supports_only_current_patch():
    security = (ROOT / "SECURITY.md").read_text()

    assert re.search(r"\|\s*0\.9\.12\s*\|\s*:white_check_mark:\s*\|", security)
    assert re.search(r"\|\s*<=\s*0\.9\.11\s*\|\s*:x:\s*\|", security)
    assert "current 0.9.x line" not in security


def _release_notes(document: str, next_heading: str | None = None) -> str:
    start = document.index(RELEASE_HEADING)
    if next_heading is None:
        return document[start:]
    return document[start : document.index(next_heading, start)]


def test_changelog_promotes_v0_9_12_and_preserves_history():
    changelog = (ROOT / "CHANGELOG.md").read_text()
    headings = re.findall(r"^## (.+)$", changelog, re.MULTILINE)
    assert headings[:2] == ["[Unreleased]", RELEASE_HEADING.removeprefix("## ")]
    unreleased = changelog.index("## [Unreleased]")
    release = changelog.index(RELEASE_HEADING)
    previous = changelog.index(PREVIOUS_HEADING)
    historical = changelog.index("## [0.9.8] - 2026-09-08")
    assert unreleased < release < previous < historical
    current_notes = _release_notes(changelog, PREVIOUS_HEADING)
    for phrase in (
        "20260917_unknown_confidence",
        "20260928_pdf_edit_pub",
        "coordinated backup",
        "alembic upgrade head",
        "pending human review",
        "Publication approval remains separate",
    ):
        assert phrase in current_notes


def test_historical_v0_9_11_release_body_is_unchanged():
    body = (ROOT / "docs/releases/v0.9.11.md").read_bytes()
    assert hashlib.sha256(body).hexdigest() == (
        "ab33bfa703407085214be04c1c9bf86f574e936660ffe1cfedfb67c4a8e3d54d"
    )


def test_checked_in_github_release_body_has_operator_notice_and_evidence():
    body = RELEASE_BODY.read_text()

    assert body.startswith(f"# Aelira v{VERSION}\n")
    for phrase in (
        "Release candidate",
        "20260917_unknown_confidence",
        "20260928_pdf_edit_pub",
        "database migration",
        "pause new intake",
        "Drain",
        "coordinated",
        "PostgreSQL",
        "uploads",
        "artifacts",
        "configuration and encryption keys",
        "alembic upgrade head",
        "same release",
        "pre-upgrade database",
        "external writebacks",
        "working file",
        "human approval",
        "unsafe or unsupported output is withheld",
        "linux/amd64",
        "linux/arm64",
        "signed annotated tag",
        "GitHub-verified",
        "ci.yml",
        "protected `release` environment",
        "signed image provenance",
        "npm trusted publishing",
        "seven-file SBOM",
        "consumed verbatim",
    ):
        assert phrase in body
    assert "No database migration" not in body
    assert "has already shipped" in body


def test_current_alembic_head_and_release_migration_chain():
    revisions = {}
    descendants = set()
    for path in (ROOT / "alembic/versions").glob("*.py"):
        values = {}
        for node in ast.parse(path.read_text()).body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                for target in targets:
                    if isinstance(target, ast.Name) and target.id in {
                        "revision",
                        "down_revision",
                    }:
                        values[target.id] = ast.literal_eval(node.value)
        revision = values.get("revision")
        if revision:
            revisions[revision] = values.get("down_revision")
            parents = values.get("down_revision")
            descendants.update(parents if isinstance(parents, tuple) else [parents])

    assert set(revisions) - descendants == {"20260928_pdf_edit_pub"}
    assert revisions["20260928_pdf_edit_pub"] == "20260917_unknown_confidence"
    assert revisions["20260917_unknown_confidence"] == "20260905_visual_analysis"


def test_release_workflow_preflights_and_consumes_exact_checked_in_body():
    workflow = (ROOT / ".github/workflows/release.yml").read_text()
    assignment = 'RELEASE_BODY_PATH="docs/releases/$TAG_NAME.md"'
    body_check = '[ -f "$RELEASE_BODY_PATH" ] && [ -s "$RELEASE_BODY_PATH" ]'

    tag_validation = workflow.index(
        'node --experimental-strip-types scripts/release_channel.ts "$TAG_NAME"'
    )
    preflight_assignment = workflow.index(assignment, tag_validation)
    preflight_check = workflow.index(body_check, preflight_assignment)
    docker_publish = workflow.index("docker-publish:")

    assert tag_validation < preflight_assignment < preflight_check < docker_publish
    assert workflow.count(assignment) == 2
    assert workflow.count(body_check) == 2
    assert '--notes-file "$RELEASE_BODY_PATH"' in workflow
    assert "Generate release notes" not in workflow


def test_historical_and_dependency_references_remain_intact():
    cli_lock = (ROOT / "cli/package-lock.json").read_text()
    dashboard_lock = (ROOT / "dashboard/package-lock.json").read_text()
    changelog = (ROOT / "CHANGELOG.md").read_text()

    assert "## [0.9.7] - 2026-08-31" in changelog
    assert "## [0.9.8] - 2026-09-08" in changelog
    assert "## [0.9.6] - 2026-08-26" in changelog
    assert "## [0.9.5] - 2026-08-22" in changelog
    assert "## [0.9.4] - 2026-08-19" in changelog
    assert '"optionator": "^0.9.3"' in cli_lock
    assert '"optionator": "^0.9.3"' in dashboard_lock
