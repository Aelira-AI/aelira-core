#!/usr/bin/env python3
"""Copy a bounded public Pillow proof from a successful native installed run.

This copier never changes an execution result. It refuses unsafe sources and
writes a status receipt to a new destination only after all checks pass.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import proof
from strict_json import load

MAX_FILE = 128 * 1024 * 1024
MAX_TOTAL = 768 * 1024 * 1024
MAX_COUNT = 300
HEX40 = re.compile(r"[a-f0-9]{40}")
HEX64 = re.compile(r"[a-f0-9]{64}")
DEB = re.compile(r"[a-z0-9][a-z0-9+.-]*_[A-Za-z0-9%+.:~_-]+_(?:all|amd64|arm64)\.deb")
INDEX = re.compile(
    r"(?:archive\.ubuntu\.com_ubuntu|ports\.ubuntu\.com_ubuntu-ports|security\.ubuntu\.com_ubuntu)_dists_resolute(?:-updates|-security|-backports)?_(?:InRelease|(?:main|universe|restricted|multiverse)_binary-(?:amd64|arm64)_Packages(?:\.lz4)?)"
)
RECIPE = (
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
CONSUMERS = (
    "src/education/remediation/pdf_remediator.py",
    "src/education/handwritten_math_suitability.py",
)
ACCEPTED_CONSUMERS = {
    "src/education/remediation/pdf_remediator.py": "84200c417654c5bc481a96dc679c6826436825f7eaf48968c5314ba13c1bc8ac",
    "src/education/handwritten_math_suitability.py": "310e239e7837e12e22a3ee14a831f38cf163360959675f7ae0ad149c4a5ddf50",
}
PYTHON_INPUTS = (
    "pillow-12.3.0.tar.gz",
    "pillow-12.3.0.json",
    "pip-26.2.1-py3-none-any.whl",
    "pip-26.2.1.json",
    "setuptools-84.0.0-py3-none-any.whl",
    "setuptools-84.0.0.json",
    "pybind11-3.1.0-py3-none-any.whl",
    "pybind11-3.1.0.json",
)
STATIC = (
    "provenance/ubuntu-archive-keyring.gpg",
    "provenance/apt-index-sha256.txt",
    "provenance/toolchain-debs.sha256",
    "provenance/base-image.txt",
    "provenance/frozen-builder-id.txt",
    "provenance/base-packages.tsv",
    "provenance/toolchain-packages.tsv",
    "provenance/toolchain-delta.tsv",
    "input/tiff/SHA256SUMS",
    "recipe/manifest.json",
    "build-exit.txt",
    "consumer-exit.txt",
    "baseline-image-id.txt",
    "installed-image-id.txt",
    "host-machine.txt",
    "daemon-architecture.txt",
    "consumer-source-sha256.tsv",
    "build-proof.json",
    "installed-proof.json",
)


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def safe_root(path: Path) -> None:
    if not path.is_absolute():
        raise ValueError("evidence root must be absolute")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if stat.S_ISLNK(current.lstat().st_mode):
            raise ValueError("symlink in evidence root")


def safe(root: Path, relative: str, directory: bool = False) -> Path:
    safe_root(root)
    if not relative or any(part in ("", ".", "..") for part in relative.split("/")):
        raise ValueError("unsafe path")
    current = root
    for part in relative.split("/"):
        current /= part
        mode = current.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise ValueError("symlink in evidence path")
    mode = current.lstat().st_mode
    if not (stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)):
        raise ValueError("nonregular evidence path")
    return current


def names(root: Path, directory: str, pattern: re.Pattern[str], arch: str) -> list[str]:
    folder = safe(root, directory, True)
    result = []
    for entry in folder.iterdir():
        name = entry.name
        if not pattern.fullmatch(name):
            raise ValueError("unapproved name in required input directory")
        if "_binary-" in name and f"_binary-{arch}_" not in name:
            raise ValueError("wrong architecture index")
        if name.endswith(".deb") and not name.endswith((f"_{arch}.deb", "_all.deb")):
            raise ValueError("wrong architecture package")
        safe(root, f"{directory}/{name}")
        result.append(name)
    if not result:
        raise ValueError("required input directory empty")
    return sorted(result)


def manifest(root: Path, file: str, directory: str, listed: list[str]) -> None:
    lines = safe(root, file).read_text().splitlines()
    actual = {}
    for line in lines:
        match = re.fullmatch(r"([a-f0-9]{64})  (?:\./)?([^/]+)", line)
        if not match or match[2] in actual:
            raise ValueError("unsafe or duplicate hash manifest entry")
        actual[match[2]] = match[1]
    if set(actual) != set(listed):
        raise ValueError("manifest file set mismatch")
    for name, expected in actual.items():
        if sha(safe(root, f"{directory}/{name}")) != expected:
            raise ValueError("input manifest hash mismatch")


def git(repo: Path, ref: str) -> str:
    return subprocess.check_output(
        ["/usr/bin/git", "-C", str(repo), "rev-parse", "--verify", ref], text=True
    ).strip()


def identity(root: Path, repo: Path, revision: str, tree: str) -> dict:
    if not HEX40.fullmatch(revision) or not HEX40.fullmatch(tree):
        raise ValueError("invalid revision/tree")
    if git(repo, "HEAD") != revision or git(repo, "HEAD^{tree}") != tree:
        raise ValueError("checkout revision/tree mismatch")
    dirty = subprocess.check_output(
        [
            "/usr/bin/git",
            "-C",
            str(repo),
            "status",
            "--porcelain",
            "--",
            "runtime-security/media/pillow",
            *CONSUMERS,
        ],
        text=True,
    )
    if dirty:
        raise ValueError("recipe or consumer checkout is not clean")
    data = load(safe(root, "recipe/manifest.json"))
    if (
        set(data) != {"revision", "tree", "files"}
        or data["revision"] != revision
        or data["tree"] != tree
    ):
        raise ValueError("recipe revision/tree mismatch")
    if set(data["files"]) != set(RECIPE):
        raise ValueError("recipe manifest incomplete")
    for name in RECIPE:
        expected = data["files"][name]
        if not HEX64.fullmatch(expected):
            raise ValueError("recipe digest invalid")
        if (
            sha(safe(root, f"recipe/{name}")) != expected
            or sha(safe(repo, f"runtime-security/media/pillow/{name}")) != expected
        ):
            raise ValueError("recipe source hash mismatch")
    return {name: sha(safe(repo, name)) for name in CONSUMERS}


def scalar(root: Path, name: str, pattern: re.Pattern[str]) -> str:
    path = safe(root, name)
    if path.stat().st_size > 256:
        raise ValueError("oversized scalar")
    value = path.read_text().strip()
    if not pattern.fullmatch(value):
        raise ValueError("invalid scalar")
    return value


def check_export_closure(
    root: Path, inventory: list[dict], status_bytes: bytes | None = None
) -> None:
    """Require exactly the copied regular files and their parent directories."""
    safe_root(root)
    if not stat.S_ISDIR(root.lstat().st_mode):
        raise ValueError("non-directory public evidence root")
    expected = {item["path"]: item for item in inventory}
    if len(expected) != len(inventory) or "ci-status.json" in expected:
        raise ValueError("duplicate public evidence path")
    directories = set()
    for relative in expected:
        parts = relative.split("/")
        if any(part in ("", ".", "..") for part in parts):
            raise ValueError("unsafe public evidence path")
        directories.update("/".join(parts[:i]) for i in range(1, len(parts)))
    if status_bytes is not None:
        expected["ci-status.json"] = {
            "bytes": len(status_bytes),
            "sha256": hashlib.sha256(status_bytes).hexdigest(),
        }
    seen_files = set()
    seen_dirs = set()

    def visit(folder: Path, prefix: str = "") -> None:
        with os.scandir(folder) as entries:
            for entry in entries:
                relative = f"{prefix}/{entry.name}" if prefix else entry.name
                mode = entry.stat(follow_symlinks=False).st_mode
                if stat.S_ISDIR(mode):
                    if relative not in directories:
                        raise ValueError("unexpected public evidence directory")
                    seen_dirs.add(relative)
                    visit(Path(entry.path), relative)
                elif stat.S_ISREG(mode):
                    if relative not in expected:
                        raise ValueError("unexpected public evidence file")
                    seen_files.add(relative)
                    item = expected[relative]
                    if (
                        entry.stat(follow_symlinks=False).st_size != item["bytes"]
                        or sha(Path(entry.path)) != item["sha256"]
                    ):
                        raise ValueError("public evidence file changed")
                    if (
                        relative == "ci-status.json"
                        and Path(entry.path).read_bytes() != status_bytes
                    ):
                        raise ValueError("public evidence status changed")
                else:
                    raise ValueError("nonregular public evidence entry")

    visit(root)
    if seen_files != set(expected) or seen_dirs != directories:
        raise ValueError("public evidence closure incomplete")


def export(
    source: Path,
    dest: Path,
    repo: Path,
    revision: str,
    tree: str,
    arch: str,
    max_file: int = MAX_FILE,
    max_total: int = MAX_TOTAL,
    max_count: int = MAX_COUNT,
) -> dict:
    if arch not in ("amd64", "arm64"):
        raise ValueError("unsupported architecture")
    safe_root(source)
    safe_root(repo)
    safe_root(dest.parent)
    if (
        dest.exists()
        or dest.resolve().is_relative_to(source.resolve())
        or source.resolve().is_relative_to(dest.resolve())
    ):
        raise ValueError("destination must be new and separate")
    if not stat.S_ISDIR(source.lstat().st_mode):
        raise ValueError("unsafe source directory")
    producer = proof.check_installed(source, arch)
    consumers = identity(source, repo, revision, tree)
    if consumers != ACCEPTED_CONSUMERS:
        raise ValueError("public consumer source changed")
    candidate_sources = {}
    for line in safe(source, "consumer-source-sha256.tsv").read_text().splitlines():
        parts = line.split("\t")
        if len(parts) != 2 or parts[0] in candidate_sources:
            raise ValueError("invalid candidate consumer source receipt")
        candidate_sources[parts[0]] = parts[1]
    if candidate_sources != consumers:
        raise ValueError("candidate consumer source mismatch")
    host = scalar(source, "host-machine.txt", re.compile(r"aarch64|x86_64"))
    daemon = scalar(
        source, "daemon-architecture.txt", re.compile(r"aarch64|arm64|x86_64|amd64")
    )
    if (host, daemon) not in (
        ("aarch64", "aarch64"),
        ("aarch64", "arm64"),
        ("x86_64", "x86_64"),
        ("x86_64", "amd64"),
    ):
        raise ValueError("host/daemon architecture mismatch")
    if ("arm64" if host == "aarch64" else "amd64") != arch:
        raise ValueError("wrong native architecture")
    build_exit = int(scalar(source, "build-exit.txt", re.compile(r"[0-9]{1,3}")))
    consumer_exit = int(scalar(source, "consumer-exit.txt", re.compile(r"[0-9]{1,3}")))
    if build_exit or consumer_exit:
        raise ValueError(
            f"execution failed: build={build_exit} consumer={consumer_exit}"
        )
    image_re = re.compile(r"sha256:[a-f0-9]{64}")
    baseline = scalar(source, "baseline-image-id.txt", image_re)
    candidate = scalar(source, "installed-image-id.txt", image_re)
    builder = scalar(source, "provenance/frozen-builder-id.txt", image_re)
    base = scalar(
        source, "provenance/base-image.txt", re.compile(r"ubuntu@sha256:[a-f0-9]{64}")
    )
    if (
        producer["baseline_image"] != baseline
        or producer["candidate_image"] != candidate
        or producer["consumer_sources"] != consumers
        or producer["architecture"] != arch
    ):
        raise ValueError("producer/image/source binding mismatch")
    apt = names(source, "input/apt-lists", INDEX, arch)
    debs = names(source, "input/apt-debs", DEB, arch)
    if (
        sum(n.endswith("InRelease") for n in apt) < 1
        or sum("_Packages" in n for n in apt) < 1
    ):
        raise ValueError("signed APT indexes incomplete")
    manifest(source, "provenance/apt-index-sha256.txt", "input/apt-lists", apt)
    manifest(source, "provenance/toolchain-debs.sha256", "input/apt-debs", debs)
    tiff = [
        f"{p}_4.7.2-0aelira1_{arch}.deb"
        for p in ("libtiff6", "libtiffxx6", "libtiff-dev")
    ]
    manifest(source, "input/tiff/SHA256SUMS", "input/tiff", tiff)
    # These package bytes are pinned by verify-tiff-input.py and checked again
    # by the frozen input-chain result from both offline builds.
    py = [f"input/python/{n}" for n in PYTHON_INPUTS]
    for name in py:
        safe(source, name)
    package_version = re.compile(
        r"pillow-12\.3\.0-cp314-cp314-linux_(?:aarch64|x86_64)\.whl"
    )
    expected_tag = "aarch64" if arch == "arm64" else "x86_64"
    wheels = []
    checks = []
    for run in "ab":
        folder = safe(source, f"build-{run}/wheels", True)
        entries = list(folder.iterdir())
        if (
            len(entries) != 1
            or not package_version.fullmatch(entries[0].name)
            or not entries[0].name.endswith(f"linux_{expected_tag}.whl")
        ):
            raise ValueError("wheel filename/architecture mismatch")
        rel = f"build-{run}/wheels/{entries[0].name}"
        wheels.append(rel)
        checks.append(sha(safe(source, rel)))
    if checks[0] != checks[1]:
        raise ValueError("wheels differ")
    if checks[0] != producer["semantics"]["wheel_sha256"]:
        raise ValueError("producer wheel/installed payload mismatch")
    selected = set(STATIC) | set(py) | set(wheels)
    selected |= {f"input/apt-lists/{n}" for n in apt}
    selected |= {f"input/apt-debs/{n}" for n in debs}
    selected |= {f"input/tiff/{n}" for n in tiff}
    selected |= {f"recipe/{n}" for n in RECIPE}
    selected |= {
        f"build-{run}/{name}"
        for run in "ab"
        for name in ("build-packages.tsv", "build-python-packages.txt")
    }
    total = 0
    inventory = []
    for relative in sorted(selected):
        path = safe(source, relative)
        size = path.stat().st_size
        total += size
        if size > max_file or total > max_total or len(inventory) >= max_count:
            raise ValueError("public evidence budget exceeded")
        inventory.append({"path": relative, "bytes": size, "sha256": sha(path)})
    dest.mkdir(parents=True)
    for item in inventory:
        relative = item["path"]
        target = dest / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(safe(source, relative), target, follow_symlinks=False)
        if sha(target) != item["sha256"] or target.stat().st_size != item["bytes"]:
            raise ValueError("copied byte mismatch")
    proof.public_replay(dest, arch)
    if load(dest / "installed-proof.json") != producer:
        raise ValueError("copied installed proof differs")
    if proof.check_installed(source, arch) != producer:
        raise ValueError("source changed during evidence copy")
    if identity(source, repo, revision, tree) != consumers:
        raise ValueError("source binding changed during evidence copy")
    for item in inventory:
        path = safe(source, item["path"])
        if path.stat().st_size != item["bytes"] or sha(path) != item["sha256"]:
            raise ValueError("source changed during evidence copy")
    check_export_closure(dest, inventory)
    receipt = {
        "status": "complete",
        "component": "pillow-source-wheel",
        "architecture": arch,
        "revision": revision,
        "tree": tree,
        "host_machine": host,
        "daemon_architecture": daemon,
        "build_exit": build_exit,
        "consumer_exit": consumer_exit,
        "baseline_image": baseline,
        "candidate_image": candidate,
        "builder_image": builder,
        "base_image": base,
        "recipe_manifest_sha256": sha(safe(source, "recipe/manifest.json")),
        "consumer_source_sha256": consumers,
        "candidate_consumer_source_sha256": candidate_sources,
        "wheel_sha256": checks[0],
        "semantic_checks": producer["semantics"],
        "signed_index_count": len(apt),
        "toolchain_deb_count": len(debs),
        "files": inventory,
        "total_bytes": total,
    }
    status_bytes = (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode()
    status_path = dest / "ci-status.json"
    created_status = False
    try:
        with status_path.open("xb") as stream:
            created_status = True
            stream.write(status_bytes)
        check_export_closure(dest, inventory, status_bytes)
    except BaseException:
        if created_status:
            status_path.unlink(missing_ok=True)
        raise
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--tree", required=True)
    parser.add_argument("--arch", required=True)
    args = parser.parse_args()
    result = export(
        Path(os.path.abspath(args.source)),
        Path(os.path.abspath(args.destination)),
        Path(os.path.abspath(args.repo)),
        args.revision,
        args.tree,
        args.arch,
    )
    print(
        f"native_pillow_public_evidence=pass architecture={args.arch} files={len(result['files'])} bytes={result['total_bytes']} wheel_sha256={result['wheel_sha256']}"
    )


if __name__ == "__main__":
    main()
