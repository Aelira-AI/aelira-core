"""Release metadata and upgrade notices must agree for v0.9.4."""

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]
VERSION = "0.9.4"
RELEASE_HEADING = "## [0.9.4] - 2026-08-19"


def test_authoritative_release_versions_are_0_9_4():
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
    # API, worker, and dashboard are all shipped from versioned images.
    assert compose.count(f"${{AELIRA_VERSION:-{VERSION}}}") == 3


def test_security_policy_supports_only_current_patch():
    security = (ROOT / "SECURITY.md").read_text()

    assert re.search(r"\|\s*0\.9\.4\s*\|\s*:white_check_mark:\s*\|", security)
    assert re.search(r"\|\s*<=\s*0\.9\.3\s*\|\s*:x:\s*\|", security)
    assert "current 0.9.x line" not in security


def test_changelog_has_empty_unreleased_and_required_upgrade_notices():
    changelog = (ROOT / "CHANGELOG.md").read_text()
    unreleased = changelog.index("## [Unreleased]")
    release = changelog.index(RELEASE_HEADING)
    historical = changelog.index("## [0.9.3] - 2026-08-18")

    assert changelog[unreleased + len("## [Unreleased]") : release].strip() == ""
    assert unreleased < release < historical

    notes = changelog[release:historical]
    for heading in (
        "### Security",
        "### Fixed",
        "### Changed",
        "### Operator action required",
    ):
        assert heading in notes

    required_phrases = (
        "staff-only",
        "legacy API keys",
        "reauthorization",
        "CANVAS_OAUTH_ALLOWED_ORIGINS",
        "reconnect",
        "SESSION_REPLAY_ENCRYPTION_KEY",
        "Redis",
        "UVICORN_WORKERS=1",
        "501",
        "zero job rows",
        "Monday at 09:00 UTC",
        "No downgrade",
        "pre-upgrade database backup",
        "matching v0.9.3 images",
        "alembic upgrade head",
    )
    for phrase in required_phrases:
        assert phrase in notes


def test_historical_and_dependency_0_9_3_references_remain_intact():
    migration = (
        ROOT / "alembic/versions/2026_08_18_canvas_content_schema.py"
    ).read_text()
    cli_lock = (ROOT / "cli/package-lock.json").read_text()
    dashboard_lock = (ROOT / "dashboard/package-lock.json").read_text()

    assert "published v0.9.3 Canvas-content schema" in migration
    assert '"optionator": "^0.9.3"' in cli_lock
    assert '"optionator": "^0.9.3"' in dashboard_lock


def test_upgrade_guide_warns_that_legacy_api_keys_require_reissue():
    guide = (ROOT / "docs/deployment/self-hosting.md").read_text()

    assert "v0.9.4 upgrade" in guide
    assert "legacy API keys" in guide
    assert "reissue" in guide
    assert "401" in guide
    assert "pre-upgrade database backup" in guide
    assert "matching v0.9.3 images" in guide
    assert "fails closed" in guide
    assert "starts anyway" not in guide
    assert (
        "docker compose -f docker-compose.prod.yml run --rm "
        "--entrypoint alembic api upgrade head"
    ) in guide
    for incomplete in (
        "docker compose --profile",
        "docker compose exec",
        "docker compose build",
        "docker compose run",
        "docker compose up",
        "docker compose logs",
    ):
        assert incomplete not in guide
