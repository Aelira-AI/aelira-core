#!/usr/bin/env python3
"""Exact frozen-input inventory and externally selected public source binding."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys

ORIGIN = "https://github.com/Aelira-AI/aelira-core.git"
RECIPE = frozenset(
    "Dockerfile.verifier README.md accept-native.sh build-offline-container.sh "
    "compatibility.py consumer-container.sh consumer.py fetch-native.sh "
    "fetch-consumer-wheels.py input-gate.py inputs.sha256 prepare-native.sh "
    "preflight-container.sh run-consumer.sh verify-consumer-wheels.py "
    "verify-apt-closure.py verify-frozen.py verify-installed.py".split()
)
SOURCE = frozenset(
    "requirements.txt src/education/docx_processor.py "
    "src/education/pptx_processor.py src/education/xlsx_processor.py "
    "src/education/remediation/pptx_reading_order.py".split()
)
PYTHON = frozenset(
    "lxml-6.1.3.tar.gz pip-26.2.1-py3-none-any.whl "
    "setuptools-84.2.1-py3-none-any.whl".split()
)
FIXED = frozenset(
    {"architecture.txt", "source-commit.txt", "source-tree.txt"}
    | {"recipe/" + name for name in RECIPE}
    | {"source/" + name for name in SOURCE}
    | {"python/" + name for name in PYTHON}
    | {"keyring/ubuntu-archive-keyring.gpg"}
    | {"verifier/image.tar", "verifier/image-id.txt", "verifier/packages.txt"}
)
PINNED = {
    "lxml": "6.1.3",
    "python-docx": "1.2.0",
    "python-pptx": "1.0.2",
    "openpyxl": "3.1.5",
    "pillow": "13.0.0",
    "setuptools": "84.2.1",
}
HEX = re.compile(r"[0-9a-f]{64}\Z")
GIT_ID = re.compile(r"[0-9a-f]{40}\Z")
PATH = re.compile(r"[A-Za-z0-9._/+%~-]+\Z")
MANIFEST_LINE = re.compile(r"([0-9a-f]{64})  ([A-Za-z0-9._/+%~-]+)\Z")


def fail(message: str) -> None:
    raise SystemExit(message)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def run(*args: str) -> bytes:
    return subprocess.run(args, check=True, stdout=subprocess.PIPE).stdout


def confined(name: str) -> bool:
    parts = PurePosixPath(name).parts
    return (
        bool(PATH.fullmatch(name))
        and str(PurePosixPath(name)) == name
        and not name.startswith("/")
        and all(part not in {"", ".", ".."} for part in parts)
        and "//" not in name
        and "\\" not in name
    )


def files_under(root: Path, *, manifest_exists: bool) -> set[str]:
    result: set[str] = set()
    directories = {""}
    pending = [root]
    while pending:
        directory = pending.pop()
        for entry in os.scandir(directory):
            path = Path(entry.path)
            name = path.relative_to(root).as_posix()
            if name == "frozen.sha256" and manifest_exists:
                if (
                    entry.is_symlink()
                    or not entry.is_file(follow_symlinks=False)
                    or entry.stat(follow_symlinks=False).st_nlink != 1
                ):
                    fail("manifest must be a regular file")
                continue
            if not confined(name):
                fail(f"noncanonical input path: {name}")
            metadata = entry.stat(follow_symlinks=False)
            if stat.S_ISDIR(metadata.st_mode):
                directories.add(name)
                pending.append(path)
            elif stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1:
                result.add(name)
            else:
                fail(f"non-regular, symlink or hard-linked input: {name}")
    permitted = {""}
    for name in result:
        for parent in PurePosixPath(name).parents:
            if str(parent) != ".":
                permitted.add(str(parent))
    if directories != permitted:
        fail(f"unexpected or empty directories: {sorted(directories ^ permitted)}")
    return result


def expected_names(root: Path, observed: set[str]) -> set[str]:
    rows = json.loads((root / "consumer-wheels/pypi-metadata.json").read_text())
    if not isinstance(rows, list) or len(rows) != 8:
        fail("consumer metadata count")
    consumer = {"consumer-wheels/pypi-metadata.json"}
    packages = set()
    for item in rows:
        package = item["package"]
        name = item["filename"]
        if package in packages or not re.fullmatch(r"[A-Za-z0-9_.+-]+\.whl", name):
            fail("duplicate or invalid consumer wheel")
        packages.add(package)
        consumer.add("consumer-wheels/" + name)
    if len(consumer) != 9 or packages != {
        "lxml",
        "python-docx",
        "python-pptx",
        "openpyxl",
        "pillow",
        "XlsxWriter",
        "typing_extensions",
        "et_xmlfile",
    }:
        fail("consumer package set")
    indexes = {name for name in observed if name.startswith("apt-lists/")}
    if not indexes or not all(
        re.fullmatch(
            r"apt-lists/[A-Za-z0-9._+-]+_(?:InRelease|Packages(?:\.lz4|\.gz)?)", name
        )
        for name in indexes
    ):
        fail("unexpected APT index filename")
    if not any(name.endswith("_InRelease") for name in indexes) or not any(
        "_Packages" in name for name in indexes
    ):
        fail("missing signed APT indexes")
    debs = {name for name in observed if name.startswith("debs/")}
    if not debs or not all(
        re.fullmatch(r"debs/[A-Za-z0-9.+%_~:-]+\.deb", name) for name in debs
    ):
        fail("unexpected DEB filename")
    deb_payloads: set[str] = set()
    for name in sorted(debs):
        payload = digest(root / name)
        if payload in deb_payloads:
            fail(f"duplicate DEB payload: {name}")
        deb_payloads.add(payload)
    return set(FIXED) | consumer | indexes | debs


def recipe_digest(recipe: Path) -> str:
    names = files_under(recipe, manifest_exists=False)
    if names != RECIPE:
        fail(f"recipe file set mismatch: {sorted(names ^ RECIPE)}")
    lines = [f"{digest(recipe / name)}  {name}\n" for name in sorted(RECIPE)]
    return hashlib.sha256("".join(lines).encode()).hexdigest()


def inventory(
    root: Path, expected_manifest: str | None, *, create: bool
) -> dict[str, object]:
    if not root.is_dir() or root.is_symlink():
        fail("frozen root must be a directory")
    observed = files_under(root, manifest_exists=not create)
    expected = expected_names(root, observed)
    if observed != expected:
        fail(f"frozen file set mismatch: {sorted(observed ^ expected)}")
    lines = [f"{digest(root / name)}  {name}\n" for name in sorted(observed)]
    actual = "".join(lines)
    manifest = root / "frozen.sha256"
    if create:
        if manifest.exists():
            fail("existing manifest")
        manifest.write_text(actual)
    else:
        if not expected_manifest or not HEX.fullmatch(expected_manifest):
            fail("expected manifest digest required")
        if digest(manifest) != expected_manifest:
            fail("externally selected manifest digest mismatch")
        raw = manifest.read_bytes().decode("ascii")
        parsed = raw.splitlines(keepends=True)
        if any(
            not line.endswith("\n") or not MANIFEST_LINE.fullmatch(line[:-1])
            for line in parsed
        ):
            fail("manifest grammar")
        paths = [MANIFEST_LINE.fullmatch(line[:-1]).group(2) for line in parsed]
        if paths != sorted(set(paths)) or any(not confined(name) for name in paths):
            fail("duplicate, unordered or unsafe manifest path")
        if raw != actual:
            fail("manifest contents or input bytes mismatch")
    return {"manifest_sha256": digest(manifest), "files": len(observed)}


def public_source(root: Path, repo: Path, commit: str, tree: str) -> None:
    if not (GIT_ID.fullmatch(commit) and GIT_ID.fullmatch(tree)):
        fail("expected Git identities must be full SHA-1 hex")
    if (
        run("git", "-C", str(repo), "remote", "get-url", "origin").decode().strip()
        != ORIGIN
    ):
        fail("wrong public origin")
    if run("git", "-C", str(repo), "rev-parse", "HEAD").decode().strip() != commit:
        fail("checkout commit mismatch")
    if (
        run("git", "-C", str(repo), "rev-parse", commit + "^{tree}").decode().strip()
        != tree
    ):
        fail("Git tree mismatch")
    if (root / "source-commit.txt").read_text() != commit + "\n":
        fail("frozen source commit label mismatch")
    if (root / "source-tree.txt").read_text() != tree + "\n":
        fail("frozen source tree label mismatch")
    for name in sorted(SOURCE):
        if (root / "source" / name).read_bytes() != run(
            "git", "-C", str(repo), "show", commit + ":" + name
        ):
            fail(f"public source bytes mismatch: {name}")
    requirements = (root / "source/requirements.txt").read_text()
    for package, version in PINNED.items():
        if (
            len(
                re.findall(
                    r"^" + re.escape(package) + r"==" + re.escape(version) + r"$",
                    requirements,
                    re.MULTILINE | re.IGNORECASE,
                )
            )
            != 1
        ):
            fail(f"public requirement pin mismatch: {package}")
    rows = json.loads((root / "consumer-wheels/pypi-metadata.json").read_text())
    for row in rows:
        if row["package"] in PINNED and row["version"] != PINNED[row["package"]]:
            fail(f"consumer wheel pin mismatch: {row['package']}")


def main() -> None:
    action = sys.argv[1]
    if action == "recipe-id" and len(sys.argv) == 3:
        print(recipe_digest(Path(sys.argv[2])))
    elif action == "freeze" and len(sys.argv) == 3:
        print(
            json.dumps(inventory(Path(sys.argv[2]), None, create=True), sort_keys=True)
        )
    elif action == "inventory" and len(sys.argv) == 4:
        print(
            json.dumps(
                inventory(Path(sys.argv[2]), sys.argv[3], create=False), sort_keys=True
            )
        )
    elif action == "check" and len(sys.argv) == 9:
        (
            _,
            _,
            frozen,
            live_recipe,
            repo,
            commit,
            tree,
            expected_recipe,
            expected_manifest,
        ) = sys.argv
        if recipe_digest(Path(live_recipe)) != expected_recipe:
            fail("externally selected recipe digest mismatch")
        summary = inventory(Path(frozen), expected_manifest, create=False)
        if recipe_digest(Path(frozen) / "recipe") != expected_recipe:
            fail("frozen recipe identity mismatch")
        public_source(Path(frozen), Path(repo), commit, tree)
        print(
            json.dumps(
                {
                    **summary,
                    "source_commit": commit,
                    "source_tree": tree,
                    "recipe_sha256": expected_recipe,
                },
                sort_keys=True,
            )
        )
    else:
        fail(
            "usage: input-gate.py recipe-id RECIPE | freeze FROZEN | inventory FROZEN EXPECTED_MANIFEST | check FROZEN RECIPE PUBLIC_CHECKOUT EXPECTED_COMMIT EXPECTED_TREE EXPECTED_RECIPE_SHA256 EXPECTED_MANIFEST_SHA256"
        )


if __name__ == "__main__":
    main()
