"""Release-candidate metadata and operator notices must agree for v0.9.6."""

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]
VERSION = "0.9.6"
RELEASE_HEADING = "## [0.9.6] - 2026-08-26"
RELEASE_BODY = ROOT / "docs/releases/v0.9.6.md"


def test_authoritative_release_versions_are_0_9_6():
    readme = (ROOT / "README.md").read_text()
    settings = (ROOT / "src/config/settings.py").read_text()
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    cli_package = json.loads((ROOT / "cli/package.json").read_text())
    cli_lock = json.loads((ROOT / "cli/package-lock.json").read_text())
    cli_readme = (ROOT / "cli/README.md").read_text()
    compose = (ROOT / "docker-compose.prod.yml").read_text()

    assert f"**Status: {VERSION} beta.**" in readme
    assert f'api_version: str = "{VERSION}"' in settings
    assert project["project"]["version"] == VERSION
    assert cli_package["version"] == VERSION
    assert cli_lock["version"] == VERSION
    assert cli_lock["packages"][""]["version"] == VERSION
    assert f"**Version:** {VERSION}" in cli_readme
    assert compose.count(f"${{AELIRA_VERSION:-{VERSION}}}") == 3


def test_security_policy_supports_only_current_patch():
    security = (ROOT / "SECURITY.md").read_text()

    assert re.search(r"\|\s*0\.9\.6\s*\|\s*:white_check_mark:\s*\|", security)
    assert re.search(r"\|\s*<=\s*0\.9\.5\s*\|\s*:x:\s*\|", security)
    assert "current 0.9.x line" not in security


def _release_notes(document: str, next_heading: str | None = None) -> str:
    start = document.index(RELEASE_HEADING)
    if next_heading is None:
        return document[start:]
    return document[start : document.index(next_heading, start)]


def _assert_v096_notice(notes: str) -> None:
    for heading in (
        "Security",
        "Added",
        "Fixed",
        "Changed",
        "Operator action required",
    ):
        assert heading in notes
    for phrase in (
        "Back up PostgreSQL",
        "alembic upgrade head",
        "20260825_canvas_queue",
        "REMEDIATION_EXECUTION_TIMEOUT_SECONDS",
        "REMEDIATION_TERMINATION_GRACE_SECONDS",
        "worker heartbeat",
        "ai_vision",
        "confidence `0.55`",
        "needs_review=true",
        "human acceptance",
        "pre-v0.9.5 quarantined work",
    ):
        assert phrase in notes


def test_changelog_has_empty_unreleased_and_v0_9_6_operator_notice():
    changelog = (ROOT / "CHANGELOG.md").read_text()
    unreleased = changelog.index("## [Unreleased]")
    release = changelog.index(RELEASE_HEADING)
    historical = changelog.index("## [0.9.5] - 2026-08-22")

    assert changelog[unreleased + len("## [Unreleased]") : release].strip() == ""
    assert unreleased < release < historical
    _assert_v096_notice(_release_notes(changelog, "## [0.9.5] - 2026-08-22"))


def test_checked_in_github_release_body_has_operator_notice_and_evidence():
    body = RELEASE_BODY.read_text()

    assert body.startswith("# Aelira v0.9.6\n")
    _assert_v096_notice(body)
    assert "linux/amd64 Docker" in body
    assert "linux/arm64 Docker" in body
    assert "signed tag" in body
    assert "seven-file SBOM" in body
    assert "consumed verbatim" in body


def test_release_workflow_preflights_and_consumes_exact_checked_in_body():
    workflow = (ROOT / ".github/workflows/release.yml").read_text()
    assignment = 'RELEASE_BODY_PATH="docs/releases/$TAG_NAME.md"'
    body_check = '[ -f "$RELEASE_BODY_PATH" ] && [ -s "$RELEASE_BODY_PATH" ]'

    tag_validation = workflow.index(
        'if ! [[ "$TAG_NAME" =~ ^v(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)$ ]]'
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

    assert "## [0.9.5] - 2026-08-22" in changelog
    assert "## [0.9.4] - 2026-08-19" in changelog
    assert '"optionator": "^0.9.3"' in cli_lock
    assert '"optionator": "^0.9.3"' in dashboard_lock
