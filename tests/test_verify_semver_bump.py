"""The release gate must refuse a version number that understates its content."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "verify_semver_bump.py"

_spec = importlib.util.spec_from_file_location("verify_semver_bump", SCRIPT)
gate = importlib.util.module_from_spec(_spec)
sys.modules["verify_semver_bump"] = gate
_spec.loader.exec_module(gate)

FIXED_ONLY = """# Changelog

## [Unreleased]

## [{version}] - 2026-11-01

### Fixed

- A fix.

### Security

- A dependency moves to a fixed release.

### Dependencies

- A package moves.

## [0.9.13] - 2026-10-20

### Added

- Older work.
"""

WITH_FEATURES = """# Changelog

## [Unreleased]

## [{version}] - 2026-11-01

### Changed

- A behaviour change.

### Security

- A fix.

### Operator action required

- Do this before upgrading.

## [0.9.13] - 2026-10-20

### Added

- Older work.
"""

BREAKING = """# Changelog

## [{version}] - 2026-11-01

### Changed

- **Breaking:** the scan response drops a field.
"""


def check(version, previous, changelog, migrations=()):
    return gate.check_release(version, previous, changelog, list(migrations))


def test_minor_bump_with_new_features_passes() -> None:
    report = check("0.10.0", "0.9.13", WITH_FEATURES.format(version="0.10.0"))
    assert report.errors == []
    assert report.actual == "minor"
    assert report.required == "minor"


def test_patch_bump_with_the_same_content_is_refused_and_names_headings() -> None:
    report = check("0.9.14", "0.9.13", WITH_FEATURES.format(version="0.9.14"))
    text = "\n".join(report.errors)
    assert report.errors
    assert "'### Changed'" in text
    assert "'### Operator action required'" in text
    assert "'### Security'" not in text
    assert "at least a minor bump" in text
    assert "0.10.0" in text


@pytest.mark.parametrize(
    "heading", ["Added", "Changed", "Removed", "Deprecated", "Operator action required"]
)
def test_each_non_patch_heading_refuses_a_patch(heading: str) -> None:
    changelog = (
        f"## [0.9.14] - 2026-11-01\n\n### Fixed\n\n- x\n\n### {heading}\n\n- y\n"
    )
    report = check("0.9.14", "0.9.13", changelog)
    assert any(f"'### {heading}'" in line for line in report.errors)


def test_fixed_security_dependencies_only_passes_as_a_patch() -> None:
    report = check("0.9.14", "0.9.13", FIXED_ONLY.format(version="0.9.14"))
    assert report.errors == []
    assert report.actual == "patch"
    assert report.required == "patch"


def test_entries_left_under_unreleased_refuse_the_release() -> None:
    changelog = FIXED_ONLY.format(version="0.9.14").replace(
        "## [Unreleased]\n",
        "## [Unreleased]\n\n### Security\n\n- A fix merged after the section was written.\n",
    )
    report = check("0.9.14", "0.9.13", changelog)
    text = "\n".join(report.errors)
    assert "still has entries under '## [Unreleased]'" in text
    assert "'## [0.9.14]'" in text


def test_a_missing_unreleased_section_does_not_refuse_the_release() -> None:
    changelog = FIXED_ONLY.format(version="0.9.14").replace("## [Unreleased]\n\n", "")
    assert "Unreleased" not in changelog
    assert check("0.9.14", "0.9.13", changelog).errors == []


def test_an_added_migration_refuses_a_patch_and_names_the_file() -> None:
    migration = "alembic/versions/20261101_new_table.py"
    report = check("0.9.14", "0.9.13", FIXED_ONLY.format(version="0.9.14"), [migration])
    text = "\n".join(report.errors)
    assert report.errors
    assert migration in text
    assert "at least a minor bump" in text


def test_a_migration_is_fine_in_a_minor() -> None:
    report = check(
        "0.10.0",
        "0.9.13",
        WITH_FEATURES.format(version="0.10.0"),
        ["alembic/versions/20261101_new_table.py"],
    )
    assert report.errors == []


@pytest.mark.parametrize(
    ("version", "previous"),
    [
        ("0.9.13", "0.9.13"),  # same
        ("0.9.12", "0.9.13"),  # lower
        ("0.8.0", "0.9.13"),  # lower minor
        ("0.9.15", "0.9.13"),  # skipped patch
        ("0.11.0", "0.9.13"),  # skipped minor
        ("0.10.1", "0.9.13"),  # minor bump that does not reset the patch
        ("1.1.0", "0.9.13"),  # major bump that is not x.0.0
        ("1.4.4", "1.4.2"),
    ],
)
def test_non_increasing_or_skipped_versions_are_refused(version, previous) -> None:
    report = check(version, previous, FIXED_ONLY.format(version=version))
    assert len(report.errors) == 1
    assert report.errors[0].startswith(f"error: {version} ")


def test_missing_or_empty_changelog_section_is_refused() -> None:
    assert "no section" in check("0.10.0", "0.9.13", "## [0.9.13] - x\n").errors[0]
    empty = "## [0.10.0] - 2026-11-01\n\nSee the notes.\n\n## [0.9.13] - x\n"
    assert "no '###' entries" in check("0.10.0", "0.9.13", empty).errors[0]


def test_breaking_marker_forces_a_major_from_one_point_zero() -> None:
    refused = check("1.5.0", "1.4.2", BREAKING.format(version="1.5.0"))
    assert refused.required == "major"
    assert any("Breaking" in line for line in refused.errors)
    assert "2.0.0" in "\n".join(refused.errors)
    assert check("2.0.0", "1.4.2", BREAKING.format(version="2.0.0")).errors == []


def test_breaking_marker_needs_only_a_minor_while_the_major_is_zero() -> None:
    assert check("0.10.0", "0.9.13", BREAKING.format(version="0.10.0")).errors == []
    refused = check("0.9.14", "0.9.13", BREAKING.format(version="0.9.14"))
    assert refused.required == "minor"
    assert refused.errors


def test_breaking_in_plain_prose_is_not_a_marker() -> None:
    changelog = "## [1.5.0] - x\n\n### Fixed\n\n- Fixes a breaking layout.\n"
    assert check("1.5.0", "1.4.2", changelog).errors == []


def test_alpha_is_checked_only_for_a_valid_base_version() -> None:
    assert check("0.10.0-alpha.1", "0.9.13", "").errors == []
    assert check("0.11.0-alpha.1", "0.9.13", "").errors


def _git(repo: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
    }
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    ).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "alembic/versions").mkdir(parents=True)
    (tmp_path / "alembic/versions/20261001_old.py").write_text("revision = 'a'\n")
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "release")
    for tag in ("v0.9.12", "v0.9.13", "v0.10.0-alpha.1"):
        _git(tmp_path, "tag", tag)
    return tmp_path


def _cli(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(repo), *args],
        text=True,
        capture_output=True,
        check=False,
    )


def test_cli_finds_the_previous_stable_tag_and_ignores_alphas(repo: Path) -> None:
    (repo / "CHANGELOG.md").write_text(FIXED_ONLY.format(version="0.9.14"))
    result = _cli(repo, "--version", "v0.9.14")
    assert result.returncode == 0, result.stderr
    assert "patch bump from 0.9.13" in result.stdout


def test_cli_refuses_a_patch_when_a_migration_was_added_since_the_tag(
    repo: Path,
) -> None:
    (repo / "alembic/versions/20261101_new.py").write_text("revision = 'b'\n")
    (repo / "CHANGELOG.md").write_text(FIXED_ONLY.format(version="0.9.14"))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "migration")
    result = _cli(repo, "--version", "0.9.14")
    assert result.returncode == 1
    assert "alembic/versions/20261101_new.py" in result.stderr
    assert "20261001_old.py" not in result.stderr
    (repo / "CHANGELOG.md").write_text(WITH_FEATURES.format(version="0.10.0"))
    assert _cli(repo, "--version", "0.10.0").returncode == 0


def test_cli_reports_a_repo_without_tags_as_unable_to_run(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "CHANGELOG.md").write_text(FIXED_ONLY.format(version="0.9.14"))
    result = _cli(tmp_path, "--version", "0.9.14")
    assert result.returncode == 2
    assert "no previous release tag" in result.stderr


def test_preview_reports_the_bump_and_never_fails(repo: Path) -> None:
    (repo / "CHANGELOG.md").write_text(
        "## [Unreleased]\n\n### Added\n\n- A feature.\n\n### Fixed\n\n- A fix.\n"
    )
    result = _cli(repo, "--preview")
    assert result.returncode == 0
    assert "must be at least a minor bump from v0.9.13: 0.10.0" in result.stdout
    assert "'### Added'" in result.stdout
    (repo / "CHANGELOG.md").write_text("## [Unreleased]\n\n### Security\n\n- A fix.\n")
    assert "at least a patch bump" in _cli(repo, "--preview").stdout
    (repo / "CHANGELOG.md").write_text("## [Unreleased]\n")
    assert "No unreleased changes" in _cli(repo, "--preview").stdout


def _as_tagged(changelog: str) -> str:
    """The changelog as its release tag saw it, with Unreleased empty.

    The gate refuses a tag while Unreleased has entries, but main keeps
    gaining entries after a release, so these tests check the released
    sections without them.
    """
    lines = changelog.splitlines(keepends=True)
    start = next(
        i for i, line in enumerate(lines) if line.startswith("## [Unreleased]")
    )
    end = next(i for i in range(start + 1, len(lines)) if lines[i].startswith("## ["))
    return "".join([*lines[: start + 1], "\n", *lines[end:]])


# The CI test job checks out without tags, so these run the decision on the
# real CHANGELOG directly. The tag lookup and the migration diff are covered
# by the temporary-repository tests above.
REAL_CHANGELOG = _as_tagged((ROOT / "CHANGELOG.md").read_text())


def test_as_tagged_empties_only_the_unreleased_section() -> None:
    live = "# Changelog\n\n## [Unreleased]\n\n### Security\n\n- Merged later.\n\n## [0.11.0] - 2026-10-28\n\n### Fixed\n\n- Shipped.\n"
    tagged = _as_tagged(live)
    assert gate.changelog_section(tagged, "Unreleased").strip() == ""
    assert gate.changelog_section(tagged, "0.11.0") == gate.changelog_section(
        live, "0.11.0"
    )
    assert gate.check_release("0.11.0", "0.10.0", live, []).errors
    assert gate.check_release("0.11.0", "0.10.0", tagged, []).errors == []


# The migration that 0.11.0 adds since v0.10.0, as the tag diff reports it.
MIGRATIONS_SINCE_V0_10_0 = ["alembic/versions/2026_10_28_webhook_client_state.py"]


def test_real_changelog_passes_as_0_11_0_after_0_10_0() -> None:
    report = gate.check_release(
        "0.11.0", "0.10.0", REAL_CHANGELOG, MIGRATIONS_SINCE_V0_10_0
    )
    assert report.errors == []
    assert report.actual == "minor"
    assert report.required == "minor"


def test_real_changelog_passes_as_0_11_1_after_0_11_0() -> None:
    report = gate.check_release("0.11.1", "0.11.0", REAL_CHANGELOG, [])
    assert report.errors == []
    assert report.actual == "patch"
    assert report.required == "patch"


def test_negative_control_0_11_1_is_refused_with_a_migration() -> None:
    report = gate.check_release(
        "0.11.1", "0.11.0", REAL_CHANGELOG, MIGRATIONS_SINCE_V0_10_0
    )
    assert report.errors
    assert MIGRATIONS_SINCE_V0_10_0[0] in "\n".join(report.errors)


def test_negative_control_real_changelog_is_refused_as_0_10_1() -> None:
    renamed = REAL_CHANGELOG.replace("## [0.11.0] - ", "## [0.10.1] - ", 1)
    assert renamed != REAL_CHANGELOG
    report = gate.check_release("0.10.1", "0.10.0", renamed, MIGRATIONS_SINCE_V0_10_0)
    message = "\n".join(report.errors)
    assert "'### Changed'" in message
    assert "'### Operator action required'" in message
    assert MIGRATIONS_SINCE_V0_10_0[0] in message
    assert "0.11.0" in message
    # The migration alone refuses a patch.
    fixed_only = gate.check_release(
        "0.10.1",
        "0.10.0",
        FIXED_ONLY.format(version="0.10.1"),
        MIGRATIONS_SINCE_V0_10_0,
    )
    assert fixed_only.errors
    # Without the rename there is simply no 0.10.1 section to release.
    assert gate.check_release("0.10.1", "0.10.0", REAL_CHANGELOG, []).errors
