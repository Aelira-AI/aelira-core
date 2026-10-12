#!/usr/bin/env python3
"""Bounded, atomic evidence export for the frozen native lxml component.

Run on the native Linux host after the three original entrypoints have ended.
The caller supplies their exit statuses and retained log hashes in a private
receipt; this program checks the logs but exports only their hashes.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile

ORIGIN = "https://github.com/Aelira-AI/aelira-core.git"
KEYRING_SHA256 = "80a36b0a6de2f69f49d2df75ef473ccde121e9e190b9ea01d20a4f63778d5c31"
HEX = re.compile(r"[0-9a-f]{64}\Z")
GIT = re.compile(r"[0-9a-f]{40}\Z")
SAFE = re.compile(r"[A-Za-z0-9._/+%~-]+\Z")
RECIPE = frozenset(
    "Dockerfile.verifier README.md accept-native.sh build-offline-container.sh compatibility.py consumer-container.sh consumer.py fetch-native.sh fetch-consumer-wheels.py input-gate.py inputs.sha256 prepare-native.sh preflight-container.sh run-consumer.sh verify-consumer-wheels.py verify-apt-closure.py verify-frozen.py verify-installed.py".split()
)
SOURCE = frozenset(
    "requirements.txt src/education/docx_processor.py src/education/pptx_processor.py src/education/xlsx_processor.py src/education/remediation/pptx_reading_order.py".split()
)
PYTHON = frozenset(
    "lxml-6.1.3.tar.gz pip-26.2.1-py3-none-any.whl setuptools-84.0.0-py3-none-any.whl".split()
)
STRUCTURED = frozenset(
    {
        "freeze-summary.json",
        "host-source-check.json",
        "host-accept-source-check.json",
        "host-consumer-source-check.json",
        "consumer/source-bound-identity.txt",
        "consumer/baseline-compatibility.json",
        "consumer/source-compatibility.json",
        "consumer/results/installed.json",
        "consumer/results/consumer.json",
        "consumer/consumer-wheel-check.json",
        "consumer/installed-python.json",
    }
    | {
        f"{area}/{name}"
        for area in ("build-a", "build-b", "consumer")
        for name in (
            "apt-closure.json",
            "authenticated-packages.json",
            "preinstall-authenticated-packages.json",
            "preinstall-consumer-wheels.json",
            "preinstall-inventory.json",
        )
    }
)
MAX_FILES = 230
MAX_FILE = 80 * 1024 * 1024
MAX_TOTAL = 450 * 1024 * 1024
MAX_JSON = 2 * 1024 * 1024
STATUS_NAMES = {"prepare-native", "accept-native", "run-consumer"}
AREAS = ("build-a", "build-b", "consumer")
PYPI_PINS = {
    "lxml": "6.1.3",
    "python-docx": "1.2.0",
    "python-pptx": "1.0.2",
    "openpyxl": "3.1.5",
    "pillow": "12.3.0",
    "XlsxWriter": "3.2.9",
    "typing_extensions": "4.15.0",
    "et_xmlfile": "2.0.0",
}
INSTALLED_PINS = {**{k.lower(): v for k, v in PYPI_PINS.items()}, "pip": "26.2.1"}
XML_CONTROLS = [
    "catalog-dtd",
    "xpath",
    "xslt",
    "relaxng",
    "xinclude",
    "c14n",
    "malformed-refusal",
]
OFFICE_CONTROLS = [
    "docx-save-load",
    "pptx-save-load",
    "xlsx-save-load",
    "aelira-pptx-reading-order-validate",
]
LIBRARIES = {
    "libc.so.6",
    "libexslt.so.0",
    "libgcrypt.so.20",
    "libgpg-error.so.0",
    "libm.so.6",
    "libxml2.so.16",
    "libxslt.so.1",
    "libz.so.1",
}
LIBRARY_OWNERS = {
    "libc.so.6": "libc6",
    "libexslt.so.0": "libxslt1.1",
    "libgcrypt.so.20": "libgcrypt20",
    "libgpg-error.so.0": "libgpg-error0",
    "libm.so.6": "libc6",
    "libxml2.so.16": "libxml2-16",
    "libxslt.so.1": "libxslt1.1",
    "libz.so.1": "zlib1g",
}
XML_LIBRARIES = frozenset({"libxml2.so.16", "libxslt.so.1", "libexslt.so.0"})
INSTALL_ADDITIONS = frozenset({"INSTALLER", "REQUESTED", "direct_url.json"})
EXCLUDED = ["verifier/image.tar", "raw logs", "installer-venv.tar"]
APT_INSTALL = re.compile(r"Inst (\S+)(?: \[[^]]+\])? \((\S+) .* \[([^]]+)\]\)\Z")


def require(ok: bool, message: str) -> None:
    if not ok:
        raise ValueError(message)


def pairs_unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def strict_json(path: Path) -> object:
    regular(path, MAX_JSON)
    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=pairs_unique,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("JSON constant")),
    )


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def regular(path: Path, limit: int = MAX_FILE) -> int:
    canonical(path)
    meta = path.lstat()
    require(
        stat.S_ISREG(meta.st_mode) and meta.st_nlink == 1,
        "nonregular or hard-linked input",
    )
    require(0 < meta.st_size <= limit, "empty or oversize input")
    return meta.st_size


def canonical(path: Path, *, may_be_new: bool = False) -> None:
    """Refuse aliases and links in every existing component, before any read/write."""
    require(path.is_absolute(), "absolute path required")
    parts = path.as_posix().split("/")
    require(all(part not in {".", "..", ""} for part in parts[1:]), "noncanonical path")
    current = Path("/")
    for index, part in enumerate(parts[1:], 1):
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            require(may_be_new and index == len(parts) - 1, "missing path component")
            return
        require(not stat.S_ISLNK(mode), "symlink path component")
        if index < len(parts) - 1:
            require(stat.S_ISDIR(mode), "non-directory path component")
    require(not may_be_new, "existing output")


def cli_path(raw: str, *, may_be_new: bool = False) -> Path:
    require(
        raw.startswith("/")
        and not raw.endswith("/")
        and "//" not in raw
        and all(part not in {".", ".."} for part in raw.split("/")),
        "path alias",
    )
    path = Path(raw)
    canonical(path, may_be_new=may_be_new)
    return path


def confined(name: str) -> bool:
    p = PurePosixPath(name)
    return (
        bool(SAFE.fullmatch(name))
        and not name.startswith("/")
        and p.as_posix() == name
        and all(part not in {"", ".", ".."} for part in p.parts)
        and "//" not in name
    )


def run(*args: str) -> bytes:
    return subprocess.run(
        args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    ).stdout


def git(repo: Path, *args: str) -> str:
    return run("git", "-C", str(repo), *args).decode().strip()


def recipe_digest(recipe: Path) -> str:
    require(recipe.is_dir() and not recipe.is_symlink(), "recipe directory")
    names = {p.name for p in recipe.iterdir()}
    require(names == RECIPE, "recipe file set")
    lines = []
    for name in sorted(RECIPE):
        regular(recipe / name, MAX_JSON)
        lines.append(f"{sha(recipe / name)}  {name}\n")
    return hashlib.sha256("".join(lines).encode()).hexdigest()


def public_source(
    repo: Path,
    frozen: Path,
    recipe: Path,
    scope: str,
    commit: str,
    tree: str,
    recipe_sha: str,
) -> None:
    require(
        GIT.fullmatch(commit) is not None and GIT.fullmatch(tree) is not None,
        "Git identity",
    )
    require(git(repo, "remote", "get-url", "origin") == ORIGIN, "public origin")
    require(git(repo, "rev-parse", "HEAD") == commit, "HEAD")
    require(git(repo, "rev-parse", "HEAD^{tree}") == tree, "tree")
    require(
        (frozen / "source-commit.txt").read_text() == commit + "\n", "frozen commit"
    )
    require((frozen / "source-tree.txt").read_text() == tree + "\n", "frozen tree")
    require(recipe_digest(recipe) == recipe_sha, "live recipe digest")
    require(recipe_digest(frozen / "recipe") == recipe_sha, "frozen recipe digest")
    for name in SOURCE:
        require(
            (frozen / "source" / name).read_bytes()
            == run("git", "-C", str(repo), "show", f"{commit}:{name}"),
            "Git source bytes",
        )
    if scope == "public-commit":
        require(
            git(repo, "status", "--porcelain", "--untracked-files=all") == "",
            "checkout is not clean",
        )
        for name in RECIPE:
            relative = "runtime-security/lxml/" + name
            require(
                git(repo, "ls-files", "--error-unmatch", relative) == relative,
                "recipe not tracked",
            )
            require(
                (recipe / name).read_bytes()
                == run("git", "-C", str(repo), "show", f"{commit}:{relative}"),
                "recipe Git bytes",
            )
    else:
        require(scope == "candidate-uncommitted", "scope")


def manifest(frozen: Path, expected: str, *, exported: bool = False) -> dict[str, str]:
    require(HEX.fullmatch(expected) is not None, "frozen identity")
    root = frozen / "frozen.sha256"
    regular(root, MAX_JSON)
    require(sha(root) == expected, "frozen manifest digest")
    raw = root.read_text(encoding="ascii")
    rows = raw.splitlines(keepends=True)
    require(
        rows and len(rows) <= MAX_FILES and all(row.endswith("\n") for row in rows),
        "manifest bounds",
    )
    result: dict[str, str] = {}
    for row in rows:
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9._/+%~-]+)\n", row)
        require(match is not None, "manifest grammar")
        digest, name = match.groups()
        require(confined(name) and name not in result, "manifest path")
        result[name] = digest
    require(list(result) == sorted(result), "manifest order")
    fixed = {
        "architecture.txt",
        "source-commit.txt",
        "source-tree.txt",
        "keyring/ubuntu-archive-keyring.gpg",
        "verifier/image.tar",
        "verifier/image-id.txt",
        "verifier/packages.txt",
    }
    fixed |= (
        {"recipe/" + n for n in RECIPE}
        | {"source/" + n for n in SOURCE}
        | {"python/" + n for n in PYTHON}
    )
    require(fixed <= result.keys(), "missing fixed inputs")
    require(
        {n for n in result if n.startswith("debs/")}
        and {n for n in result if n.startswith("apt-lists/")},
        "missing APT inputs",
    )
    metadata = strict_json(frozen / "consumer-wheels/pypi-metadata.json")
    require(
        type(metadata) is list and len(metadata) == len(PYPI_PINS),
        "PyPI metadata count",
    )
    consumer = {"consumer-wheels/pypi-metadata.json"}
    packages = set()
    for row in metadata:
        require(
            type(row) is dict
            and set(row)
            == {"filename", "metadata_url", "package", "sha256", "url", "version"},
            "PyPI metadata row",
        )
        name, package = row["filename"], row["package"]
        require(
            type(name) is str
            and re.fullmatch(r"[A-Za-z0-9_.+-]+\.whl", name) is not None
            and package in PYPI_PINS
            and package not in packages,
            "PyPI wheel name",
        )
        packages.add(package)
        consumer.add("consumer-wheels/" + name)
    require(packages == PYPI_PINS.keys(), "PyPI package set")
    apt = {n for n in result if n.startswith("apt-lists/")}
    debs = {n for n in result if n.startswith("debs/")}
    require(result.keys() == fixed | consumer | apt | debs, "frozen exact classes")
    require(
        all(re.fullmatch(r"debs/[A-Za-z0-9.+%_~:-]+\.deb", n) for n in debs),
        "DEB names",
    )
    require(
        all(
            re.fullmatch(
                r"apt-lists/[A-Za-z0-9._+-]+_(?:InRelease|Packages(?:\.lz4|\.gz)?)", n
            )
            for n in apt
        ),
        "APT names",
    )
    total = 0
    for name, digest in result.items():
        if exported and name == "verifier/image.tar":
            continue
        size = regular(
            frozen / name,
            200 * 1024 * 1024 if name == "verifier/image.tar" else MAX_FILE,
        )
        total += size
        require(sha(frozen / name) == digest, "frozen input hash")
    require(total <= 600 * 1024 * 1024, "frozen total")
    observed = set()
    observed_dirs = {""}
    for directory, dirs, files in os.walk(frozen, followlinks=False):
        for entry in dirs:
            require(not (Path(directory) / entry).is_symlink(), "symlink directory")
            observed_dirs.add((Path(directory) / entry).relative_to(frozen).as_posix())
        for entry in files:
            name = (Path(directory) / entry).relative_to(frozen).as_posix()
            observed.add(name)
    require(
        observed
        == (set(result) - ({"verifier/image.tar"} if exported else set()))
        | {"frozen.sha256"},
        "frozen extra or omitted path",
    )
    expected_dirs = {""} | {
        parent.as_posix()
        for name in observed
        for parent in PurePosixPath(name).parents
        if parent.as_posix() != "."
    }
    require(observed_dirs == expected_dirs, "frozen directory inventory")
    return result


def fields(value: object, names: set[str], label: str) -> dict[str, object]:
    require(type(value) is dict and set(value) == names, label + " fields")
    return value


def digest_value(value: object, label: str) -> None:
    require(type(value) is str and HEX.fullmatch(value) is not None, label + " digest")


def number(value: object, low: int, high: int, label: str) -> None:
    require(type(value) is int and low <= value <= high, label + " number")


def safe_scalar(value: object, pattern: str, label: str, limit: int = 240) -> None:
    require(
        type(value) is str
        and 0 < len(value) <= limit
        and re.fullmatch(pattern, value) is not None,
        label + " value",
    )


def native_wheel_name(architecture: str) -> str:
    require(architecture in {"arm64", "amd64"}, "native architecture")
    platform = "aarch64" if architecture == "arm64" else "x86_64"
    return f"lxml-6.1.3-cp314-cp314-linux_{platform}.whl"


def canonical_library_path(value: object, soname: str, architecture: str) -> None:
    require(type(value) is str and len(value) <= 120, "library path type")
    require(
        soname in LIBRARIES and architecture in {"arm64", "amd64"}, "library identity"
    )
    triplet = "aarch64-linux-gnu" if architecture == "arm64" else "x86_64-linux-gnu"
    path = PurePosixPath(value)
    require(
        value == path.as_posix()
        and path.parent == PurePosixPath("/usr/lib") / triplet
        and re.fullmatch(re.escape(soname) + r"(?:\.[0-9]+(?:\.[0-9]+)*)?", path.name)
        is not None,
        "canonical loaded SONAME path",
    )


def wheel_metadata(path: Path, architecture: str) -> None:
    """Bind the filename to the one CPython/ABI/platform tag inside the wheel."""
    require(path.name == native_wheel_name(architecture), "native wheel filename")
    with zipfile.ZipFile(path) as archive:
        info = "lxml-6.1.3.dist-info/"
        wheel = archive.read(info + "WHEEL").decode("utf-8")
        metadata = archive.read(info + "METADATA").decode("utf-8")
        tag = "cp314-cp314-linux_" + (
            "aarch64" if architecture == "arm64" else "x86_64"
        )
        require(re.findall(r"^Tag: (.+)$", wheel, re.MULTILINE) == [tag], "wheel tag")
        require(
            re.findall(r"^Name: (.+)$", metadata, re.MULTILINE) == ["lxml"]
            and re.findall(r"^Version: (.+)$", metadata, re.MULTILINE) == ["6.1.3"],
            "wheel distribution",
        )


def installed_additions(
    installed: dict[str, object],
    wheel_name: str,
    wheel_sha: str,
    wheel_rows: list[list[str]],
) -> None:
    """Prove the three pip-created hashed rows from bounded public content."""
    info = "lxml-6.1.3.dist-info/"
    additions = installed["record_additions"]
    require(type(additions) is list and len(additions) == 3, "installation additions")
    observed = {}
    for item in additions:
        row = fields(item, {"path", "sha256", "size"}, "installation addition")
        require(
            type(row["path"]) is str
            and row["path"].startswith(info)
            and row["path"][len(info) :] in INSTALL_ADDITIONS
            and row["path"] not in observed,
            "installation addition path",
        )
        digest_value(row["sha256"], "installation addition")
        number(row["size"], 0, MAX_JSON, "installation addition size")
        observed[row["path"]] = row
    require(
        set(observed) == {info + name for name in INSTALL_ADDITIONS},
        "exact installation additions",
    )
    expected = {info + "INSTALLER": b"pip\n", info + "REQUESTED": b""}
    url = "file:///wheels/" + wheel_name
    archive_info = {"hash": "sha256=" + wheel_sha, "hashes": {"sha256": wheel_sha}}
    direct_urls = (
        {"archive_info": archive_info, "url": url},
        {"archive_info": {}, "url": url},
    )
    direct = observed[info + "direct_url.json"]
    require(
        any(
            (direct["sha256"], direct["size"])
            == (
                sha_bytes(json.dumps(value, sort_keys=True).encode()),
                len(json.dumps(value, sort_keys=True).encode()),
            )
            for value in direct_urls
        ),
        "direct URL content",
    )
    for name, payload in expected.items():
        require(
            (observed[name]["sha256"], observed[name]["size"])
            == (sha_bytes(payload), len(payload)),
            "pip metadata content",
        )
    hashed = sum(bool(row[1]) for row in wheel_rows)
    require(
        installed["record_hashes_verified"] == hashed + len(additions),
        "exact installed RECORD count",
    )


def validated_receipts(
    root: Path,
    prefix: str,
    frozen: Path,
    entries: dict[str, str],
    commit: str,
    tree: str,
    recipe_sha: str,
    frozen_sha: str,
    wheel_sha: str,
    wheel_name: str,
) -> tuple[dict[str, object], dict[str, object]]:
    """Validate every exported field against producer schemas and selected inputs."""

    def receipt(name: str) -> object:
        return strict_json(root / prefix / name)

    count = len(entries)
    summary = fields(
        receipt("freeze-summary.json"), {"files", "manifest_sha256"}, "freeze summary"
    )
    require(
        summary == {"files": count, "manifest_sha256": frozen_sha}
        and type(summary["files"]) is int,
        "freeze summary binding",
    )
    host = {
        "files": count,
        "manifest_sha256": frozen_sha,
        "recipe_sha256": recipe_sha,
        "source_commit": commit,
        "source_tree": tree,
    }
    for name in (
        "host-source-check.json",
        "host-accept-source-check.json",
        "host-consumer-source-check.json",
    ):
        row = fields(receipt(name), set(host), "host source")
        require(row == host and type(row["files"]) is int, "host source binding")
    verifier = (frozen / "verifier/image-id.txt").read_text()
    require(
        re.fullmatch(r"sha256:[0-9a-f]{64}\n", verifier) is not None,
        "verifier identity",
    )
    identity = root / prefix / "consumer/source-bound-identity.txt"
    regular(identity, MAX_JSON)
    require(
        identity.read_text()
        == f"source_commit={commit}\nsource_tree={tree}\nrecipe_sha256={recipe_sha}\nfrozen_sha256={frozen_sha}\nwheel_sha256={wheel_sha}\nverifier_image_id={verifier.strip()}\n",
        "consumer source binding",
    )

    pypi = strict_json(frozen / "consumer-wheels/pypi-metadata.json")
    wheel_pins = {}
    for row in pypi:
        require(row["version"] == PYPI_PINS[row["package"]], "PyPI version")
        digest_value(row["sha256"], "PyPI")
        safe_scalar(
            row["url"],
            r"https://files\.pythonhosted\.org/packages/[A-Za-z0-9/._+-]+",
            "PyPI URL",
            300,
        )
        require(
            row["metadata_url"]
            == f"https://pypi.org/pypi/{row['package']}/{row['version']}/json",
            "PyPI metadata URL",
        )
        require(
            entries["consumer-wheels/" + row["filename"]] == row["sha256"],
            "PyPI wheel bytes",
        )
        wheel_pins[row["package"]] = row["sha256"]
    require(wheel_pins.keys() == PYPI_PINS.keys(), "wheel pin set")

    auths = []
    closures = []
    for area in AREAS:
        inventory = fields(
            receipt(f"{area}/preinstall-inventory.json"),
            {"files", "manifest_sha256"},
            "inventory",
        )
        require(
            inventory == summary and type(inventory["files"]) is int,
            "preinstall inventory binding",
        )
        closure = fields(
            receipt(f"{area}/apt-closure.json"),
            {"exact_closure", "selected_packages"},
            "APT closure",
        )
        require(closure["exact_closure"] is True, "APT closure observation")
        number(closure["selected_packages"], 1, 180, "APT selected")
        closures.append(closure)
        for name in ("preinstall-consumer-wheels.json",):
            check = fields(
                receipt(f"{area}/{name}"), {"checked", "wheels"}, "consumer wheel check"
            )
            require(
                type(check["checked"]) is int
                and check["checked"] == len(PYPI_PINS)
                and type(check["wheels"]) is dict
                and check["wheels"] == wheel_pins,
                "consumer wheel proof",
            )
        for name in (
            "authenticated-packages.json",
            "preinstall-authenticated-packages.json",
        ):
            auth = fields(
                receipt(f"{area}/{name}"),
                {"keyring_sha256", "release", "indexes", "debs"},
                "authenticated packages",
            )
            require(auth["keyring_sha256"] == KEYRING_SHA256, "keyring identity")
            require(
                type(auth["release"]) is list
                and 1 <= len(auth["release"]) <= 20
                and type(auth["indexes"]) is list
                and 1 <= len(auth["indexes"]) <= 40
                and type(auth["debs"]) is list
                and 1 <= len(auth["debs"]) <= 180,
                "authenticated list bounds",
            )
            release_names = set()
            for item in auth["release"]:
                row = fields(item, {"file", "sha256", "signed_indexes"}, "Release")
                safe_scalar(
                    row["file"], r"[A-Za-z0-9._+-]+_InRelease", "Release filename"
                )
                digest_value(row["sha256"], "Release")
                number(row["signed_indexes"], 1, 10000, "signed indexes")
                require(
                    row["file"] not in release_names
                    and entries.get("apt-lists/" + row["file"]) == row["sha256"],
                    "Release binding",
                )
                release_names.add(row["file"])
            index_names = set()
            for item in auth["indexes"]:
                row = fields(
                    item,
                    {"file", "release", "path", "sha256", "size"},
                    "Packages index",
                )
                safe_scalar(
                    row["file"],
                    r"[A-Za-z0-9._+-]+_Packages(?:\.lz4|\.gz)?",
                    "Packages filename",
                )
                safe_scalar(
                    row["path"],
                    r"[A-Za-z0-9._+-]+/binary-(?:amd64|arm64|all)/Packages",
                    "signed index path",
                )
                require(
                    row["release"] in release_names and row["file"] not in index_names,
                    "index Release binding",
                )
                digest_value(row["sha256"], "Packages")
                number(row["size"], 1, MAX_FILE, "Packages size")
                require("apt-lists/" + row["file"] in entries, "Packages inventory")
                index_names.add(row["file"])
            deb_names = set()
            packages = set()
            for item in auth["debs"]:
                row = fields(
                    item,
                    {
                        "file",
                        "package",
                        "version",
                        "architecture",
                        "sha256",
                        "size",
                        "signed_index_filename",
                    },
                    "DEB",
                )
                safe_scalar(row["file"], r"[A-Za-z0-9.+%_~:-]+\.deb", "DEB filename")
                safe_scalar(row["package"], r"[a-z0-9][a-z0-9+.-]*", "DEB package")
                safe_scalar(row["version"], r"[A-Za-z0-9.+:~_-]+", "DEB version")
                require(
                    row["architecture"]
                    in {"all", (frozen / "architecture.txt").read_text().strip()},
                    "DEB architecture",
                )
                safe_scalar(
                    row["signed_index_filename"],
                    r"pool/[A-Za-z0-9._+~/%-]+\.deb",
                    "signed DEB filename",
                )
                require(confined(row["signed_index_filename"]), "signed DEB path")
                digest_value(row["sha256"], "DEB")
                number(row["size"], 1, MAX_FILE, "DEB size")
                require(
                    row["file"] not in deb_names
                    and row["package"] not in packages
                    and entries.get("debs/" + row["file"]) == row["sha256"],
                    "DEB inventory binding",
                )
                deb_names.add(row["file"])
                packages.add(row["package"])
            require(
                {"apt-lists/" + n for n in release_names | index_names}
                == {n for n in entries if n.startswith("apt-lists/")}
                and {"debs/" + n for n in deb_names}
                == {n for n in entries if n.startswith("debs/")},
                "authenticated exact input sets",
            )
            require(
                len(auth["debs"]) == closure["selected_packages"], "APT closure count"
            )
            auths.append(auth)
    require(
        closures[0] == closures[1] == closures[2]
        and all(row == auths[0] for row in auths),
        "cross-run authenticated equality",
    )
    consumer_wheels = fields(
        receipt("consumer/consumer-wheel-check.json"),
        {"checked", "wheels"},
        "installed consumer wheels",
    )
    require(
        type(consumer_wheels["checked"]) is int
        and consumer_wheels["checked"] == len(PYPI_PINS)
        and type(consumer_wheels["wheels"]) is dict
        and consumer_wheels["wheels"] == wheel_pins,
        "consumer wheels binding",
    )
    packages = receipt("consumer/installed-python.json")
    require(
        type(packages) is list and len(packages) == len(INSTALLED_PINS),
        "installed Python count",
    )
    observed_packages = {}
    for item in packages:
        row = fields(item, {"name", "version"}, "installed Python")
        safe_scalar(row["name"], r"[A-Za-z0-9_-]+", "installed Python name")
        safe_scalar(row["version"], r"[0-9]+(?:\.[0-9]+)*", "installed Python version")
        require(
            row["name"].lower() not in observed_packages, "duplicate installed Python"
        )
        observed_packages[row["name"].lower()] = row["version"]
    require(observed_packages == INSTALLED_PINS, "installed Python versions")
    for name, version in (("baseline", [2, 14, 6]), ("source", [2, 15, 2])):
        compatibility = fields(
            receipt(f"consumer/{name}-compatibility.json"),
            {"libxml", "gzip", "lzma", "schematron"},
            "compatibility",
        )
        require(
            type(compatibility["libxml"]) is list
            and compatibility["libxml"] == version
            and all(type(n) is int for n in compatibility["libxml"]),
            "libxml compatibility version",
        )
        for codec in ("gzip", "lzma"):
            row = fields(
                compatibility[codec], {"accepted", "error_type"}, "compression"
            )
            require(
                row["accepted"] is False and row["error_type"] == "XMLSyntaxError",
                "observed compression refusal",
            )
        row = fields(
            compatibility["schematron"], {"available", "error_type"}, "Schematron"
        )
        require(
            row["available"] is False
            and row["error_type"] == "SchematronValidateError",
            "observed Schematron refusal",
        )
    consumer = fields(
        receipt("consumer/results/consumer.json"),
        {"nonroot", "xml", "office"},
        "consumer",
    )
    require(
        consumer["nonroot"] is True
        and consumer["xml"] == XML_CONTROLS
        and consumer["office"] == OFFICE_CONTROLS,
        "consumer observations",
    )
    installed = fields(
        receipt("consumer/results/installed.json"),
        {
            "distribution",
            "version",
            "record_hashes_verified",
            "record_additions",
            "wheel_sha256",
            "extensions_sha256",
            "compiled_libxml",
            "runtime_libxml",
            "compiled_libxslt",
            "runtime_libxslt",
            "libraries",
        },
        "installed lxml",
    )
    require(
        installed["distribution"] == "lxml"
        and installed["version"] == "6.1.3"
        and installed["wheel_sha256"] == wheel_sha,
        "installed wheel identity",
    )
    number(installed["record_hashes_verified"], 50, 1000, "RECORD count")
    for key, version in (
        ("compiled_libxml", [2, 15, 2]),
        ("runtime_libxml", [2, 15, 2]),
        ("compiled_libxslt", [1, 1, 45]),
        ("runtime_libxslt", [1, 1, 45]),
    ):
        require(
            type(installed[key]) is list
            and installed[key] == version
            and all(type(n) is int for n in installed[key]),
            "loaded XML version",
        )
    require(
        type(installed["extensions_sha256"]) is dict
        and 7 <= len(installed["extensions_sha256"]) <= 20,
        "extension count",
    )
    for name, digest in installed["extensions_sha256"].items():
        require(
            type(name) is str
            and confined(name)
            and re.fullmatch(
                r"lxml/(?:html/)?[A-Za-z0-9_]+\.cpython-314-(?:aarch64|x86_64)-linux-gnu\.so",
                name,
            )
            is not None,
            "extension name",
        )
        digest_value(digest, "extension")
    libraries = installed["libraries"]
    require(
        type(libraries) is dict and set(libraries) == LIBRARIES, "loaded library set"
    )
    owners = {row["package"] for row in auths[0]["debs"]}
    architecture = (frozen / "architecture.txt").read_text().strip()
    for soname, item in libraries.items():
        row = fields(item, {"path", "sha256", "owner"}, "loaded library")
        canonical_library_path(row["path"], soname, architecture)
        safe_scalar(
            row["owner"], r"[a-z0-9][a-z0-9+.-]*(?::(?:arm64|amd64))?", "library owner"
        )
        owner = row["owner"].split(":", 1)[0]
        require(owner == LIBRARY_OWNERS[soname], "library expected owner")
        if soname in XML_LIBRARIES:
            require(owner in owners, "XML library authenticated owner")
        digest_value(row["sha256"], "loaded library")
    return auths[0], installed


def status_receipt(path: Path) -> dict[str, object]:
    regular(path, MAX_JSON)
    data = strict_json(path)
    require(
        type(data) is dict
        and set(data) == {"schema", "commands"}
        and type(data["schema"]) is int
        and data["schema"] == 1,
        "status schema",
    )
    commands = data["commands"]
    require(type(commands) is dict and set(commands) == STATUS_NAMES, "status commands")
    clean = {}
    for name, row in commands.items():
        require(
            type(row) is dict and set(row) == {"exit_code", "log_path", "log_sha256"},
            "status row",
        )
        require(
            type(row["exit_code"]) is int and row["exit_code"] == 0,
            "failed or absent original command",
        )
        require(
            type(row["log_path"]) is str and Path(row["log_path"]).is_absolute(),
            "log path",
        )
        require(
            type(row["log_sha256"]) is str
            and HEX.fullmatch(row["log_sha256"]) is not None,
            "log hash",
        )
        path2 = cli_path(row["log_path"])
        regular(path2, 10 * 1024 * 1024)
        require(sha(path2) == row["log_sha256"], "retained original log changed")
        clean[name] = {"exit_code": 0, "log_sha256": row["log_sha256"]}
    return clean


def validate_receipts(
    scratch: Path,
    frozen_hash: str,
    wheel_hash: str,
    commit: str,
    tree: str,
    recipe_sha: str,
) -> tuple[str, dict[str, object]]:
    require(HEX.fullmatch(wheel_hash) is not None, "wheel identity")
    architecture = (scratch / "frozen/architecture.txt").read_text().strip()
    require(architecture in {"amd64", "arm64"}, "architecture")
    wheel_names = []
    for area in ("build-a", "build-b"):
        matches = list((scratch / area).glob("lxml-6.1.3-*.whl"))
        require(len(matches) == 1, "wheel count")
        wheel = matches[0]
        regular(wheel)
        require(sha(wheel) == wheel_hash, "wheel bytes")
        wheel_metadata(wheel, architecture)
        require(
            (scratch / area / "wheel.sha256").read_text().split()[0] == wheel_hash,
            "recorded wheel hash",
        )
        wheel_names.append(wheel.name)
    require(
        wheel_names[0] == wheel_names[1]
        and (scratch / "build-a" / wheel_names[0]).read_bytes()
        == (scratch / "build-b" / wheel_names[0]).read_bytes(),
        "unequal wheels",
    )
    for area in ("build-a", "build-b", "consumer"):
        inv = strict_json(scratch / area / "preinstall-inventory.json")
        require(
            type(inv) is dict
            and inv.get("manifest_sha256") == frozen_hash
            and type(inv.get("files")) is int,
            "preinstall inventory",
        )
        closure = strict_json(scratch / area / "apt-closure.json")
        require(
            type(closure) is dict
            and closure.get("exact_closure") is True
            and type(closure.get("selected_packages")) is int,
            "APT closure",
        )
        actual = run(
            sys.executable,
            "-I",
            str(scratch / "frozen/recipe/verify-apt-closure.py"),
            str(scratch / area / "apt-simulation.txt"),
            str(scratch / area / "preinstall-authenticated-packages.json"),
        )
        require(json.loads(actual) == closure, "APT closure replay")
        require(
            (scratch / area / "preinstall-signed-inputs.txt").read_text()
            == frozen_hash + "\n",
            "signed-input marker",
        )
        require(
            (scratch / area / "preinstall-authenticated.txt").read_text()
            == frozen_hash + "\n",
            "final preinstall marker",
        )
    auth = [
        strict_json(scratch / area / "preinstall-authenticated-packages.json")
        for area in ("build-a", "build-b", "consumer")
    ]
    require(auth[0] == auth[1] == auth[2], "different authenticated packages")
    require(
        len(auth[0]["debs"])
        == strict_json(scratch / "build-a/apt-closure.json")["selected_packages"],
        "package count",
    )
    require(
        (scratch / "consumer/source-bound-identity.txt").read_text()
        == f"source_commit={commit}\nsource_tree={tree}\nrecipe_sha256={recipe_sha}\nfrozen_sha256={frozen_hash}\nwheel_sha256={wheel_hash}\nverifier_image_id={(scratch / 'frozen/verifier/image-id.txt').read_text().strip()}\n",
        "consumer identity",
    )
    installed = strict_json(scratch / "consumer/results/installed.json")
    require(
        installed.get("distribution") == "lxml"
        and installed.get("version") == "6.1.3"
        and installed.get("wheel_sha256") == wheel_hash
        and installed.get("record_hashes_verified", 0) >= 50,
        "installed receipt",
    )
    require(
        strict_json(scratch / "consumer/results/consumer.json").get("nonroot") is True,
        "consumer receipt",
    )
    for key in ("gzip", "lzma", "schematron"):
        for file in ("baseline-compatibility.json", "source-compatibility.json"):
            row = strict_json(scratch / "consumer" / file)[key]
            require(
                row.get("accepted", row.get("available")) is False,
                "compatibility receipt",
            )
    with zipfile.ZipFile(scratch / "build-a" / wheel_names[0]) as archive:
        wheel_rows = list(
            csv.reader(
                archive.read("lxml-6.1.3.dist-info/RECORD").decode().splitlines()
            )
        )
    installed_additions(installed, wheel_names[0], wheel_hash, wheel_rows)
    return wheel_names[0], auth[0]


def apt_selection(path: Path) -> list[list[str]]:
    regular(path, MAX_JSON)
    lines = path.read_text().splitlines()
    require(
        "Reading package lists..." in lines and "Solving dependencies..." in lines,
        "incomplete APT simulation",
    )
    selected = set()
    for line in lines:
        if line.startswith("Inst "):
            match = APT_INSTALL.fullmatch(line)
            require(
                match is not None and match.groups() not in selected,
                "invalid APT selection",
            )
            selected.add(match.groups())
    require(0 < len(selected) <= 180, "APT selection bounds")
    return [list(row) for row in sorted(selected)]


def copy_one(
    source: Path, target: Path, expected: str | None = None
) -> dict[str, object]:
    size = regular(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as src, target.open("xb") as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)
    digest = sha(target)
    require(expected is None or digest == expected, "copy changed")
    return {"sha256": digest, "size": size}


def export(args: argparse.Namespace) -> None:
    scratch, repo, recipe = (
        cli_path(x) for x in (args.scratch, args.repo, args.recipe)
    )
    require(all(path.is_dir() for path in (scratch, repo, recipe)), "input directory")
    require(args.scope in {"candidate-uncommitted", "public-commit"}, "scope")
    frozen = scratch / "frozen"
    status = status_receipt(cli_path(args.status))
    public_source(
        repo, frozen, recipe, args.scope, args.commit, args.tree, args.recipe_sha256
    )
    entries = manifest(frozen, args.frozen_sha256)
    wheel_name, auth = validate_receipts(
        scratch,
        args.frozen_sha256,
        args.wheel_sha256,
        args.commit,
        args.tree,
        args.recipe_sha256,
    )
    validated_receipts(
        scratch,
        Path("."),
        frozen,
        entries,
        args.commit,
        args.tree,
        args.recipe_sha256,
        args.frozen_sha256,
        args.wheel_sha256,
        wheel_name,
    )
    require(
        len(auth["debs"]) == len([n for n in entries if n.startswith("debs/")]),
        "DEB inventory",
    )
    for row in auth["debs"]:
        name = "debs/" + row["file"]
        require(
            entries.get(name) == row["sha256"]
            and (frozen / name).stat().st_size == row["size"],
            "authenticated DEB bytes",
        )
    out = cli_path(args.out, may_be_new=True)
    require(confined(out.name) and out.parent.is_dir(), "new absolute output")
    stage = Path(tempfile.mkdtemp(prefix=".lxml-evidence-", dir=out.parent))
    try:
        files: dict[str, dict[str, object]] = {}
        for name, digest in entries.items():
            if name == "verifier/image.tar":
                continue
            files["frozen/" + name] = copy_one(
                frozen / name, stage / "frozen" / name, digest
            )
        files["frozen/frozen.sha256"] = copy_one(
            frozen / "frozen.sha256", stage / "frozen/frozen.sha256", args.frozen_sha256
        )
        for name in sorted(STRUCTURED):
            source = scratch / name
            regular(source, MAX_JSON)
            if name.endswith(".json"):
                strict_json(source)
            files["receipts/" + name] = copy_one(source, stage / "receipts" / name)
        for area in ("build-a", "build-b"):
            name = f"wheels/{area}/{wheel_name}"
            files[name] = copy_one(
                scratch / area / wheel_name, stage / name, args.wheel_sha256
            )
        plans = {
            area: apt_selection(scratch / area / "apt-simulation.txt")
            for area in ("build-a", "build-b", "consumer")
        }
        require(
            plans["build-a"] == plans["build-b"] == plans["consumer"],
            "different APT plans",
        )
        plan_file = stage / "receipts/apt-selections.json"
        plan_file.write_text(
            json.dumps({"schema": 1, "selected": plans}, sort_keys=True) + "\n"
        )
        files["receipts/apt-selections.json"] = {
            "sha256": sha(plan_file),
            "size": regular(plan_file, MAX_JSON),
        }
        require(
            len(files) <= MAX_FILES
            and sum(row["size"] for row in files.values()) <= MAX_TOTAL,
            "export bounds",
        )
        bundle = {
            "schema": 1,
            "scope": args.scope,
            "origin": ORIGIN,
            "commit": args.commit,
            "tree": args.tree,
            "recipe_sha256": args.recipe_sha256,
            "frozen_sha256": args.frozen_sha256,
            "wheel_sha256": args.wheel_sha256,
            "architecture": (frozen / "architecture.txt").read_text().strip(),
            "original_commands": status,
            "excluded": ["verifier/image.tar", "raw logs", "installer-venv.tar"],
            "files": files,
        }
        (stage / "bundle.json").write_text(
            json.dumps(bundle, indent=2, sort_keys=True) + "\n"
        )
        check(
            stage,
            repo,
            args.commit,
            args.tree,
            args.recipe_sha256,
            args.frozen_sha256,
            args.wheel_sha256,
        )
        os.rename(stage, out)
    except BaseException:
        shutil.rmtree(stage)
        raise
    print(
        json.dumps(
            {
                "exported": True,
                "scope": args.scope,
                "architecture": bundle["architecture"],
                "files": len(files),
                "bytes": sum(row["size"] for row in files.values()),
            },
            sort_keys=True,
        )
    )


def check(
    bundle: Path,
    repo: Path,
    commit: str,
    tree: str,
    recipe_sha: str,
    frozen_sha: str,
    wheel_sha: str,
) -> None:
    canonical(bundle)
    canonical(repo)
    require(bundle.is_dir() and repo.is_dir(), "bundle or checkout directory")
    regular(bundle / "bundle.json", MAX_JSON)
    data = strict_json(bundle / "bundle.json")
    require(
        type(data) is dict
        and set(data)
        == {
            "schema",
            "scope",
            "origin",
            "commit",
            "tree",
            "recipe_sha256",
            "frozen_sha256",
            "wheel_sha256",
            "architecture",
            "original_commands",
            "excluded",
            "files",
        },
        "bundle schema",
    )
    require(
        type(data["schema"]) is int
        and data["schema"] == 1
        and data["origin"] == ORIGIN
        and type(data["origin"]) is str,
        "bundle identity",
    )
    require(
        type(data["scope"]) is str
        and data["scope"] in {"public-commit", "candidate-uncommitted"},
        "bundle scope",
    )
    require(
        type(data["excluded"]) is list and data["excluded"] == EXCLUDED,
        "excluded inventory",
    )
    require(type(data["architecture"]) is str, "architecture type")
    require(
        all(
            type(data[key]) is str and pattern.fullmatch(data[key]) is not None
            for key, pattern in (
                ("commit", GIT),
                ("tree", GIT),
                ("recipe_sha256", HEX),
                ("frozen_sha256", HEX),
                ("wheel_sha256", HEX),
            )
        ),
        "identity types",
    )
    require(
        (
            data["commit"],
            data["tree"],
            data["recipe_sha256"],
            data["frozen_sha256"],
            data["wheel_sha256"],
        )
        == (commit, tree, recipe_sha, frozen_sha, wheel_sha),
        "external identity",
    )
    files = data["files"]
    require(type(files) is dict and len(files) <= MAX_FILES, "file count")
    total = 0
    for name, row in files.items():
        require(
            confined(name)
            and (
                name.startswith("frozen/")
                or name.startswith("receipts/")
                or name.startswith("wheels/")
            ),
            "export path",
        )
        require(
            type(row) is dict
            and set(row) == {"sha256", "size"}
            and type(row["size"]) is int
            and 0 < row["size"] <= MAX_FILE
            and type(row["sha256"]) is str
            and HEX.fullmatch(row["sha256"]),
            "file row",
        )
        require(
            regular(bundle / name) == row["size"]
            and sha(bundle / name) == row["sha256"],
            "export file bytes",
        )
        total += row["size"]
    require(total <= MAX_TOTAL, "export total")
    observed = set()
    observed_dirs = {""}
    for directory, dirs, names in os.walk(bundle, followlinks=False):
        for name in dirs:
            require(not (Path(directory) / name).is_symlink(), "directory symlink")
            observed_dirs.add((Path(directory) / name).relative_to(bundle).as_posix())
        for name in names:
            observed.add((Path(directory) / name).relative_to(bundle).as_posix())
    require(observed == set(files) | {"bundle.json"}, "extra export file")
    expected_dirs = {""} | {
        parent.as_posix()
        for name in observed
        for parent in PurePosixPath(name).parents
        if parent.as_posix() != "."
    }
    require(observed_dirs == expected_dirs, "export directory inventory")
    frozen = bundle / "frozen"
    listed = manifest(frozen, frozen_sha, exported=True)
    require(recipe_digest(frozen / "recipe") == recipe_sha, "recipe")
    require(
        data["architecture"] in {"amd64", "arm64"}
        and (frozen / "architecture.txt").read_text() == data["architecture"] + "\n",
        "bundle architecture",
    )
    require(
        (frozen / "source-commit.txt").read_text() == commit + "\n"
        and (frozen / "source-tree.txt").read_text() == tree + "\n",
        "frozen Git labels",
    )
    require(
        git(repo, "remote", "get-url", "origin") == ORIGIN
        and git(repo, "rev-parse", "HEAD") == commit
        and git(repo, "rev-parse", "HEAD^{tree}") == tree,
        "Git revision",
    )
    for name in SOURCE:
        require(
            (frozen / "source" / name).read_bytes()
            == run("git", "-C", str(repo), "show", f"{commit}:{name}"),
            "source Git bytes",
        )
    if data["scope"] == "public-commit":
        require(
            git(repo, "status", "--porcelain", "--untracked-files=all") == "",
            "public checkout dirty",
        )
        for name in RECIPE:
            require(
                (frozen / "recipe" / name).read_bytes()
                == run(
                    "git",
                    "-C",
                    str(repo),
                    "show",
                    f"{commit}:runtime-security/lxml/{name}",
                ),
                "recipe Git bytes",
            )
    else:
        require(data["scope"] == "candidate-uncommitted", "scope")
    wheels = sorted(n for n in files if n.startswith("wheels/"))
    wheel_name = native_wheel_name(data["architecture"])
    require(
        wheels == [f"wheels/build-a/{wheel_name}", f"wheels/build-b/{wheel_name}"],
        "exact wheel paths",
    )
    expected_files = {
        "frozen/" + name for name in listed if name != "verifier/image.tar"
    }
    expected_files |= {"frozen/frozen.sha256", "receipts/apt-selections.json"}
    expected_files |= {"receipts/" + name for name in STRUCTURED}
    expected_files |= set(wheels)
    require(set(files) == expected_files, "exact export inventory")
    require(all(sha(bundle / n) == wheel_sha for n in wheels), "wheel hash")
    wheel_metadata(bundle / wheels[0], data["architecture"])
    auth, installed = validated_receipts(
        bundle,
        Path("receipts"),
        frozen,
        listed,
        commit,
        tree,
        recipe_sha,
        frozen_sha,
        wheel_sha,
        wheels[0].split("/")[-1],
    )
    with zipfile.ZipFile(bundle / wheels[0]) as archive:
        names = archive.namelist()
        require(
            len(names) == len(set(names)) and len(names) < 1000, "wheel entry count"
        )
        rec = [n for n in names if n.endswith(".dist-info/RECORD")]
        require(len(rec) == 1, "wheel RECORD")
        rows = list(csv.reader(archive.read(rec[0]).decode().splitlines()))
        hashed_rows = sum(bool(row[1]) for row in rows)
        require(hashed_rows >= 50, "wheel RECORD count")
        installed_additions(installed, wheel_name, wheel_sha, rows)
        require(
            {row[0] for row in rows} == set(names) and len(rows) == len(names),
            "RECORD exact entries",
        )
        for name, digest, size in rows:
            require(name in names and confined(name), "wheel path")
            if digest:
                algorithm, value = digest.split("=", 1)
                payload = archive.read(name)
                require(
                    algorithm == "sha256"
                    and base64.urlsafe_b64encode(hashlib.sha256(payload).digest())
                    .decode()
                    .rstrip("=")
                    == value
                    and len(payload) == int(size),
                    "wheel RECORD hash",
                )
        extensions = {
            name for name in names if name.startswith("lxml/") and name.endswith(".so")
        }
        require(
            set(installed["extensions_sha256"]) == extensions, "exact extension set"
        )
        for name, digest in installed["extensions_sha256"].items():
            payload = archive.read(name)
            require(
                payload.startswith(b"\x7fELF") and sha_bytes(payload) == digest,
                "extension ELF hash",
            )
    require(
        len(auth["debs"])
        == strict_json(bundle / "receipts/build-a/apt-closure.json")[
            "selected_packages"
        ],
        "closure count",
    )
    selections = strict_json(bundle / "receipts/apt-selections.json")
    require(
        type(selections) is dict
        and set(selections) == {"schema", "selected"}
        and type(selections["schema"]) is int
        and selections["schema"] == 1,
        "APT selection schema",
    )
    plans = selections["selected"]
    require(
        type(plans) is dict
        and set(plans) == {"build-a", "build-b", "consumer"}
        and plans["build-a"] == plans["build-b"] == plans["consumer"],
        "APT plan equality",
    )
    plan = plans["build-a"]
    require(
        type(plan) is list
        and all(
            type(row) is list
            and len(row) == 3
            and all(type(value) is str for value in row)
            for row in plan
        ),
        "APT selection shape",
    )
    require(
        {tuple(row) for row in plan}
        == {
            (row["package"], row["version"], row["architecture"])
            for row in auth["debs"]
        }
        and len(plan) == len(auth["debs"]),
        "exact authenticated APT closure",
    )
    for row in auth["debs"]:
        require(sha(frozen / "debs" / row["file"]) == row["sha256"], "DEB hash")
    replay_signed_inputs(frozen, auth)
    replay_python_inputs(frozen)
    replay_installed_libraries(frozen, auth, installed)
    require(
        type(data["original_commands"]) is dict
        and set(data["original_commands"]) == STATUS_NAMES,
        "original commands",
    )
    for row in data["original_commands"].values():
        require(
            type(row) is dict
            and set(row) == {"exit_code", "log_sha256"}
            and type(row["exit_code"]) is int
            and row["exit_code"] == 0,
            "original status",
        )
        digest_value(row["log_sha256"], "original log")


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def replay_signed_inputs(frozen: Path, authenticated: dict[str, object]) -> None:
    """Check signed Release -> Packages -> exact cached DEBs without installing them."""
    keyring = frozen / "keyring/ubuntu-archive-keyring.gpg"
    require(
        sha(keyring) == KEYRING_SHA256 == authenticated["keyring_sha256"],
        "pinned base keyring",
    )
    release_data: dict[str, dict[str, tuple[str, int]]] = {}
    releases = authenticated["release"]
    indexes = authenticated["indexes"]
    require(
        type(releases) is list and type(indexes) is list and releases and indexes,
        "APT receipt lists",
    )
    require(
        {row["file"] for row in releases}
        == {p.name for p in (frozen / "apt-lists").glob("*_InRelease")},
        "Release set",
    )
    require(
        {row["file"] for row in indexes}
        == {p.name for p in (frozen / "apt-lists").glob("*_Packages*")},
        "Packages index set",
    )
    for row in releases:
        name = row["file"]
        require(
            re.fullmatch(r"[A-Za-z0-9._+-]+_InRelease", name) is not None,
            "Release name",
        )
        path = frozen / "apt-lists" / name
        require(sha(path) == row["sha256"], "Release receipt hash")
        run("gpgv", "--keyring", str(keyring), str(path))
        body = path.read_text()
        require(
            body.startswith("-----BEGIN PGP SIGNED MESSAGE-----\n")
            and body.count("-----BEGIN PGP SIGNATURE-----") == 1,
            "signed Release grammar",
        )
        block = body.split("\nSHA256:\n", 1)[1]
        checks = {}
        for line in block.splitlines():
            match = re.fullmatch(r" ([0-9a-f]{64})\s+(\d+)\s+(\S+)", line)
            if match is None:
                break
            checks[match.group(3)] = (match.group(1), int(match.group(2)))
        require(checks and len(checks) == row["signed_indexes"], "Release hashes")
        release_data[name] = checks
    package_rows: dict[tuple[str, str, str], list[dict[str, str]]] = {}
    for row in indexes:
        name = row["file"]
        require(
            re.fullmatch(r"[A-Za-z0-9._+-]+_Packages(?:\.lz4|\.gz)?", name) is not None,
            "Packages name",
        )
        release = row["release"]
        require(
            release in release_data and row["path"] in release_data[release],
            "index Release binding",
        )
        payload = run(
            "/usr/lib/apt/apt-helper", "cat-file", str(frozen / "apt-lists" / name)
        )
        digest, size = release_data[release][row["path"]]
        require(
            (sha_bytes(payload), len(payload))
            == (digest, size)
            == (row["sha256"], row["size"]),
            "signed Packages bytes",
        )
        for stanza in payload.decode().split("\n\n"):
            fields = dict(re.findall(r"^([A-Za-z0-9-]+): (.*)$", stanza, re.MULTILINE))
            if {
                "Package",
                "Version",
                "Architecture",
                "SHA256",
                "Size",
                "Filename",
            } <= fields.keys():
                package_rows.setdefault(
                    (fields["Package"], fields["Version"], fields["Architecture"]), []
                ).append(fields)
    require(
        len(authenticated["debs"]) == len(list((frozen / "debs").glob("*.deb"))),
        "DEB count",
    )
    selected = set()
    for row in authenticated["debs"]:
        path = frozen / "debs" / row["file"]
        require(
            sha(path) == row["sha256"] and path.stat().st_size == row["size"],
            "DEB bytes",
        )
        key = (row["package"], row["version"], row["architecture"])
        require(
            key not in selected
            and row["architecture"]
            in {"all", (frozen / "architecture.txt").read_text().strip()},
            "DEB identity",
        )
        selected.add(key)
        require(
            any(
                fields["SHA256"] == row["sha256"]
                and int(fields["Size"]) == row["size"]
                and fields["Filename"] == row["signed_index_filename"]
                for fields in package_rows.get(key, [])
            ),
            "DEB absent from signed index",
        )
        metadata = run(
            "dpkg-deb", "-f", str(path), "Package", "Version", "Architecture"
        ).decode()
        values = dict(re.findall(r"^([A-Za-z0-9-]+): (.*)$", metadata, re.MULTILINE))
        require(
            key
            == tuple(
                values.get(field) for field in ("Package", "Version", "Architecture")
            ),
            "DEB payload metadata",
        )


def replay_python_inputs(frozen: Path) -> None:
    pins = (frozen / "recipe/inputs.sha256").read_text().splitlines()
    require(len(pins) == 3, "Python input count")
    for line in pins:
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9._+-]+)", line)
        require(match is not None and match.group(2) in PYTHON, "Python input pin")
        require(
            sha(frozen / "python" / match.group(2)) == match.group(1),
            "Python input hash",
        )
    metadata = strict_json(frozen / "consumer-wheels/pypi-metadata.json")
    require(type(metadata) is list and len(metadata) == 8, "PyPI metadata count")
    expected = {
        "lxml": "6.1.3",
        "python-docx": "1.2.0",
        "python-pptx": "1.0.2",
        "openpyxl": "3.1.5",
        "pillow": "12.3.0",
        "XlsxWriter": "3.2.9",
        "typing_extensions": "4.15.0",
        "et_xmlfile": "2.0.0",
    }
    require(
        {row["package"]: row["version"] for row in metadata} == expected,
        "PyPI package pins",
    )
    names = set()
    for row in metadata:
        require(
            type(row) is dict
            and set(row)
            == {"filename", "metadata_url", "package", "sha256", "url", "version"},
            "PyPI metadata row",
        )
        name = row["filename"]
        require(
            re.fullmatch(r"[A-Za-z0-9_.+-]+\.whl", name) is not None
            and name not in names,
            "PyPI wheel name",
        )
        names.add(name)
        require(
            row["url"].startswith("https://files.pythonhosted.org/packages/")
            and "?" not in row["url"]
            and "@" not in row["url"],
            "PyPI artifact URL",
        )
        require(
            row["metadata_url"]
            == f"https://pypi.org/pypi/{row['package']}/{row['version']}/json",
            "PyPI metadata URL",
        )
        require(
            sha(frozen / "consumer-wheels" / name) == row["sha256"], "PyPI wheel hash"
        )
    require(
        names == {p.name for p in (frozen / "consumer-wheels").glob("*.whl")},
        "PyPI wheel set",
    )


def replay_installed_libraries(
    frozen: Path, authenticated: dict[str, object], installed: dict[str, object]
) -> None:
    libraries = installed["libraries"]
    require(XML_LIBRARIES <= libraries.keys(), "XML library set")
    packages = {row["package"]: row for row in authenticated["debs"]}
    with tempfile.TemporaryDirectory(prefix="lxml-deb-payload-") as temp:
        roots = {}
        for soname in XML_LIBRARIES:
            row = libraries[soname]
            owner = row["owner"].split(":", 1)[0]
            require(
                owner in packages and row["path"].startswith("/usr/lib/"),
                "library owner",
            )
            if owner not in roots:
                target = Path(temp) / owner
                target.mkdir()
                run(
                    "dpkg-deb",
                    "-x",
                    str(frozen / "debs" / packages[owner]["file"]),
                    str(target),
                )
                roots[owner] = target
            payload = roots[owner] / row["path"].lstrip("/")
            require(
                payload.is_file()
                and not payload.is_symlink()
                and sha(payload) == row["sha256"],
                "installed library differs from authenticated DEB payload at reported path",
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    export_cmd = sub.add_parser("export")
    for name in (
        "scratch",
        "repo",
        "recipe",
        "status",
        "out",
        "scope",
        "commit",
        "tree",
        "recipe-sha256",
        "frozen-sha256",
        "wheel-sha256",
    ):
        export_cmd.add_argument("--" + name, required=True)
    check_cmd = sub.add_parser("check")
    for name in (
        "bundle",
        "repo",
        "commit",
        "tree",
        "recipe-sha256",
        "frozen-sha256",
        "wheel-sha256",
    ):
        check_cmd.add_argument("--" + name, required=True)
    args = parser.parse_args()
    try:
        if args.action == "export":
            export(args)
        else:
            bundle, repo = cli_path(args.bundle), cli_path(args.repo)
            check(
                bundle,
                repo,
                args.commit,
                args.tree,
                args.recipe_sha256,
                args.frozen_sha256,
                args.wheel_sha256,
            )
            print(
                json.dumps(
                    {
                        "checked": True,
                        "scope": strict_json(bundle / "bundle.json")["scope"],
                    },
                    sort_keys=True,
                )
            )
    except (
        OSError,
        ValueError,
        KeyError,
        IndexError,
        TypeError,
        subprocess.CalledProcessError,
        zipfile.BadZipFile,
    ) as error:
        raise SystemExit(f"evidence refused: {type(error).__name__}") from None


if __name__ == "__main__":
    main()
