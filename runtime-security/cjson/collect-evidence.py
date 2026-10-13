#!/usr/bin/env python3
"""Export declared public cJSON build inputs and typed component receipts."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import subprocess
import sys

MAX_FILE = 2 * 1024 * 1024 * 1024
MAX_TOTAL = 3 * 1024 * 1024 * 1024
INDEX = re.compile(r"(?:archive\.ubuntu\.com_ubuntu|ports\.ubuntu\.com_ubuntu-ports|security\.ubuntu\.com_ubuntu)_dists_resolute(?:-updates|-security|-backports)?_(?:InRelease|(?:main|universe|restricted|multiverse)_source_Sources(?:\.lz4)?)")
RECIPE = ("README.md", "build.sh", "build-in-container.sh", "prepare-native.sh", "test-installed.sh",
          "build-source.py", "test-source.py", "verify-ubuntu-source.py", "test-native.c",
          "changelog-entry.txt", "ubuntu-1.7.19-security-backport.patch")


def regular(root, relative):
    current = root
    for part in Path(relative).parts:
        if part in ("..", "."):
            raise ValueError("Unsafe evidence path")
        current = current / part
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise ValueError("Evidence symlink")
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE:
        raise ValueError("Invalid evidence file")
    return current


def small(root, relative):
    path = regular(root, relative)
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("Receipt exceeds bound")
    return path.read_bytes()


def export(args):
    args.dest.mkdir(mode=0o700)
    rows = {}
    total = 0
    errors = []
    receipt = {"schema": "aelira-cjson-native-evidence-v1", "status": "fail",
               "architecture": args.arch, "revision": args.revision, "tree": args.tree,
               "build_exit": args.build_exit, "scope": "Native cJSON component; final release images require separate acceptance"}

    def copy(relative):
        nonlocal total
        path = regular(args.source, relative)
        total += path.stat().st_size
        if total > MAX_TOTAL or len(rows) >= 200:
            raise ValueError("Evidence bundle exceeds bound")
        destination = args.dest / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        sha = hashlib.sha256()
        with path.open("rb") as incoming, destination.open("xb") as outgoing:
            while chunk := incoming.read(1024 * 1024):
                sha.update(chunk)
                outgoing.write(chunk)
        rows[relative] = {"sha256": sha.hexdigest(), "size": destination.stat().st_size}

    try:
        if args.arch not in ("amd64", "arm64"):
            raise ValueError("Invalid architecture")
        if not args.source.is_absolute() or args.source.resolve() != args.source or not args.source.is_dir():
            raise ValueError("Source directory must be canonical")
        for ref, expected in (("HEAD", args.revision), ("HEAD^{tree}", args.tree)):
            if not re.fullmatch(r"[a-f0-9]{40}", expected):
                raise ValueError("Invalid checkout identity")
            actual = subprocess.check_output(['git', '-C', str(args.repo), 'rev-parse', ref], text=True).strip()
            if actual != expected:
                raise ValueError("Checkout identity mismatch")
        dirty = subprocess.check_output(['git', '-C', str(args.repo), 'status', '--porcelain', '--',
                                         'runtime-security/cjson', '.github/workflows/ci.yml'], text=True)
        if dirty:
            raise ValueError("Recipe checkout is dirty")
        status = json.loads(small(args.source, "build-status.json"))
        receipt["stage"] = status["phase"]
        if status != {"schema": "aelira-cjson-build-status-v1", "phase": "complete", "exit": 0} or args.build_exit != 0:
            raise ValueError("Native build did not complete")
        copy("build-status.json")
        copy("reproducible-sha256.txt")
        for name in RECIPE:
            relative = "frozen-recipe/" + name
            copy(relative)
            expected = hashlib.sha256((args.repo / "runtime-security/cjson" / name).read_bytes()).hexdigest()
            if rows[relative]["sha256"] != expected:
                raise ValueError("Executed recipe differs from checkout")
        for name in ("cjson_1.7.19-2.dsc", "cjson_1.7.19.orig.tar.gz", "cjson_1.7.19-2.debian.tar.xz"):
            copy("source-input/" + name)
        for name in ("base-image.txt", "builder-id.txt", "toolchain-packages.tsv", "ubuntu-archive-keyring.gpg", "recipe-sha256.txt"):
            copy("provenance/" + name)
        builder = small(args.source, "provenance/builder-id.txt").decode().strip()
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", builder):
            raise ValueError("Invalid frozen builder ID")
        receipt["builder_id"] = builder
        indexes = args.source / "provenance/apt-lists"
        if indexes.is_symlink() or not indexes.is_dir():
            raise ValueError("Unsafe index directory")
        for path in sorted(indexes.iterdir()):
            if not INDEX.fullmatch(path.name):
                raise ValueError("Unexpected index")
            copy("provenance/apt-lists/" + path.name)
        if not any(name.endswith('_InRelease') for name in rows):
            raise ValueError("Signed index missing")
        for attempt in "ab":
            prefix = "build-" + attempt + "/"
            controls = json.loads(small(args.source, prefix + "source-controls.json"))
            if controls["schema"] != "aelira-cjson-source-controls-v1" or controls["status"] != "pass" or controls["count"] != 9:
                raise ValueError("Source controls incomplete")
            copy(prefix + "source-controls.json")
            authenticated = json.loads(small(args.source, prefix + "ubuntu-source.json"))
            if authenticated["schema"] != "aelira-cjson-ubuntu-source-v1" or authenticated["status"] != "pass" or not authenticated["records"]:
                raise ValueError("Source authentication incomplete")
            copy(prefix + "ubuntu-source.json")
            for level in ("0", "2"):
                log = small(args.source, prefix + "native-O" + level + ".log").decode()
                if log != "borrowed_parent_allocation_controls=pass successes=574 refusals=194\ncJSON native controls passed\n":
                    raise ValueError("Native sanitizer controls incomplete")
                copy(prefix + "native-O" + level + ".log")
            build_log = small(args.source, prefix + "package-build-private.log").decode()
            if "100% tests passed, 0 tests failed out of 22\n" not in build_log:
                raise ValueError("Upstream tests incomplete")
            names = ("cjson_1.7.19-2+aelira3.dsc", "cjson_1.7.19-2+aelira3.debian.tar.xz", "source-receipt.json")
            for name in names:
                copy(prefix + "artifacts/" + name)
            for package in ("libcjson1", "libcjson-dev"):
                name = package + "_1.7.19-2+aelira3_" + args.arch + ".deb"
                copy(prefix + "artifacts/" + name)
                if attempt == "b" and rows[prefix + "artifacts/" + name]["sha256"] != rows["build-a/artifacts/" + name]["sha256"]:
                    raise ValueError("Package reproducibility mismatch")
            if attempt == "b":
                for name in names[:2]:
                    if rows[prefix + "artifacts/" + name]["sha256"] != rows["build-a/artifacts/" + name]["sha256"]:
                        raise ValueError("Source reproducibility mismatch")
        installed_log = small(args.source, "installed-private.log").decode()
        match = re.findall(r"^installed_cjson_receipt=(.+)$", installed_log, re.MULTILINE)
        if len(match) != 1 or f"installed_cjson_acceptance=pass architecture={args.arch} version=1.7.19-2+aelira3\n" not in installed_log:
            raise ValueError("Installed controls incomplete")
        installed = json.loads(match[0])
        if installed["schema"] != "aelira-cjson-installed-v1" or installed["status"] != "pass" or installed["architecture"] != args.arch or installed["version"] != "1.7.19-2+aelira3":
            raise ValueError("Installed receipt mismatch")
        receipt["installed"] = installed
        receipt["upstream_tests_per_build"] = 22
        receipt["allocation_outcomes_per_native_run"] = 768
        tar = args.source / "provenance/builder.tar"
        receipt["builder_exported"] = tar.exists()
        if args.require_builder and not tar.exists():
            raise ValueError("Frozen builder export missing")
        if tar.exists():
            copy("provenance/builder.tar")
        receipt["status"] = "pass"
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        errors.append(type(error).__name__)
    receipt["errors"] = errors
    receipt["files"] = rows
    (args.dest / "component.json").write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n")
    print(json.dumps({key: receipt[key] for key in ("schema", "status", "architecture", "build_exit", "errors")}))
    return 0 if receipt["status"] == "pass" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "dest", "repo"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("arch", "revision", "tree"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--build-exit", required=True, type=int)
    parser.add_argument("--require-builder", action="store_true")
    sys.exit(export(parser.parse_args()))
