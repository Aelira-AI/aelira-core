#!/usr/bin/env python3
"""Copy and verify the exact Pillow recipe executed by a component run."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import stat
import subprocess
import sys

from strict_json import load, exact_object

FILES = (
    "README.md",
    "build.sh",
    "build-in-container.sh",
    "ci-evidence.py",
    "fetch-python-inputs.py",
    "install-in-candidate.sh",
    "prepare-native.sh",
    "proof.py",
    "recipe-lock.py",
    "replay-frozen.sh",
    "strict_json.py",
    "test-ci-evidence.py",
    "test-installed.sh",
    "verify-frozen-inputs.py",
    "verify-installed.py",
    "verify-tiff-input.py",
    "verify-wheel.py",
)


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def regular(path: Path) -> None:
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError(f"recipe is not a regular file: {path.name}")


def git(repo: Path, ref: str) -> str:
    return subprocess.check_output(
        ["/usr/bin/git", "-C", str(repo), "rev-parse", "--verify", ref], text=True
    ).strip()


def snapshot(source: Path, target: Path, repo: Path) -> None:
    if target.exists():
        raise ValueError("recipe snapshot destination exists")
    target.mkdir()
    for name in FILES:
        path = source / name
        regular(path)
        shutil.copyfile(path, target / name, follow_symlinks=False)
        (target / name).chmod(0o444)
    payload = {
        "revision": git(repo, "HEAD"),
        "tree": git(repo, "HEAD^{tree}"),
        "files": {name: digest(target / name) for name in FILES},
    }
    (target / "manifest.json").write_text(json.dumps(payload, sort_keys=True) + "\n")
    (target / "manifest.json").chmod(0o444)
    target.chmod(0o555)
    verify(target)


def verify(target: Path) -> None:
    if not stat.S_ISDIR(target.lstat().st_mode):
        raise ValueError("unsafe recipe directory")
    manifest = target / "manifest.json"
    regular(manifest)
    data = exact_object(load(manifest), {"revision", "tree", "files"})
    exact_object(data["files"], set(FILES))
    if {p.name for p in target.iterdir()} != set(FILES) | {"manifest.json"}:
        raise ValueError("unexpected recipe entry")
    for name, expected in data["files"].items():
        path = target / name
        regular(path)
        if digest(path) != expected:
            raise ValueError(f"recipe hash mismatch: {name}")


def live_matches(target: Path, source: Path, repo: Path) -> None:
    verify(target)
    data = load(target / "manifest.json")
    if data["revision"] != git(repo, "HEAD") or data["tree"] != git(
        repo, "HEAD^{tree}"
    ):
        raise ValueError("checkout revision/tree changed")
    for name in FILES:
        regular(source / name)
        if digest(source / name) != data["files"][name]:
            raise ValueError(f"live recipe changed: {name}")


def compare(target: Path, source: Path) -> None:
    verify(target)
    data = load(target / "manifest.json")
    for name in FILES:
        regular(source / name)
        if digest(source / name) != data["files"][name]:
            raise ValueError(f"live recipe changed: {name}")


if __name__ == "__main__":
    command = sys.argv[1]
    if command == "snapshot":
        snapshot(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]))
    elif command == "verify":
        verify(Path(sys.argv[2]))
    elif command == "live":
        live_matches(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]))
    elif command == "compare":
        compare(Path(sys.argv[2]), Path(sys.argv[3]))
    else:
        raise SystemExit(
            "usage: recipe-lock.py snapshot SOURCE TARGET REPO | verify TARGET | live TARGET SOURCE REPO"
        )
