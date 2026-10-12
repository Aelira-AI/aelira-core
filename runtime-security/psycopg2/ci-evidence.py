#!/usr/bin/env python3
"""Copy a bounded psycopg2 native run into a separate public evidence directory.

This copier records the caller's original exit statuses. It never turns a
failed or incomplete native run into an accepted component.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys

MAX_COUNT = 400
MAX_FILE = 64 * 1024 * 1024
MAX_TOTAL = 256 * 1024 * 1024
MAX_JSON = 128 * 1024
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
RECIPE = (
    "accept-native.sh",
    "build-offline-container.sh",
    "consumer.py",
    "fetch-native.sh",
    "inputs.sha256",
    "prepare-native.sh",
    "run-synthetic-db.sh",
    "verify-frozen.py",
    "verify_installed.py",
)
CONTROLS = (
    "unicode-jsonb",
    "parameterized-query",
    "error-savepoint-recovery",
    "copy-in-out",
    "sqlalchemy-pool",
    "verify-full-localhost-query",
    "verify-full-wrong-host-refusal",
    "verify-full-unrelated-ca-refusal",
)
DEB = re.compile(r"[a-z0-9][a-z0-9+.-]*_[A-Za-z0-9.%+~:-]+_(?:all|amd64|arm64)\.deb\Z")
INDEX = re.compile(
    r"(?:ports\.ubuntu\.com_ubuntu-ports|archive\.ubuntu\.com_ubuntu|security\.ubuntu\.com_ubuntu)_dists_[a-z0-9-]+_(?:InRelease|[a-z]+_binary-(?:arm64|amd64)_Packages\.lz4)\Z"
)
PYTHON = re.compile(
    r"(?:psycopg2_binary-2\.9\.13\.tar\.gz|pip-26\.2\.1-py3-none-any\.whl|setuptools-84\.0\.0-py3-none-any\.whl)\Z"
)
PACKAGE = re.compile(r"[a-z0-9][a-z0-9+.-]*(?::(?:all|amd64|arm64))?\Z")
VERSION = re.compile(r"[A-Za-z0-9.+:~%-]{1,100}\Z")
LIBRARY = re.compile(r"lib[A-Za-z0-9+_.-]+\.so(?:\.[0-9]+)*\Z")
WHEEL = re.compile(
    r"psycopg2_binary-2\.9\.13-cp[0-9]+-cp[0-9]+-linux_(?:aarch64|x86_64)\.whl\Z"
)


def safe_root(path: Path, *, exists: bool) -> Path:
    if not path.is_absolute():
        raise ValueError("source, destination and repository must be absolute")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        if part in ("", ".", ".."):
            raise ValueError("unsafe root path")
        current /= part
        if current.exists() or current.is_symlink():
            mode = current.lstat().st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                raise ValueError("root has a symlink or non-directory component")
        elif exists:
            raise ValueError("root missing")
    if not exists and path.exists():
        raise ValueError("destination already exists")
    return path


def safe_file(root: Path, rel: str) -> Path:
    parts = rel.split("/")
    if not rel or any(p in ("", ".", "..") for p in parts):
        raise ValueError("unsafe relative path")
    current = root
    for part in parts:
        current /= part
        mode = current.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise ValueError("symlink in evidence path")
    if not stat.S_ISREG(current.lstat().st_mode):
        raise ValueError("evidence is not a regular file")
    return current


def digest(path: Path, maximum: int | None = None) -> tuple[str, int]:
    if maximum is None:
        maximum = MAX_FILE
    size = path.stat().st_size
    if size > maximum:
        raise ValueError("oversized evidence file")
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    if path.stat().st_size != size:
        raise ValueError("source file changed while hashing")
    return h.hexdigest(), size


def small_text(root: Path, rel: str, maximum: int = MAX_JSON) -> str:
    path = safe_file(root, rel)
    digest(path, maximum)
    return path.read_text(encoding="utf-8")


def json_report(root: Path, rel: str) -> object:
    def unique(pairs: list[tuple[str, object]]) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON object key")
            result[key] = value
        return result

    def reject_constant(_value: str) -> None:
        raise ValueError("nonstandard JSON constant")

    return json.loads(
        small_text(root, rel), object_pairs_hook=unique, parse_constant=reject_constant
    )


def only_keys(value: object, keys: set[str]) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("unexpected report fields")
    return value


def sha(value: object) -> bool:
    return isinstance(value, str) and bool(HEX64.fullmatch(value))


def bounded_int(value: object, minimum: int, maximum: int) -> bool:
    return type(value) is int and minimum <= value <= maximum


def clean_git(repo: Path, revision: str, tree: str) -> None:
    if not HEX40.fullmatch(revision) or not HEX40.fullmatch(tree):
        raise ValueError("invalid revision or tree")
    for ref, expected in (("HEAD", revision), ("HEAD^{tree}", tree)):
        actual = subprocess.run(
            ["/usr/bin/git", "-C", str(repo), "rev-parse", "--verify", ref],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if actual != expected:
            raise ValueError("stale source revision or tree")
    paths = [f"runtime-security/psycopg2/{name}" for name in RECIPE]
    subprocess.run(
        ["/usr/bin/git", "-C", str(repo), "ls-files", "--error-unmatch", "--", *paths],
        check=True,
        capture_output=True,
    )
    if subprocess.run(
        [
            "/usr/bin/git",
            "-C",
            str(repo),
            "status",
            "--porcelain",
            "--",
            "runtime-security/psycopg2",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout:
        raise ValueError("public psycopg2 checkout scope is dirty")


def frozen_plan(source: Path, repo: Path, arch: str) -> tuple[dict[str, str], set[str]]:
    if small_text(source, "frozen/architecture.txt", 16).strip() != arch:
        raise ValueError("frozen architecture mismatch")
    entries: dict[str, str] = {}
    for line in small_text(source, "frozen/frozen.sha256", 64 * 1024).splitlines():
        match = re.fullmatch(
            r"([a-f0-9]{64})  (?:\./)?((?:apt-lists|debs|python|recipe|keyring)/[^/]+)",
            line,
        )
        if not match:
            raise ValueError("invalid frozen manifest")
        expected, relative = match.groups()
        group, name = relative.split("/", 1)
        valid = {
            "apt-lists": bool(INDEX.fullmatch(name)),
            "debs": bool(DEB.fullmatch(name))
            and name.endswith((f"_{arch}.deb", "_all.deb")),
            "python": bool(PYTHON.fullmatch(name)),
            "recipe": name in RECIPE,
            "keyring": name == "ubuntu-archive-keyring.gpg",
        }[group]
        if (
            group == "apt-lists"
            and "_binary-" in name
            and f"_binary-{arch}_" not in name
        ):
            valid = False
        if not valid or relative in entries:
            raise ValueError("invalid, duplicate or wrong-architecture frozen entry")
        entries[relative] = expected
    if not 1 <= len(entries) <= MAX_COUNT - 20:
        raise ValueError("empty or excessive frozen manifest")
    for group in ("apt-lists", "debs", "python", "recipe", "keyring"):
        if not any(name.startswith(group + "/") for name in entries):
            raise ValueError("missing frozen input group")
    if {
        name.removeprefix("recipe/") for name in entries if name.startswith("recipe/")
    } != set(RECIPE):
        raise ValueError("incomplete frozen recipe")
    if {
        name.removeprefix("python/") for name in entries if name.startswith("python/")
    } != {
        "psycopg2_binary-2.9.13.tar.gz",
        "pip-26.2.1-py3-none-any.whl",
        "setuptools-84.0.0-py3-none-any.whl",
    }:
        raise ValueError("incomplete Python inputs")
    if "keyring/ubuntu-archive-keyring.gpg" not in entries:
        raise ValueError("missing frozen Ubuntu public keyring")
    plan = {"frozen/architecture.txt", "frozen/frozen.sha256"}
    for relative, expected in entries.items():
        frozen = f"frozen/{relative}"
        if digest(safe_file(source, frozen))[0] != expected:
            raise ValueError("frozen input hash mismatch")
        if relative.startswith("recipe/"):
            live = safe_file(
                repo, "runtime-security/psycopg2/" + relative.removeprefix("recipe/")
            )
            if digest(live)[0] != expected:
                raise ValueError("live source differs from frozen recipe")
        plan.add(frozen)
    pinned = {}
    for line in small_text(
        repo, "runtime-security/psycopg2/inputs.sha256", 2048
    ).splitlines():
        match = re.fullmatch(r"([a-f0-9]{64})  ([^/ ]+)", line)
        if (
            not match
            or not PYTHON.fullmatch(match.group(2))
            or match.group(2) in pinned
        ):
            raise ValueError("invalid pinned Python manifest")
        pinned[match.group(2)] = match.group(1)
    if set(pinned) != {
        n.removeprefix("python/") for n in entries if n.startswith("python/")
    }:
        raise ValueError("Python pin list mismatch")
    for name, expected in pinned.items():
        if entries["python/" + name] != expected:
            raise ValueError("Python input hash differs from public pin")
    return entries, plan


def validate_auth(root: Path, rel: str, entries: dict[str, str], arch: str) -> None:
    report = only_keys(
        json_report(root, rel), {"keyring_sha256", "release", "indexes", "debs"}
    )
    if report["keyring_sha256"] != entries["keyring/ubuntu-archive-keyring.gpg"]:
        raise ValueError("authenticated keyring hash mismatch")
    expected = {
        "release": {"file", "sha256", "signed_indexes"},
        "indexes": {"file", "release", "path", "sha256", "size"},
        "debs": {
            "file",
            "package",
            "version",
            "architecture",
            "sha256",
            "size",
            "signed_index_filename",
        },
    }
    signed: dict[str, dict[str, tuple[str, int]]] = {}
    for section, keys in expected.items():
        rows = report[section]
        if not isinstance(rows, list) or not rows or len(rows) > MAX_COUNT:
            raise ValueError("invalid authentication inventory")
        seen = set()
        for row in rows:
            item = only_keys(row, keys)
            name = item["file"]
            if not isinstance(name, str) or name in seen:
                raise ValueError("duplicate authentication inventory")
            seen.add(name)
            if section == "debs":
                if (
                    not DEB.fullmatch(name)
                    or type(item["architecture"]) is not str
                    or item["architecture"] not in (arch, "all")
                    or type(item["package"]) is not str
                    or not PACKAGE.fullmatch(item["package"])
                    or type(item["version"]) is not str
                    or not VERSION.fullmatch(item["version"])
                ):
                    raise ValueError("invalid DEB identity")
                if (
                    not isinstance(item["signed_index_filename"], str)
                    or not re.fullmatch(
                        r"pool/[A-Za-z0-9_+./%~-]{1,220}\.deb",
                        item["signed_index_filename"],
                    )
                    or ".." in item["signed_index_filename"]
                ):
                    raise ValueError("invalid authenticated DEB path")
                source_name = "debs/" + name
            else:
                if (
                    not INDEX.fullmatch(name)
                    or (section == "release" and not name.endswith("_InRelease"))
                    or (section == "indexes" and "_Packages" not in name)
                ):
                    raise ValueError("invalid signed index identity")
                source_name = "apt-lists/" + name
            if not sha(item["sha256"]) or (
                section != "release"
                and not bounded_int(item["size"], 0, 4 * 1024 * 1024 * 1024)
            ):
                raise ValueError("invalid authentication digest or size")
            if section != "indexes" and (
                entries.get(source_name) != item["sha256"]
                or (
                    section == "debs"
                    and digest(safe_file(root, "frozen/" + source_name))[1]
                    != item["size"]
                )
            ):
                raise ValueError("authenticated payload mismatch")
            if section == "release":
                if not bounded_int(item["signed_indexes"], 1, 10000):
                    raise ValueError("invalid signed index count")
                body = small_text(root, "frozen/" + source_name, 1024 * 1024)
                if (
                    "-----BEGIN PGP SIGNED MESSAGE-----" not in body
                    or "\nSHA256:\n" not in body
                ):
                    raise ValueError("invalid signed Release envelope")
                block = body.split("\nSHA256:\n", 1)[1]
                rows: dict[str, tuple[str, int]] = {}
                for line in block.splitlines():
                    match = re.fullmatch(r" ([0-9a-f]{64})\s+(\d+)\s+(\S+)", line)
                    if not match:
                        break
                    index_hash, index_size, index_path = match.groups()
                    if index_path in rows or not bounded_int(
                        int(index_size), 0, 4 * 1024 * 1024 * 1024
                    ):
                        raise ValueError("invalid signed Release entry")
                    rows[index_path] = (index_hash, int(index_size))
                if len(rows) != item["signed_indexes"]:
                    raise ValueError("signed Release count mismatch")
                signed[name] = rows
            if section == "indexes":
                if (
                    type(item["release"]) is not str
                    or item["release"] not in signed
                    or type(item["path"]) is not str
                    or not re.fullmatch(
                        r"[a-z]+/binary-(?:arm64|amd64)/Packages", item["path"]
                    )
                    or f"binary-{arch}/" not in item["path"]
                ):
                    raise ValueError("index authentication mismatch")
                if signed[item["release"]].get(item["path"]) != (
                    item["sha256"],
                    item["size"],
                ):
                    raise ValueError("index digest differs from signed Release")
    if {"apt-lists/" + r["file"] for r in report["release"] + report["indexes"]} != {
        n for n in entries if n.startswith("apt-lists/")
    }:
        raise ValueError("incomplete authenticated index inventory")
    if {"debs/" + r["file"] for r in report["debs"]} != {
        n for n in entries if n.startswith("debs/")
    }:
        raise ValueError("incomplete authenticated DEB inventory")


def validate_installed(value: object) -> dict:
    d = only_keys(
        value,
        {
            "package",
            "version",
            "record_hashes_verified",
            "extension_sha256",
            "compiled_libpq",
            "runtime_libpq",
            "loaded_library_sha256",
            "loaded_library_packages",
        },
    )
    if (
        d["package"] != "psycopg2-binary"
        or d["version"] != "2.9.13"
        or not bounded_int(d["record_hashes_verified"], 19, 1000)
        or not sha(d["extension_sha256"])
    ):
        raise ValueError("invalid installed wheel identity")
    for key in ("compiled_libpq", "runtime_libpq"):
        if type(d[key]) is not int or not 100000 <= d[key] <= 999999:
            raise ValueError("invalid libpq version")
    libs = d["loaded_library_sha256"]
    owners = d["loaded_library_packages"]
    if (
        not isinstance(libs, dict)
        or not isinstance(owners, dict)
        or set(libs) != set(owners)
        or not 3 <= len(libs) <= 40
        or not {"libpq.so.5", "libssl.so.3", "libcrypto.so.3"} <= set(libs)
    ):
        raise ValueError("invalid loaded libraries")
    if any(
        not LIBRARY.fullmatch(k)
        or not sha(v)
        or not isinstance(owners[k], str)
        or not PACKAGE.fullmatch(owners[k])
        for k, v in libs.items()
    ):
        raise ValueError("unsafe loaded library receipt")
    return d


def validate_consumer(value: object, installed: dict) -> dict:
    d = only_keys(
        value,
        {
            "passed",
            "driver_version",
            "compiled_libpq",
            "runtime_libpq",
            "postgres_server_major",
            "tls_protocol",
            "controls",
        },
    )
    if (
        d["passed"] is not True
        or not isinstance(d["driver_version"], str)
        or not re.fullmatch(r"2\.9\.13(?: \([A-Za-z0-9 ]+\))?", d["driver_version"])
    ):
        raise ValueError("consumer did not pass or has invalid driver identity")
    if (
        not bounded_int(d["compiled_libpq"], 100000, 999999)
        or not bounded_int(d["runtime_libpq"], 100000, 999999)
        or d["compiled_libpq"] != installed["compiled_libpq"]
        or d["runtime_libpq"] != installed["runtime_libpq"]
        or type(d["postgres_server_major"]) is not int
        or d["postgres_server_major"] != 16
        or type(d["tls_protocol"]) is not str
        or d["tls_protocol"] not in ("TLSv1.2", "TLSv1.3")
        or d["controls"] != list(CONTROLS)
    ):
        raise ValueError("consumer identity or controls mismatch")
    return d


def harness(root: Path, arch: str, wheel_hash: str) -> dict[str, str]:
    values = {}
    for line in small_text(
        root, "synthetic-db/harness-identity.txt", 2048
    ).splitlines():
        if not re.fullmatch(r"[a-z0-9_]+=[A-Za-z0-9:]+", line):
            raise ValueError("unsafe harness identity")
        key, value = line.split("=", 1)
        if key in values:
            raise ValueError("duplicate harness identity")
        values[key] = value
    if set(values) != {
        "host_uid",
        "host_gid",
        "probe_uid",
        "probe_gid",
        "architecture",
        "frozen_manifest_sha256",
        "frozen_consumer_sha256",
        "frozen_verifier_sha256",
        "wheel_sha256",
        "runtime_image_id",
    }:
        raise ValueError("incomplete harness identity")
    if (
        any(
            not re.fullmatch(r"[1-9][0-9]{0,8}", values[k])
            for k in ("host_uid", "host_gid", "probe_uid", "probe_gid")
        )
        or values["host_uid"] != values["probe_uid"]
        or values["host_gid"] != values["probe_gid"]
    ):
        raise ValueError("host/probe user mismatch")
    if (
        values["architecture"] != arch
        or values["wheel_sha256"] != wheel_hash
        or not re.fullmatch(r"sha256:[a-f0-9]{64}", values["runtime_image_id"])
    ):
        raise ValueError("harness wheel, image or architecture mismatch")
    for key, relative in (
        ("frozen_manifest_sha256", "frozen/frozen.sha256"),
        ("frozen_consumer_sha256", "frozen/recipe/consumer.py"),
        ("frozen_verifier_sha256", "frozen/recipe/verify_installed.py"),
    ):
        if values[key] != digest(safe_file(root, relative))[0]:
            raise ValueError("harness frozen probe mismatch")
    return values


def build_receipts(root: Path, arch: str) -> tuple[dict[str, str], set[str]]:
    wheel_names = []
    hashes = {}
    plan = set()
    for run in ("a", "b"):
        directory = root / f"build-{run}"
        if not stat.S_ISDIR(directory.lstat().st_mode):
            raise ValueError("missing build directory")
        names = [p.name for p in directory.iterdir() if p.name.endswith(".whl")]
        if (
            len(names) != 1
            or not WHEEL.fullmatch(names[0])
            or ("aarch64" if arch == "arm64" else "x86_64") not in names[0]
        ):
            raise ValueError("wrong or duplicate native wheel")
        wheel_names.append(names[0])
        rel = f"build-{run}/{names[0]}"
        hashes[run] = digest(safe_file(root, rel))[0]
        raw = small_text(root, f"build-{run}/wheel.sha256", 512).strip()
        match = re.fullmatch(r"([a-f0-9]{64})  (/.+/)?([^/]+\.whl)", raw)
        if not match or match.group(1) != hashes[run] or match.group(3) != names[0]:
            raise ValueError("wheel SHA manifest mismatch")
        plan.add(rel)
        for name in (
            "authenticated-packages.json",
            "installed-build-packages.txt",
            "python-build-tools.json",
        ):
            rel = f"build-{run}/{name}"
            small_text(root, rel, 128 * 1024)
            plan.add(rel)
        rows = small_text(
            root, f"build-{run}/installed-build-packages.txt"
        ).splitlines()
        if not 20 <= len(rows) <= 500 or any(
            not re.fullmatch(
                r"[a-z0-9][a-z0-9+.-]*(?::(?:all|amd64|arm64))?\t[A-Za-z0-9.+:~%-]{1,100}",
                row,
            )
            for row in rows
        ):
            raise ValueError("unsafe build package inventory")
        tools = json_report(root, f"build-{run}/python-build-tools.json")
        if (
            not isinstance(tools, list)
            or not 2 <= len(tools) <= 10
            or any(
                not isinstance(t, dict)
                or set(t) != {"name", "version"}
                or type(t["name"]) is not str
                or not PACKAGE.fullmatch(t["name"].lower())
                or type(t["version"]) is not str
                or not VERSION.fullmatch(t["version"])
                for t in tools
            )
        ):
            raise ValueError("unsafe Python build tool inventory")
    if wheel_names[0] != wheel_names[1] or hashes["a"] != hashes["b"]:
        raise ValueError("two native wheels differ")
    for name in (
        "authenticated-packages.json",
        "installed-build-packages.txt",
        "python-build-tools.json",
    ):
        if (
            digest(safe_file(root, f"build-a/{name}"))[0]
            != digest(safe_file(root, f"build-b/{name}"))[0]
        ):
            raise ValueError("two native build inventories differ")
    return {"wheel_name": wheel_names[0], "wheel_sha256": hashes["a"]}, plan


def complete_export(
    source: Path,
    dest: Path,
    repo: Path,
    arch: str,
    revision: str,
    tree: str,
    build_exit: int,
    consumer_exit: int,
) -> dict:
    safe_root(source, exists=True)
    safe_root(repo, exists=True)
    clean_git(repo, revision, tree)
    entries, plan = frozen_plan(source, repo, arch)
    keyring = safe_file(source, "frozen/keyring/ubuntu-archive-keyring.gpg")
    keyring_hash, keyring_size = digest(keyring, 5 * 1024 * 1024)
    if keyring_size < 1024:
        raise ValueError("Ubuntu public keyring missing or too small")
    wheel, build_plan = build_receipts(source, arch)
    plan |= build_plan
    for run in ("a", "b"):
        validate_auth(source, f"build-{run}/authenticated-packages.json", entries, arch)
    installed = validate_installed(json_report(source, "synthetic-db/installed.json"))
    consumer = validate_consumer(
        json_report(source, "synthetic-db/consumers.json"), installed
    )
    ident = harness(source, arch, wheel["wheel_sha256"])
    plan |= {
        "synthetic-db/installed.json",
        "synthetic-db/consumers.json",
        "synthetic-db/harness-identity.txt",
    }
    if len(plan) > MAX_COUNT:
        raise ValueError("evidence count exceeds limit")
    files = []
    total = 0
    for relative in sorted(plan):
        path = safe_file(source, relative)
        file_hash, size = digest(path)
        total += size
        if total > MAX_TOTAL:
            raise ValueError("evidence total exceeds limit")
        files.append({"path": relative, "sha256": file_hash, "size": size})
    for entry in files:
        target = dest / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        source_path = safe_file(source, entry["path"])
        with source_path.open("rb") as origin, target.open("xb") as output:
            for chunk in iter(lambda: origin.read(1024 * 1024), b""):
                output.write(chunk)
        if digest(target) != (entry["sha256"], entry["size"]):
            raise ValueError("source changed during copy")
    manifest = {
        "schema": 1,
        "architecture": arch,
        "revision": revision,
        "tree": tree,
        "files": files,
    }
    (dest / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n"
    )
    accepted = build_exit == 0 and consumer_exit == 0
    summary = {
        "schema": 1,
        "architecture": arch,
        "revision": revision,
        "tree": tree,
        "build_exit": build_exit,
        "consumer_exit": consumer_exit,
        "evidence_complete": True,
        "component_accepted": accepted,
        "disposition": "accepted" if accepted else "failed_original_exit",
        "wheel_name": wheel["wheel_name"],
        "wheel_sha256": wheel["wheel_sha256"],
        "frozen_manifest_sha256": ident["frozen_manifest_sha256"],
        "ubuntu_keyring_sha256": keyring_hash,
        "file_count": len(files),
        "total_bytes": total,
        "installed_record_hashes_verified": installed["record_hashes_verified"],
        "loaded_library_count": len(installed["loaded_library_sha256"]),
        "consumer_control_count": len(consumer["controls"]),
    }
    (dest / "summary.json").write_text(
        json.dumps(summary, sort_keys=True, indent=2) + "\n"
    )
    return summary


def export(
    source: Path,
    dest: Path,
    repo: Path,
    arch: str,
    revision: str,
    tree: str,
    build_exit: int,
    consumer_exit: int,
) -> dict:
    if (
        arch not in ("amd64", "arm64")
        or not HEX40.fullmatch(revision)
        or not HEX40.fullmatch(tree)
    ):
        raise ValueError("invalid architecture or source identity")
    if not bounded_int(build_exit, 0, 255) or not bounded_int(consumer_exit, 0, 255):
        raise ValueError("invalid original process exit")
    safe_root(dest, exists=False)
    if (
        dest == source
        or source in dest.parents
        or dest in source.parents
        or repo in dest.parents
    ):
        raise ValueError("destination overlaps source or repository")
    dest.mkdir(parents=True, exist_ok=False)
    refused = {
        "schema": 1,
        "architecture": arch,
        "revision": revision,
        "tree": tree,
        "build_exit": build_exit,
        "consumer_exit": consumer_exit,
        "evidence_complete": False,
        "component_accepted": False,
        "disposition": "refused",
        "file_count": 0,
        "total_bytes": 0,
    }
    (dest / "summary.json").write_text(
        json.dumps(refused, sort_keys=True, indent=2) + "\n"
    )
    try:
        return complete_export(
            source, dest, repo, arch, revision, tree, build_exit, consumer_exit
        )
    except Exception:
        # Detailed evidence can be partial if a copy or post-copy check fails.
        # Retain only the bounded caller-supplied status, never failed raw data.
        shutil.rmtree(dest)
        dest.mkdir()
        (dest / "summary.json").write_text(
            json.dumps(refused, sort_keys=True, indent=2) + "\n"
        )
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "source",
        "dest",
        "repo",
        "arch",
        "revision",
        "tree",
        "build-exit",
        "consumer-exit",
    ):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    if args.arch not in ("amd64", "arm64"):
        parser.error("architecture must be amd64 or arm64")
    for name in ("build_exit", "consumer_exit"):
        value = getattr(args, name)
        if not re.fullmatch(r"(?:0|[1-9][0-9]{0,2})", value) or int(value) > 255:
            parser.error("exit status must be an integer from 0 to 255")
    try:
        result = export(
            Path(args.source),
            Path(args.dest),
            Path(args.repo),
            args.arch,
            args.revision,
            args.tree,
            int(args.build_exit),
            int(args.consumer_exit),
        )
    except (
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        AttributeError,
        OSError,
        subprocess.CalledProcessError,
        UnicodeError,
        json.JSONDecodeError,
    ) as error:
        print(f"psycopg2 evidence refused: {type(error).__name__}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                k: result[k]
                for k in (
                    "architecture",
                    "component_accepted",
                    "file_count",
                    "total_bytes",
                    "wheel_sha256",
                )
            },
            sort_keys=True,
        )
    )
    return 0 if result["component_accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
