"""Release metadata and operator notices must agree for v0.11.0."""

import ast
import hashlib
import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]
VERSION = "0.11.0"
RELEASE_HEADING = "## [0.11.0] - 2026-10-28"
RELEASE_BODY = ROOT / "docs/releases/v0.11.0.md"
PREVIOUS_HEADING = "## [0.10.0] - 2026-10-25"
V0913_HEADING = "## [0.9.13] - 2026-10-20"
V0912_HEADING = "## [0.9.12] - 2026-10-15"


def test_authoritative_release_versions_are_0_11_0():
    readme = (ROOT / "README.md").read_text()
    settings = (ROOT / "src/config/settings.py").read_text()
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    cli_package = json.loads((ROOT / "cli/package.json").read_text())
    cli_lock = json.loads((ROOT / "cli/package-lock.json").read_text())
    cli_readme = (ROOT / "cli/README.md").read_text()
    compose = (ROOT / "docker-compose.prod.yml").read_text()

    assert f"**Status: {VERSION} beta.**" in readme
    assert "beta candidate" not in readme
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

    assert re.search(r"\|\s*0\.11\.0\s*\|\s*:white_check_mark:\s*\|", security)
    assert re.search(r"\|\s*<=\s*0\.10\.0\s*\|\s*:x:\s*\|", security)
    assert "current 0.9.x line" not in security


def _release_notes(document: str, next_heading: str | None = None) -> str:
    start = document.index(RELEASE_HEADING)
    if next_heading is None:
        return document[start:]
    return document[start : document.index(next_heading, start)]


def test_changelog_dates_v0_11_0_and_preserves_history():
    changelog = (ROOT / "CHANGELOG.md").read_text()
    headings = re.findall(r"^## (.+)$", changelog, re.MULTILINE)
    assert headings[:2] == ["[Unreleased]", RELEASE_HEADING.removeprefix("## ")]
    assert "Changes after v0.9.12" not in changelog
    assert "## [0.9.12] - 2026-10-11" not in changelog
    assert "## [0.9.13] - 2026-10-11" not in changelog
    assert "## [0.10.0] - 2026-10-11" not in changelog
    unreleased = changelog.index("## [Unreleased]")
    release = changelog.index(RELEASE_HEADING)
    pending = changelog[unreleased + len("## [Unreleased]") : release].strip()
    # Unreleased is empty or holds only Keep a Changelog subsections.
    assert pending == "" or pending.startswith("### ")
    previous = changelog.index(PREVIOUS_HEADING)
    v0913 = changelog.index(V0913_HEADING)
    v0912 = changelog.index(V0912_HEADING)
    v0911 = changelog.index("## [0.9.11] - 2026-09-12")
    historical = changelog.index("## [0.9.8] - 2026-09-08")
    assert unreleased < release < previous < v0913 < v0912 < v0911 < historical
    current_notes = _release_notes(changelog, PREVIOUS_HEADING)
    for phrase in (
        "adds one database migration, `20261028_webhook_secret`",
        "single Alembic head after `20261020_media_captions`",
        "Its downgrade drops nothing",
        "coordinated backup",
        "alembic upgrade head",
        "human approval",
        "Use matching API, worker and dashboard images",
        "Roll back with the matched pre-upgrade database",
        "Microsoft webhook subscriptions created before 0.11.0",
        "POST /microsoft/subscriptions",
        "GHSA-cxmg-mrg8-j7r6",
        "GHSA-cpcw-3584-j8q4",
        "GHSA-5prr-c8m5-63r7",
        "GHSA-724p-6r8m-h85x",
    ):
        assert phrase in current_notes
    assert "adds no database migration" not in current_notes


def test_historical_v0_9_11_release_body_is_unchanged():
    body = (ROOT / "docs/releases/v0.9.11.md").read_bytes()
    assert hashlib.sha256(body).hexdigest() == (
        "ab33bfa703407085214be04c1c9bf86f574e936660ffe1cfedfb67c4a8e3d54d"
    )


def test_historical_v0_9_12_release_body_is_unchanged():
    body = (ROOT / "docs/releases/v0.9.12.md").read_bytes()
    assert hashlib.sha256(body).hexdigest() == (
        "555d4a67be94d7cb221174a73050824f524986621e101170ef4f0c27cc6afd63"
    )


def test_historical_v0_9_13_release_body_is_unchanged():
    body = (ROOT / "docs/releases/v0.9.13.md").read_bytes()
    assert hashlib.sha256(body).hexdigest() == (
        "7ad1cc61e88797a8078b4f9c15e87ef45d24ea2240d746495fa5def844acd972"
    )


def test_historical_v0_10_0_release_body_is_unchanged():
    body = (ROOT / "docs/releases/v0.10.0.md").read_bytes()
    assert hashlib.sha256(body).hexdigest() == (
        "0bcce92d5cef65f880fa31b97693e1990c5ebd48de5afb9775609a4d0fa4e5c2"
    )


def test_checked_in_github_release_body_has_operator_notice_and_evidence():
    body = RELEASE_BODY.read_text()

    assert body.startswith(f"# Aelira v{VERSION}\n")
    assert "release candidate as of" not in body
    assert "open draft" not in body
    assert f"No v{VERSION} tag" not in body
    for phrase in (
        "This release adds one database migration",
        "20261028_webhook_secret",
        "20261020_media_captions",
        "20261018_media_truth",
        "20261017_canvas_scope",
        "20260928_pdf_edit_pub",
        "`client_state_hash`",
        "Its downgrade drops nothing",
        "pause new intake",
        "Drain",
        "coordinated backup",
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
        "Unsafe or unsupported output is withheld",
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
        "[#373]",
        "[#376]",
        "[#375]",
        "[#377]",
        "## Security fixes",
        "security/advisories/GHSA-cxmg-mrg8-j7r6",
        "security/advisories/GHSA-cpcw-3584-j8q4",
        "security/advisories/GHSA-5prr-c8m5-63r7",
        "security/advisories/GHSA-724p-6r8m-h85x",
        "`GET /microsoft/subscriptions`",
        "`POST /microsoft/subscriptions`",
        "`PUBLIC_API_URL`",
        "[#687]",
        "[#840]",
        "## Known limits and open security items",
        "CVE-2026-94283",
        "CVE-2026-94284",
        "CVE-2026-94285",
        "CVE-2026-113022",
        "[#747]",
        "CVE-2026-112036",
        "[#755]",
        "CVE-2026-106576",
        "[#750]",
        "zod to 4.6.7",
        "[#786]",
        "`setuptools` 70.3.0",
        "[#800]",
        "Chrome for Testing",
    ):
        assert phrase in body
    assert "no database migration" not in body.lower()
    assert "Check the workflow result and published artifacts before upgrading" in body


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

    assert set(revisions) - descendants == {"20261028_webhook_secret"}
    assert revisions["20261028_webhook_secret"] == "20261020_media_captions"
    assert revisions["20261020_media_captions"] == "20261018_media_truth"
    assert revisions["20261018_media_truth"] == "20261017_canvas_scope"
    assert revisions["20261017_canvas_scope"] == "20260928_pdf_edit_pub"
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
