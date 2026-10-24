#!/usr/bin/env python3
"""Refuse a release whose version number understates what it contains.

Semantic Versioning only works if the number is checked against the content.
This gate compares the new version with the previous release tag, then reads
the CHANGELOG section for the new version and the migrations added since that
tag, and refuses a bump that is smaller than the content requires.

Rules:
  * The version must increase, and must not skip a number: the next release
    after 0.9.13 is 0.9.14, 0.10.0 or 1.0.0, nothing else.
  * A patch release carries only `### Fixed`, `### Security` and
    `### Dependencies`. Any other heading (`### Added`, `### Changed`,
    `### Removed`, `### Deprecated`, `### Operator action required`) and any
    Alembic revision added under `alembic/versions/` since the previous tag
    require at least a minor bump.
  * While the major version is 0, a minor bump (0.x.y to 0.(x+1).0) is the
    largest bump a release needs, breaking changes included.
  * From 1.0.0, an entry that starts with `**Breaking:**` anywhere in the
    section requires a major bump. Write the marker as the first words of the
    bullet, for example `- **Breaking:** the /v1/scan response drops "score".`
  * Alpha tags (`vX.Y.Z-alpha.N`) are checked only for a valid base version.

Usage:
    python scripts/verify_semver_bump.py --version 0.10.0
    python scripts/verify_semver_bump.py --version 0.10.0 --previous-tag v0.9.13
    python scripts/verify_semver_bump.py --preview     # what the Unreleased
                                                       # section requires

Exit codes: 0 allowed (or preview), 1 refused, 2 could not run.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

KINDS = ("patch", "minor", "major")
STABLE_TAG = re.compile(r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
VERSION = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-alpha\.(0|[1-9][0-9]*))?$"
)
PATCH_HEADINGS = frozenset({"Fixed", "Security", "Dependencies"})
BREAKING = re.compile(r"^[ \t]*(?:[-*][ \t]+)?\*\*Breaking:\*\*", re.MULTILINE)
MIGRATIONS = "alembic/versions/"

Version = tuple[int, int, int]


class GateError(Exception):
    """The gate could not run (exit code 2)."""


@dataclass
class Report:
    """What a release needs and what the proposed version provides."""

    actual: str | None = None
    required: str = "patch"
    reasons: list[tuple[str, str]] = field(default_factory=list)  # (kind, reason)
    errors: list[str] = field(default_factory=list)


def parse_version(text: str) -> tuple[Version, bool]:
    match = VERSION.fullmatch(text)
    if not match:
        raise GateError(f"not a release version: {text!r}")
    numbers = (int(match[1]), int(match[2]), int(match[3]))
    return numbers, match[4] is not None


def format_version(version: Version) -> str:
    return ".".join(str(part) for part in version)


def bump_kind(previous: Version, new: Version) -> tuple[str | None, str | None]:
    """Return (kind, problem). Exactly one of them is set."""
    if new <= previous:
        return None, (
            f"{format_version(new)} does not increase on " f"{format_version(previous)}"
        )
    if new[0] > previous[0]:
        if new == (previous[0] + 1, 0, 0):
            return "major", None
        expected = format_version((previous[0] + 1, 0, 0))
        return (
            None,
            f"skips numbers: a major bump from {format_version(previous)} is {expected}",
        )
    if new[1] > previous[1]:
        if new == (previous[0], previous[1] + 1, 0):
            return "minor", None
        expected = format_version((previous[0], previous[1] + 1, 0))
        return (
            None,
            f"skips numbers: a minor bump from {format_version(previous)} is {expected}",
        )
    if new == (previous[0], previous[1], previous[2] + 1):
        return "patch", None
    expected = format_version((previous[0], previous[1], previous[2] + 1))
    return (
        None,
        f"skips numbers: a patch bump from {format_version(previous)} is {expected}",
    )


def next_version(previous: Version, kind: str) -> str:
    major, minor, patch = previous
    if kind == "major":
        return format_version((major + 1, 0, 0))
    if kind == "minor":
        return format_version((major, minor + 1, 0))
    return format_version((major, minor, patch + 1))


def changelog_section(changelog: str, name: str) -> str | None:
    """Return the body of `## [name]`, or None when there is no such section."""
    lines = changelog.splitlines()
    start = None
    for index, line in enumerate(lines):
        match = re.match(r"^## \[([^\]]+)\]", line)
        if not match:
            continue
        if start is not None:
            return "\n".join(lines[start:index])
        if match[1] == name:
            start = index + 1
    return "\n".join(lines[start:]) if start is not None else None


def content_requirements(
    section: str, added_migrations: list[str], major_version: int
) -> list[tuple[str, str]]:
    """Each entry is (minimum kind, reason naming the offending content)."""
    found: list[tuple[str, str]] = []
    for heading in re.findall(r"^### (.+?)\s*$", section, re.MULTILINE):
        if heading not in PATCH_HEADINGS:
            found.append(("minor", f"CHANGELOG heading '### {heading}'"))
    for path in added_migrations:
        found.append(("minor", f"migration added: {path}"))
    if BREAKING.search(section):
        kind = "major" if major_version >= 1 else "minor"
        found.append((kind, "CHANGELOG entry marked '**Breaking:**'"))
    return found


def strongest(requirements: list[tuple[str, str]]) -> str:
    kinds = [kind for kind, _ in requirements]
    return max(kinds, key=KINDS.index, default="patch")


def check_release(
    new_version: str,
    previous_version: str,
    changelog: str,
    added_migrations: list[str],
) -> Report:
    """Pure decision: no git, no files."""
    report = Report()
    new, alpha = parse_version(new_version)
    previous, previous_alpha = parse_version(previous_version)
    if previous_alpha:
        raise GateError(f"previous release must be stable: {previous_version}")

    kind, problem = bump_kind(previous, new)
    if problem:
        report.errors.append(f"error: {new_version} {problem}")
        return report
    report.actual = kind
    if alpha:
        return report

    section = changelog_section(changelog, new_version)
    if section is None:
        report.errors.append(f"error: CHANGELOG.md has no section '## [{new_version}]'")
        return report
    if not re.search(r"^### ", section, re.MULTILINE):
        report.errors.append(
            f"error: CHANGELOG.md section [{new_version}] has no '###' entries"
        )
        return report

    report.reasons = content_requirements(section, added_migrations, new[0])
    report.required = strongest(report.reasons)
    if KINDS.index(kind) < KINDS.index(report.required):
        needed = next_version(previous, report.required)
        report.errors.append(
            f"error: {new_version} is a {kind} bump from "
            f"{format_version(previous)}, but this release needs at least a "
            f"{report.required} bump:"
        )
        report.errors.extend(
            f"  - {reason}" for need, reason in report.reasons if need != "patch"
        )
        report.errors.append(
            f"Release it as {needed}, or take those entries out of " f"{new_version}."
        )
    return report


def git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as error:
        raise GateError(f"git is unavailable: {error}") from error
    if result.returncode != 0:
        raise GateError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def stable_tags(root: Path) -> list[tuple[Version, str]]:
    tags = []
    for line in git(root, "tag", "--list", "v*").split():
        match = STABLE_TAG.fullmatch(line)
        if match:
            tags.append(((int(match[1]), int(match[2]), int(match[3])), line))
    return sorted(tags)


def previous_tag(root: Path, new_version: str) -> str:
    """The newest stable tag other than the one being released."""
    candidates = [tag for _, tag in stable_tags(root) if tag != f"v{new_version}"]
    if not candidates:
        raise GateError("no previous release tag found; fetch tags (fetch-depth: 0)")
    return candidates[-1]


def added_migrations(root: Path, since: str, head: str) -> list[str]:
    output = git(
        root,
        "diff",
        "--name-only",
        "--diff-filter=A",
        f"{since}..{head}",
        "--",
        MIGRATIONS,
    )
    return sorted(line for line in output.splitlines() if line.endswith(".py"))


def run_gate(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    new_version = args.version.removeprefix("v")
    previous = args.previous_tag or previous_tag(root, new_version)
    if not STABLE_TAG.fullmatch(previous):
        raise GateError(f"previous tag must look like v1.2.3: {previous!r}")
    changelog_path = Path(args.changelog) if args.changelog else root / "CHANGELOG.md"
    changelog = changelog_path.read_text(encoding="utf-8")
    report = check_release(
        new_version,
        previous.removeprefix("v"),
        changelog,
        added_migrations(root, previous, args.head),
    )
    if report.errors:
        print("\n".join(report.errors), file=sys.stderr)
        return 1
    print(
        f"ok: {new_version} is a {report.actual} bump from "
        f"{previous.removeprefix('v')}; its content requires at least "
        f"{report.required}"
    )
    return 0


def run_preview(args: argparse.Namespace) -> int:
    """Say what the Unreleased section requires. Never refuses."""
    root = Path(args.root).resolve()
    try:
        tags = stable_tags(root)
    except GateError as error:
        print(f"notice: cannot preview the next version bump: {error}")
        return 0
    if not tags:
        print(
            "notice: no release tag is available, so the next bump cannot be previewed"
        )
        return 0
    latest, latest_tag = tags[-1]
    changelog_path = Path(args.changelog) if args.changelog else root / "CHANGELOG.md"
    section = changelog_section(
        changelog_path.read_text(encoding="utf-8"), "Unreleased"
    )
    migrations = added_migrations(root, latest_tag, args.head)
    reasons = content_requirements(section or "", migrations, latest[0])
    if not reasons and not (section or "").strip() and not migrations:
        lines = [f"No unreleased changes are recorded since {latest_tag}."]
    else:
        required = strongest(reasons)
        lines = [
            f"The next release must be at least a {required} bump from "
            f"{latest_tag}: {next_version(latest, required)} or later."
        ]
        lines.extend(f"- {reason}" for need, reason in reasons if need != "patch")
    print("\n".join(lines))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("### Semantic version bump\n\n" + "\n".join(lines) + "\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--version", help="new version or tag, for example 0.10.0")
    parser.add_argument(
        "--previous-tag",
        help="previous release tag; default is the newest other stable tag",
    )
    parser.add_argument(
        "--changelog", help="CHANGELOG file; default is CHANGELOG.md in --root"
    )
    parser.add_argument("--root", default=str(ROOT), help="repository root")
    parser.add_argument(
        "--head", default="HEAD", help="ref whose migrations are compared"
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="report what the Unreleased section requires; never fails",
    )
    args = parser.parse_args(argv)
    if not args.preview and not args.version:
        parser.error("--version is required unless --preview is given")
    try:
        return run_preview(args) if args.preview else run_gate(args)
    except (GateError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 0 if args.preview else 2


if __name__ == "__main__":
    sys.exit(main())
