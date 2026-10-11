#!/usr/bin/env python3
"""Export a bounded, replayable, public TIFF CI evidence subset.

This is a copier, not an acceptance test. A failed or incomplete build stays
failed; the JSON status is intentionally present even for partial exports.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

MAX_FILE = 128 * 1024 * 1024
MAX_TOTAL = 512 * 1024 * 1024
MAX_COUNT = 1000
STATUS_LIMIT = 1024 * 1024
VERSION = "4.7.2-0aelira1"
ARCHES = ("amd64", "arm64")
PACKAGES = (
    "libtiff-dev",
    "libtiff-doc",
    "libtiff-opengl",
    "libtiff-tools",
    "libtiff5-dev",
    "libtiff6",
    "libtiffxx6",
)
SOURCE = (
    "rouault-gitlab.gpg",
    "rouault-keyring.gpg",
    "tiff_4.7.0.orig.tar.bz2",
    "tiff_4.7.0-3ubuntu5.debian.tar.xz",
    "tiff_4.7.0-3ubuntu5.dsc",
    "tiff_4.7.2.orig.tar.xz",
    "tiff_4.7.2.orig.tar.xz.sig",
)
PROVENANCE = (
    "apt-index-sha256.txt",
    "apt-index-targets.txt",
    "base-image.txt",
    "base-packages.tsv",
    "base-pull.log",
    "frozen-builder-id.txt",
    "prepare-console.log",
    "source-fetch.log",
    "source-hashes.log",
    "source-input.sha256",
    "toolchain-debs.sha256",
    "toolchain-delta.tsv",
    "toolchain-packages.tsv",
    "ubuntu-archive-keyring.gpg",
    "upstream-signature.log",
    "apt-update.log",
    "tool-install.log",
    "toolchain-download.log",
)
BUILD_LOGS = (
    "SHA256SUMS",
    "build.log",
    "console.log",
    "source-build.log",
    "source-unpack.log",
    "ubuntu-binary-chain.log",
    "ubuntu-source-chain.log",
)
REQUIRED = {
    "reproducible-sha256.txt",
    *(f"source-input/{name}" for name in SOURCE),
    *(
        f"provenance/{name}"
        for name in PROVENANCE
        if name
        not in (
            "base-pull.log",
            "apt-update.log",
            "tool-install.log",
            "toolchain-download.log",
            "source-fetch.log",
        )
    ),
    *(f"build-{run}/{name}" for run in "ab" for name in BUILD_LOGS),
    "build-a/installed-console.log",
}
HEX40 = re.compile(r"^[a-f0-9]{40}$")
HEX64 = re.compile(r"^[a-f0-9]{64}$")
DEB_NAME = re.compile(
    r"^[a-z0-9][a-z0-9+.-]*_(?:%3a|[A-Za-z0-9.+:~\-])+_(?:all|amd64|arm64)\.deb$"
)
INDEX_NAME = re.compile(
    r"^(?:(?:archive\.ubuntu\.com_ubuntu|ports\.ubuntu\.com_ubuntu-ports)_dists_resolute(?:-updates|-security|-backports)?"
    r"|security\.ubuntu\.com_ubuntu_dists_resolute-security)_"
    r"(?:InRelease|(?:main|universe|restricted|multiverse)_(?:source_Sources|binary-(?:amd64|arm64)_Packages)\.lz4)$"
)


def regular(root: Path, relative: str) -> Path:
    """Check every component with lstat; never follow source links."""
    if not relative or any(part in ("", ".", "..") for part in relative.split("/")):
        raise ValueError(f"unsafe path: {relative}")
    current = root
    for part in relative.split("/"):
        current = current / part
        mode = current.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise ValueError(f"symlink: {relative}")
    if not stat.S_ISREG(current.lstat().st_mode):
        raise ValueError(f"not a regular file: {relative}")
    return current


def safe_directory(root: Path, relative: str) -> Path:
    """Do not enumerate a directory reached through any symlink component."""
    current = root
    for part in relative.split("/"):
        current = current / part
        mode = current.lstat().st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise ValueError(f"unsafe evidence directory: {relative}")
    return current


def manifest_names(
    root: Path,
    relative: str,
    directory: str,
    pattern: re.Pattern[str],
    maximum: int,
    arch: str,
) -> list[str]:
    path = regular(root, relative)
    if path.stat().st_size > min(maximum, 1024 * 1024):
        raise ValueError(f"oversized manifest: {relative}")
    names: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([a-f0-9]{64})  (?:\./)?([^/]+)", line)
        if not match or not pattern.fullmatch(match.group(2)):
            raise ValueError(f"invalid manifest entry: {relative}")
        if directory.endswith("toolchain-debs") and not match.group(2).endswith(
            (f"_{arch}.deb", "_all.deb")
        ):
            raise ValueError(f"wrong architecture in manifest: {relative}")
        if (
            directory.endswith("apt-lists")
            and "_binary-" in match.group(2)
            and f"_binary-{arch}_" not in match.group(2)
        ):
            raise ValueError(f"wrong architecture in manifest: {relative}")
        names.append(f"{directory}/{match.group(2)}")
    if not names or len(names) > MAX_COUNT or len(set(names)) != len(names):
        raise ValueError(f"empty, duplicate, or excessive manifest: {relative}")
    return names


def git_identity(repo: Path, revision: str, tree: str) -> None:
    for value in (revision, tree):
        if not HEX40.fullmatch(value):
            raise ValueError("revision and tree must be 40 lowercase hex characters")
    for ref, expected in (("HEAD", revision), ("HEAD^{tree}", tree)):
        actual = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--verify", ref],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if actual != expected:
            raise ValueError(f"checkout {ref} differs from supplied identity")
    dirty = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "status",
            "--porcelain",
            "--",
            "runtime-security/media/tiff",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    # The exporter itself can be under review; release CI must run from a clean checkout.
    if dirty:
        raise ValueError("TIFF recipe tree is not clean in checkout")


def file_plan(arch: str) -> set[str]:
    paths = {"reproducible-sha256.txt"}
    paths.update(f"source-input/{name}" for name in SOURCE)
    paths.update(f"provenance/{name}" for name in PROVENANCE)
    for run in "ab":
        paths.update(f"build-{run}/{name}" for name in BUILD_LOGS)
        paths.update(
            f"build-{run}/{name}"
            for name in (
                "tiff_4.7.0.orig.tar.bz2",
                "tiff_4.7.0-3ubuntu5.debian.tar.xz",
                "tiff_4.7.0-3ubuntu5.dsc",
                "tiff_4.7.2.orig.tar.xz",
                f"tiff_{VERSION}.debian.tar.xz",
                f"tiff_{VERSION}.dsc",
                f"tiff_{VERSION}_{arch}.buildinfo",
                f"tiff_{VERSION}_{arch}.changes",
                f"tiff_{VERSION}_source.buildinfo",
                f"tiff_{VERSION}_source.changes",
            )
        )
        for package in PACKAGES:
            package_arch = "all" if package == "libtiff-doc" else arch
            paths.add(f"build-{run}/{package}_{VERSION}_{package_arch}.deb")
    paths.add("build-a/installed-console.log")
    return paths


def proof_errors(dest: Path, files: list[dict[str, object]], arch: str) -> list[str]:
    """Check exported proof, without executing any downloaded metadata."""
    present = {str(item["path"]): str(item["sha256"]) for item in files}
    errors: list[str] = []
    manifest_counts: dict[str, int] = {}
    for manifest, directory in (
        ("provenance/toolchain-debs.sha256", "provenance/toolchain-debs"),
        ("provenance/apt-index-sha256.txt", "provenance/apt-lists"),
        ("provenance/source-input.sha256", "source-input"),
    ):
        if manifest not in present:
            continue
        lines = (dest / manifest).read_text(encoding="utf-8").splitlines()
        valid = True
        for line in lines:
            match = re.fullmatch(r"([a-f0-9]{64})  (?:\./)?([^/]+)", line)
            if not match or present.get(f"{directory}/{match.group(2)}") != match.group(
                1
            ):
                errors.append(f"manifest hash or file mismatch: {manifest}")
                valid = False
                break
        if valid:
            manifest_counts[manifest] = len(lines)
    toolchain_count = manifest_counts.get("provenance/toolchain-debs.sha256", -1)
    for run in "ab":
        for name, expected in (
            ("ubuntu-source-chain.log", "ubuntu_source_chain=pass releases=4"),
            (
                "ubuntu-binary-chain.log",
                f"ubuntu_binary_chain=pass packages={toolchain_count} releases=4",
            ),
        ):
            relative = f"build-{run}/{name}"
            if (
                relative in present
                and expected
                not in (dest / relative).read_text(encoding="utf-8").splitlines()
            ):
                errors.append(f"missing acceptance marker: {relative}")
        relative = f"build-{run}/build.log"
        if relative in present:
            data = (dest / relative).read_text(encoding="utf-8", errors="replace")
            for marker in (
                "# TOTAL: 109",
                "# PASS:  109",
                "# SKIP:  0",
                "# FAIL:  0",
                "# ERROR: 0",
            ):
                if marker not in data.splitlines():
                    errors.append(f"missing acceptance marker: {relative}: {marker}")
    installed = "build-a/installed-console.log"
    marker = f"installed_tiff_acceptance=pass architecture={arch} version={VERSION}"
    if (
        installed in present
        and marker not in (dest / installed).read_text(encoding="utf-8").splitlines()
    ):
        errors.append(f"missing acceptance marker: {installed}")
    for package in PACKAGES:
        package_arch = "all" if package == "libtiff-doc" else arch
        name = f"{package}_{VERSION}_{package_arch}.deb"
        a, b = f"build-a/{name}", f"build-b/{name}"
        if a not in present or b not in present:
            errors.append(f"missing binary archive pair: {name}")
        elif present[a] != present[b]:
            errors.append(f"nonidentical binary archive pair: {name}")
    for name in (
        "tiff_4.7.0-3ubuntu5.dsc",
        f"tiff_{VERSION}.dsc",
        "tiff_4.7.0-3ubuntu5.debian.tar.xz",
        f"tiff_{VERSION}.debian.tar.xz",
    ):
        a, b = f"build-a/{name}", f"build-b/{name}"
        if a not in present or b not in present or present[a] != present[b]:
            errors.append(f"missing or nonidentical source archive pair: {name}")
    if not any(name.startswith("provenance/apt-lists/") for name in present):
        errors.append("signed index files absent")
    if not any(name.startswith("provenance/toolchain-debs/") for name in present):
        errors.append("toolchain package files absent")
    return errors


def unknown_artifacts(source: Path, planned: set[str]) -> list[str]:
    """Reject unlisted archive-like files in the controlled output directories."""
    errors: list[str] = []
    for directory in (
        "source-input",
        "build-a",
        "build-b",
        "provenance/toolchain-debs",
        "provenance/apt-lists",
    ):
        try:
            folder = safe_directory(source, directory)
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
            continue
        for entry in folder.iterdir():
            relative = f"{directory}/{entry.name}"
            if (
                relative in planned
                or entry.is_dir()
                or entry.name in ("rouault-keyring.gpg~",)
            ):
                continue
            if directory.startswith("build-") and entry.name.endswith(".ddeb"):
                continue  # Debug packages are intentionally excluded from replay evidence.
            if (
                directory == "source-input"
                or directory.startswith("provenance/")
                or entry.name.endswith(
                    (".deb", ".dsc", ".tar.xz", ".tar.bz2", ".buildinfo", ".changes")
                )
            ):
                errors.append(f"unknown evidence name: {relative}")
                if len(errors) >= 100:
                    errors.append(
                        "additional unknown evidence names were not enumerated"
                    )
                    return errors
    return errors


def copy_file(source: Path, dest: Path, maximum: int) -> tuple[int, str]:
    fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
            raise ValueError(f"oversized or nonregular file: {source.name}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        with os.fdopen(
            os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644), "wb"
        ) as output:
            while chunk := os.read(fd, 1024 * 1024):
                size += len(chunk)
                if size > maximum:
                    raise ValueError(f"file grew beyond limit: {source.name}")
                output.write(chunk)
                digest.update(chunk)
        after = os.fstat(fd)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise ValueError(f"source changed while copying: {source.name}")
        return size, digest.hexdigest()
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    finally:
        os.close(fd)


def export(args: argparse.Namespace) -> int:
    source = Path(args.source)
    dest = Path(args.dest)
    if source.is_symlink() or not source.is_dir() or dest.exists() or dest.is_symlink():
        raise ValueError("source must be a directory and destination must not exist")
    if args.arch not in ARCHES:
        raise ValueError("architecture must be amd64 or arm64")
    if (
        not 0 < args.max_file <= MAX_FILE
        or not STATUS_LIMIT < args.max_total <= MAX_TOTAL
        or not 1 < args.max_count <= MAX_COUNT
    ):
        raise ValueError("budgets cannot exceed production limits")
    identity_error = None
    try:
        git_identity(Path(args.repo), args.revision, args.tree)
    except (ValueError, subprocess.CalledProcessError, OSError) as exc:
        identity_error = str(exc)
    dest.mkdir(parents=True)
    errors: list[str] = []
    if identity_error:
        errors.append(identity_error)
    paths = file_plan(args.arch)
    for manifest, directory, pattern in (
        ("provenance/toolchain-debs.sha256", "provenance/toolchain-debs", DEB_NAME),
        ("provenance/apt-index-sha256.txt", "provenance/apt-lists", INDEX_NAME),
    ):
        try:
            paths.update(
                manifest_names(
                    source, manifest, directory, pattern, args.max_file, args.arch
                )
            )
        except (OSError, ValueError, UnicodeError) as exc:
            errors.append(f"{manifest}: {exc}")
            paths.discard(manifest)
    errors.extend(unknown_artifacts(source, paths))
    exported: list[dict[str, object]] = []
    missing: list[str] = []
    total = 0
    for relative in sorted(paths):
        try:
            path = regular(source, relative)
        except FileNotFoundError:
            if relative in REQUIRED or relative.startswith(("build-a/", "build-b/")):
                missing.append(relative)
            continue
        except (OSError, ValueError) as exc:
            errors.append(f"{relative}: {exc}")
            continue
        size = path.stat().st_size
        if (
            size > args.max_file
            or total + size > args.max_total - STATUS_LIMIT
            or len(exported) >= args.max_count - 1
        ):
            errors.append(f"budget exceeded: {relative}")
            continue
        try:
            copied, digest = copy_file(path, dest / relative, args.max_file)
            total += copied
            exported.append({"path": relative, "bytes": copied, "sha256": digest})
        except (OSError, ValueError) as exc:
            errors.append(f"{relative}: {exc}")
    for relative in REQUIRED:
        if (
            relative not in {item["path"] for item in exported}
            and relative not in missing
        ):
            missing.append(relative)
    if not missing and not errors and args.build_exit == 0:
        try:
            errors.extend(proof_errors(dest, exported, args.arch))
        except (OSError, UnicodeError, ValueError) as exc:
            errors.append(f"proof validation failed: {type(exc).__name__}")
    if args.build_exit != 0:
        errors.append(f"build exited {args.build_exit}")
    status = "complete" if not errors and not missing else "incomplete"
    report = {
        "schema": "aelira-tiff-ci-evidence-v1",
        "status": status,
        "architecture": args.arch,
        "version": VERSION,
        "revision": args.revision,
        "tree": args.tree,
        "build_exit": args.build_exit,
        "limits": {
            "file_bytes": args.max_file,
            "total_bytes_including_status": args.max_total,
            "status_reserved_bytes": STATUS_LIMIT,
            "files_including_status": args.max_count,
        },
        "exported_bytes": total,
        "exported_count": len(exported),
        "files": exported,
        "missing": sorted(missing),
        "errors": errors,
    }
    encoded = json.dumps(report, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    if len(encoded) > STATUS_LIMIT:
        raise ValueError("status exceeds 1 MiB; no truncated report written")
    (dest / "ci-status.json").write_bytes(encoded)
    print(f"tiff_ci_evidence={status} files={len(exported)} bytes={total}")
    return 0 if status == "complete" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--dest", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--arch", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--tree", required=True)
    parser.add_argument("--build-exit", required=True, type=int)
    parser.add_argument(
        "--max-file", type=int, default=MAX_FILE, help=argparse.SUPPRESS
    )
    parser.add_argument(
        "--max-total", type=int, default=MAX_TOTAL, help=argparse.SUPPRESS
    )
    parser.add_argument(
        "--max-count", type=int, default=MAX_COUNT, help=argparse.SUPPRESS
    )
    args = parser.parse_args()
    try:
        return export(args)
    except (ValueError, OSError) as exc:
        print(f"tiff_ci_evidence=error reason={exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
