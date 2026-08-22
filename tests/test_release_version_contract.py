"""Release-candidate metadata and operator notices must agree for v0.9.5."""

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]
VERSION = "0.9.5"
RELEASE_HEADING = "## [0.9.5] - 2026-08-22"
RELEASE_BODY = ROOT / "docs/releases/v0.9.5.md"
QUARANTINE_REASON = "pre_v0_9_5_job_quarantined"
QUARANTINE_QUERY = (
    "SELECT id, job_type, status, last_error_code, created_at "
    "FROM cloud_job_queue WHERE status = 'failed' AND "
    "last_error_code = 'pre_v0_9_5_job_quarantined' "
    "ORDER BY created_at, id;"
)
WEBSITE_RELEASE_COMMIT = "88d6e717aab852aefd10ca10e8bd49504eeb6d1c"


def test_authoritative_release_versions_are_0_9_5():
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

    assert re.search(r"\|\s*0\.9\.5\s*\|\s*:white_check_mark:\s*\|", security)
    assert re.search(r"\|\s*<=\s*0\.9\.4\s*\|\s*:x:\s*\|", security)
    assert "current 0.9.x line" not in security


def _release_notes(document: str, next_heading: str | None = None) -> str:
    start = document.index(RELEASE_HEADING)
    if next_heading is None:
        return document[start:]
    return document[start : document.index(next_heading, start)]


def test_changelog_has_empty_unreleased_and_v0_9_5_operator_notice():
    changelog = (ROOT / "CHANGELOG.md").read_text()
    unreleased = changelog.index("## [Unreleased]")
    release = changelog.index(RELEASE_HEADING)
    historical = changelog.index("## [0.9.4] - 2026-08-19")

    assert changelog[unreleased + len("## [Unreleased]") : release].strip() == ""
    assert unreleased < release < historical
    _assert_v095_notice(_release_notes(changelog, "## [0.9.4] - 2026-08-19"))


def test_checked_in_github_release_body_has_exact_operator_notice_and_evidence():
    body = RELEASE_BODY.read_text()

    assert body.startswith("# Aelira v0.9.5\n")
    _assert_v095_notice(body)
    assert WEBSITE_RELEASE_COMMIT in body
    assert "through Task24a" in body
    assert "Task23 HEAD" not in body
    assert "15 focused tests" in body
    assert "13 metadata + 2 dependency-policy" in body
    assert "production website and CMS builds" in body
    assert "generator check" in body
    assert "canonical metadata remains v0.9.4" in body
    assert "synthetic immutable v0.9.5 fixture" in body
    assert "seven release SBOM assets" in body


def _assert_v095_notice(notes: str) -> None:
    for heading in ("Security", "Fixed", "Changed", "Operator action required"):
        assert heading in notes
    for phrase in (
        "durable-worker activation",
        "every pre-v0.9.5 pending or processing job",
        "rather than executing it",
        QUARANTINE_REASON,
        QUARANTINE_QUERY,
        "scans, remediations, uploads, and syncs",
        "review",
        "deliberately resubmit",
    ):
        assert phrase in notes


def test_release_workflow_preflights_and_consumes_exact_checked_in_body():
    workflow = (ROOT / ".github/workflows/release.yml").read_text()
    release_body_assignment = 'RELEASE_BODY_PATH="docs/releases/$TAG_NAME.md"'
    release_body_check = '[ -f "$RELEASE_BODY_PATH" ] && [ -s "$RELEASE_BODY_PATH" ]'

    strict_tag_validation = workflow.index(
        'if ! [[ "$TAG_NAME" =~ ^v(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)$ ]]'
    )
    preflight_body_assignment = workflow.index(
        release_body_assignment, strict_tag_validation
    )
    preflight_body_check = workflow.index(release_body_check, preflight_body_assignment)
    docker_publish = workflow.index("docker-publish:")

    assert strict_tag_validation < preflight_body_assignment < preflight_body_check
    assert preflight_body_check < docker_publish
    assert workflow.count(release_body_assignment) == 2
    assert workflow.count(release_body_check) == 2
    assert (
        workflow.count("Missing or empty checked-in release body: $RELEASE_BODY_PATH")
        == 2
    )
    assert '--notes-file "$RELEASE_BODY_PATH"' in workflow
    assert "Generate release notes" not in workflow
    assert 'git log "$PREV_TAG"..HEAD' not in workflow


def test_historical_and_dependency_0_9_4_references_remain_intact():
    cli_lock = (ROOT / "cli/package-lock.json").read_text()
    dashboard_lock = (ROOT / "dashboard/package-lock.json").read_text()
    changelog = (ROOT / "CHANGELOG.md").read_text()

    assert "## [0.9.4] - 2026-08-19" in changelog
    assert '"optionator": "^0.9.3"' in cli_lock
    assert '"optionator": "^0.9.3"' in dashboard_lock


def test_upgrade_guide_retains_v0_9_4_security_actions():
    guide = (ROOT / "docs/deployment/self-hosting.md").read_text()

    assert "v0.9.4 upgrade" in guide
    assert "legacy API keys" in guide
    assert "reissue" in guide
    assert "pre-upgrade database backup" in guide
    assert "matching v0.9.3 images" in guide
    assert (
        "docker compose -f docker-compose.prod.yml run --rm "
        "--entrypoint alembic api upgrade head"
    ) in guide
