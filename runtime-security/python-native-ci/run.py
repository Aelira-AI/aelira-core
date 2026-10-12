#!/usr/bin/env python3
"""Native, source-bound orchestration for the two reviewed Python replacements."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import posixpath
import re
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

ORIGIN = "https://github.com/Aelira-AI/aelira-core.git"
ORIGINS = {ORIGIN, "https://github.com/Aelira-AI/aelira-core"}
BASE = "ubuntu:26.04@sha256:f144425ff09be612d6d9ad965196e9cdc23dae1f42110a8a11a3e9a8198759f7"
ZIP_SHA256 = {
    "amd64": "53909f0ff4bacb69e497b63620562f8f9bd28b9070505c42124125123451c58d",
    "arm64": "bda119ff252cc18c16a73b0e6b6a48a775f052b2796db3fdd2cdba7784b818a5",
}
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
IMAGE = re.compile(r"sha256:[0-9a-f]{64}\Z")
FIXTURE = Path("runtime-security/python-consumer-fixture")
PILLOW = Path("runtime-security/media/pillow")
PSYC = Path("runtime-security/psycopg2")
NOT_RUN = {"exit": 125, "state": "not_run", "reason": "dependency_not_passed"}
MAX_ZIP = 1024 * 1024 * 1024
MAX_DEB = 128 * 1024 * 1024
MAX_PUBLIC_FILES = 300
MAX_PUBLIC_FILE = 128 * 1024 * 1024
MAX_PUBLIC_TOTAL = 768 * 1024 * 1024
PILLOW_CONSUMER_STAGES = {
    "preflight",
    "candidate_install",
    "candidate_commit",
    "source_identity",
    "installed_verifier",
    "final_proof",
    "complete",
}


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def strict_json(path: Path, limit: int = 256 * 1024) -> object:
    if path.stat().st_size > limit:
        raise ValueError("oversized JSON receipt")

    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def reject(_: str) -> object:
        raise ValueError("nonstandard JSON constant")

    return json.loads(path.read_text(), object_pairs_hook=pairs, parse_constant=reject)


def exact_dict(value: object, keys: set[str]) -> dict[str, object]:
    if type(value) is not dict or set(value) != keys:
        raise ValueError("receipt schema differs")
    return value


def sha(value: object, size: int = 64) -> bool:
    return type(value) is str and bool(
        (HEX64 if size == 64 else HEX40).fullmatch(value)
    )


def owned_parent(path: Path) -> None:
    if (
        not path.is_absolute()
        or path.exists()
        or path.is_symlink()
        or path.resolve() != path
    ):
        raise ValueError("output must be a new canonical absolute path")
    parent = path.parent
    if (
        parent.resolve(strict=True) != parent
        or parent.stat().st_uid != os.getuid()
        or not parent.is_dir()
    ):
        raise ValueError("output parent must be canonical and owned")


def canonical_file(path: Path, max_size: int) -> None:
    if not path.is_absolute() or path.resolve(strict=True) != path or path.is_symlink():
        raise ValueError("input must be a canonical absolute regular file")
    mode = path.stat().st_mode
    if not stat.S_ISREG(mode) or not 0 < path.stat().st_size <= max_size:
        raise ValueError("input file type or size invalid")


def probe(args: list[str], *, cwd: Path | None = None, timeout: int = 30) -> str:
    result = subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=True
    )
    return result.stdout.strip()


def git(repo: Path, *args: str) -> str:
    return probe(["/usr/bin/git", "-C", str(repo), *args])


def identity(repo: Path, arch: str, revision: str) -> dict[str, str]:
    if arch not in ZIP_SHA256 or not HEX40.fullmatch(revision):
        raise ValueError("architecture or revision invalid")
    if os.getuid() == 0 or platform.system() != "Linux":
        raise ValueError("nonroot native Linux required")
    if repo.resolve(strict=True) != repo or git(
        repo, "rev-parse", "--show-toplevel"
    ) != str(repo):
        raise ValueError("repository root is not canonical")
    observed_origin = git(repo, "remote", "get-url", "origin")
    if observed_origin not in ORIGINS:
        raise ValueError("public origin mismatch")
    if git(repo, "rev-parse", "HEAD") != revision:
        raise ValueError("HEAD differs from requested public revision")
    tree = git(repo, "rev-parse", "HEAD^{tree}")
    if not HEX40.fullmatch(tree) or git(
        repo, "status", "--porcelain", "--untracked-files=all"
    ):
        raise ValueError("public checkout not clean")
    for relative in (
        FIXTURE / "prepare.py",
        FIXTURE / "integrity.py",
        FIXTURE / "native.py",
        FIXTURE / "test-prepare.py",
        FIXTURE / "test-integrity.py",
        FIXTURE / "test-native.py",
        PILLOW / "build.sh",
        PILLOW / "test-installed.sh",
        PILLOW / "ci-evidence.py",
        PILLOW / "verify-tiff-input.py",
        PSYC / "prepare-native.sh",
        PSYC / "accept-native.sh",
        PSYC / "run-synthetic-db.sh",
        PSYC / "ci-evidence.py",
        Path("requirements.txt"),
    ):
        path = repo / relative
        canonical_file(path, 4 * 1024 * 1024)
        if git(repo, "ls-files", "--error-unmatch", "--", str(relative)) != str(
            relative
        ):
            raise ValueError("required source is untracked")
        if git(repo, "hash-object", "--", str(path)) != git(
            repo, "rev-parse", f"HEAD:{relative}"
        ):
            raise ValueError("required source bytes differ from commit")
    machine = platform.machine()
    if (arch, machine) not in {("amd64", "x86_64"), ("arm64", "aarch64")}:
        raise ValueError("host architecture differs")
    daemon = probe(["docker", "info", "--format", "{{.Architecture}}"])
    if daemon not in ({"amd64", "x86_64"} if arch == "amd64" else {"arm64", "aarch64"}):
        raise ValueError("Docker daemon architecture differs")
    return {
        "revision": revision,
        "tree": tree,
        "architecture": arch,
        "origin": observed_origin,
    }


def extract_tiff(zip_path: Path, output: Path, arch: str) -> None:
    if digest(zip_path) != ZIP_SHA256[arch]:
        raise ValueError("accepted TIFF artifact digest differs")
    names = {
        f"build-a/{name}_4.7.2-0aelira1_{arch}.deb"
        for name in ("libtiff6", "libtiffxx6", "libtiff-dev")
    }
    selected: dict[str, zipfile.ZipInfo] = {}
    with zipfile.ZipFile(zip_path) as archive:
        entries = archive.infolist()
        if len(entries) > 10000:
            raise ValueError("TIFF artifact inventory excessive")
        for entry in entries:
            logical = posixpath.normpath(entry.filename.replace("\\", "/"))
            if logical not in names:
                continue
            mode = entry.external_attr >> 16
            if (
                logical in selected
                or entry.filename != logical
                or entry.is_dir()
                or not stat.S_ISREG(mode)
                or not 0 < entry.file_size <= MAX_DEB
            ):
                raise ValueError("required TIFF member unsafe or duplicated")
            selected[logical] = entry
        if set(selected) != names:
            raise ValueError("accepted TIFF artifact incomplete")
        output.mkdir(mode=0o700)
        for name, entry in selected.items():
            target = output / Path(name).name
            size = 0
            with archive.open(entry) as source, target.open("xb") as sink:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    size += len(block)
                    if size > MAX_DEB:
                        raise ValueError("TIFF member expansion exceeded limit")
                    sink.write(block)
            if size != entry.file_size:
                raise ValueError("TIFF member size differs")


def run_stage(
    name: str, command: list[str], scratch: Path, *, timeout: int = 7200
) -> dict[str, object]:
    log = scratch / "logs" / f"{name}.log"
    with log.open("xb") as output:
        try:
            result = subprocess.run(
                command,
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError:
            return {"exit": None, "state": "unavailable", "reason": "command_missing"}
        except subprocess.TimeoutExpired:
            return {"exit": None, "state": "unavailable", "reason": "timeout"}
        except OSError:
            return {"exit": None, "state": "unavailable", "reason": "process_error"}
    return {
        "exit": result.returncode if 0 <= result.returncode <= 255 else None,
        "state": (
            "passed"
            if result.returncode == 0
            else ("failed" if result.returncode > 0 else "unavailable")
        ),
        "reason": (
            "none"
            if result.returncode == 0
            else (
                "process_nonzero" if result.returncode > 0 else "terminated_by_signal"
            )
        ),
    }


def passed(status: dict[str, object]) -> bool:
    return status["state"] == "passed" and status["exit"] == 0


def pillow_consumer_diagnostic(
    root: Path, status: dict[str, object]
) -> dict[str, object]:
    unavailable = {"schema": 1, "state": "unavailable", "stage": None, "exit": None}
    observed_exit = status.get("exit")
    if (
        type(observed_exit) is not int
        or not 0 <= observed_exit <= 255
        or status.get("state") != ("passed" if observed_exit == 0 else "failed")
    ):
        return unavailable
    try:
        path = root / "consumer-stage.json"
        counter = root / "consumer-exit.txt"
        canonical_file(path, 256)
        canonical_file(counter, 4)
        value = exact_dict(strict_json(path, 256), {"schema", "stage", "exit"})
        if (
            type(value["schema"]) is not int
            or value["schema"] != 1
            or type(value["stage"]) is not str
            or value["stage"] not in PILLOW_CONSUMER_STAGES
            or type(value["exit"]) is not int
            or value["exit"] != observed_exit
            or counter.read_bytes() != f"{observed_exit}\n".encode("ascii")
            or (observed_exit == 0 and value["stage"] != "complete")
        ):
            return unavailable
        return {
            "schema": 1,
            "state": "reported",
            "stage": value["stage"],
            "exit": observed_exit,
        }
    except (OSError, ValueError, TypeError, UnicodeError):
        return unavailable


def prepare_receipt(
    path: Path, identity_data: dict[str, str], fixture_dir: Path
) -> dict[str, object]:
    value = exact_dict(
        strict_json(path),
        {
            "files",
            "src_files",
            "selected_pins",
            "context_manifest_sha256",
            "requirements_blob",
            "src_tree",
            "archive_sha256",
            "commit",
            "origin",
            "native_bootstrap_sha256",
            "external_integrity_sha256",
        },
    )
    if any(
        type(value[k]) is not int or not 1 <= value[k] <= 100000
        for k in ("files", "src_files", "selected_pins")
    ):
        raise ValueError("source inventory counts invalid")
    for key in (
        "context_manifest_sha256",
        "archive_sha256",
        "native_bootstrap_sha256",
        "external_integrity_sha256",
    ):
        if not sha(value[key]):
            raise ValueError("source receipt digest invalid")
    if (
        value["commit"] != identity_data["revision"]
        or value["origin"] != identity_data["origin"]
        or value["requirements_blob"]
        != git(fixture_dir.parent.parent, "rev-parse", "HEAD:requirements.txt")
        or value["src_tree"] != git(fixture_dir.parent.parent, "rev-parse", "HEAD:src")
    ):
        raise ValueError("source receipt identity differs")
    if (
        digest(fixture_dir / "native.py") != value["native_bootstrap_sha256"]
        or digest(fixture_dir / "integrity.py") != value["external_integrity_sha256"]
    ):
        raise ValueError("external trusted bootstrap hashes differ")
    return value


def baseline_receipt(
    path: Path, source: dict[str, object], identity_data: dict[str, str]
) -> dict[str, object]:
    value = exact_dict(
        strict_json(path),
        {
            "status",
            "architecture",
            "base_manifest",
            "base_image_id",
            "image_id",
            "source",
            "commit",
            "origin",
            "archive_sha256",
            "frozen_input",
            "official_component_wheels",
            "baseline",
        },
    )
    expected_source = {
        key: source[key]
        for key in (
            "files",
            "src_files",
            "selected_pins",
            "context_manifest_sha256",
            "requirements_blob",
            "src_tree",
        )
    }
    if (
        value["status"] != "official_wheel_baseline_pass"
        or value["architecture"] != identity_data["architecture"]
        or value["base_manifest"] != BASE
        or value["commit"] != identity_data["revision"]
        or value["origin"] != identity_data["origin"]
        or value["archive_sha256"] != source["archive_sha256"]
        or value["source"] != expected_source
        or not isinstance(value["baseline"], dict)
        or value["baseline"].get("passed") is not True
        or value["baseline"].get("state") != "official-wheel-before-replacement"
        or value["baseline"].get("private_library_dirs_present")
        != ["pillow.libs", "psycopg2_binary.libs"]
        or not IMAGE.fullmatch(value["image_id"])
        or not IMAGE.fullmatch(value["base_image_id"])
    ):
        raise ValueError("baseline/source/image receipt differs")
    frozen = value["frozen_input"]
    wheels = value["official_component_wheels"]
    if (
        type(frozen) is not dict
        or not sha(frozen.get("input_manifest_sha256"))
        or type(wheels) is not dict
        or set(wheels) != {"pillow", "psycopg2-binary"}
        or any(type(w) is not dict or not sha(w.get("sha256")) for w in wheels.values())
    ):
        raise ValueError("official frozen input or wheel receipts invalid")
    return {
        "status": value["status"],
        "image_id": value["image_id"],
        "archive_sha256": value["archive_sha256"],
        "input_manifest_sha256": frozen["input_manifest_sha256"],
        "official_wheel_sha256": {
            name: wheels[name]["sha256"] for name in sorted(wheels)
        },
    }


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")


def publish_subset(source: Path, target: Path) -> None:
    if (
        not source.is_dir()
        or source.is_symlink()
        or target.exists()
        or target.is_symlink()
    ):
        raise ValueError("component subset publication path invalid")
    files = 0
    total = 0
    for path in source.rglob("*"):
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise ValueError("component subset contains unsafe entry")
        if stat.S_ISREG(mode):
            files += 1
            size = path.stat().st_size
            total += size
            if (
                size > MAX_PUBLIC_FILE
                or files > MAX_PUBLIC_FILES
                or total > MAX_PUBLIC_TOTAL
            ):
                raise ValueError("component subset budget exceeded")
    if files == 0:
        raise ValueError("component subset empty")
    source.rename(target)  # Both output parents were checked on the same device.


def execute(
    repo: Path, arch: str, revision: str, scratch: Path, tiff_zip: Path, dest: Path
) -> int:
    identity_data = identity(repo, arch, revision)
    owned_parent(scratch)
    owned_parent(dest)
    canonical_file(tiff_zip, MAX_ZIP)
    if scratch.parent.stat().st_dev != dest.parent.stat().st_dev:
        raise ValueError("private/public output roots must share a filesystem")
    if any(
        a == b or a in b.parents or b in a.parents
        for a, b in (
            (scratch, dest),
            (repo, scratch),
            (repo, dest),
            (tiff_zip, scratch),
            (tiff_zip, dest),
        )
    ):
        raise ValueError("input and output roots overlap")
    if digest(tiff_zip) != ZIP_SHA256[arch]:
        raise ValueError("accepted TIFF artifact digest differs")
    scratch.mkdir(mode=0o700)
    (scratch / "logs").mkdir(mode=0o700)
    dest.mkdir(mode=0o700)
    statuses: dict[str, dict[str, object]] = {
        name: dict(NOT_RUN)
        for name in (
            "fixture_test_prepare",
            "fixture_test_native",
            "source_export",
            "archive_control",
            "base_pull",
            "fixture_native",
            "tiff_extract",
            "tiff_verify",
            "pillow_build",
            "pillow_consumer",
            "pillow_export",
            "pillow_publish",
            "psyc_prepare",
            "psyc_build",
            "psyc_consumer",
            "psyc_export",
            "psyc_publish",
        )
    }
    fixture_public: dict[str, object] = {"status": "incomplete"}
    result = {
        "schema": 1,
        **identity_data,
        "component_scope": ["pillow", "psycopg2"],
        "full_runtime_cleared": False,
        "all_copy_cleared": False,
        "release_security_cleared": False,
        "advisory_cleared": False,
        "disposition": "incomplete",
        "stages": statuses,
        "pillow_consumer_diagnostic": {
            "schema": 1,
            "state": "not_run",
            "stage": None,
            "exit": None,
        },
    }
    write_json(dest / "summary.json", result)
    write_json(dest / "fixture.json", fixture_public)

    def step(name: str, command: list[str], timeout: int = 7200) -> bool:
        statuses[name] = run_stage(name, command, scratch, timeout=timeout)
        write_json(dest / "summary.json", result)
        return passed(statuses[name])

    try:
        fixture = repo / FIXTURE
        origin = identity_data["origin"]
        controls = [
            (
                "fixture_test_prepare",
                [
                    sys.executable,
                    str(fixture / "test-prepare.py"),
                    "--repo",
                    str(repo),
                    "--origin",
                    origin,
                    "--commit",
                    revision,
                ],
            ),
            ("fixture_test_native", [sys.executable, str(fixture / "test-native.py")]),
        ]
        for label, command in controls:
            if not step(label, command, 600):
                return 1
        output = scratch / "fixture-export"
        if not step(
            "source_export",
            [
                sys.executable,
                str(fixture / "prepare.py"),
                "--repo",
                str(repo),
                "--expected-ref",
                revision,
                "--expected-origin",
                origin,
                "--out",
                str(output),
            ],
            600,
        ):
            return 1
        source = prepare_receipt(
            scratch / "logs" / "source_export.log", identity_data, fixture
        )
        archive = output.with_suffix(".tar")
        canonical_file(archive, MAX_ZIP)
        if digest(archive) != source["archive_sha256"]:
            raise ValueError("exported archive differs from receipt")
        fixture_public = {
            "status": "source_authenticated",
            "archive_sha256": source["archive_sha256"],
            "context_manifest_sha256": source["context_manifest_sha256"],
            "requirements_blob": source["requirements_blob"],
            "src_tree": source["src_tree"],
            "files": source["files"],
            "src_files": source["src_files"],
            "selected_pins": source["selected_pins"],
        }
        write_json(dest / "fixture.json", fixture_public)
        if not step(
            "archive_control",
            [
                sys.executable,
                str(fixture / "test-integrity.py"),
                str(archive),
                "--archive-sha256",
                str(source["archive_sha256"]),
                "--origin",
                origin,
                "--commit",
                revision,
                "--requirements-blob",
                str(source["requirements_blob"]),
                "--src-tree",
                str(source["src_tree"]),
            ],
            600,
        ):
            return 1
        if not step(
            "base_pull", ["docker", "pull", "--platform", f"linux/{arch}", BASE], 1800
        ):
            return 1
        native_scratch = scratch / "fixture-native"
        if not step(
            "fixture_native",
            [
                sys.executable,
                str(fixture / "native.py"),
                "--scratch",
                str(native_scratch),
                "--archive",
                str(archive),
                "--archive-sha256",
                str(source["archive_sha256"]),
                "--origin",
                origin,
                "--commit",
                revision,
                "--requirements-blob",
                str(source["requirements_blob"]),
                "--src-tree",
                str(source["src_tree"]),
            ],
            5400,
        ):
            return 1
        baseline = baseline_receipt(
            native_scratch / "receipt.json", source, identity_data
        )
        if (
            probe(
                [
                    "docker",
                    "image",
                    "inspect",
                    "--format",
                    "{{.Id}}",
                    str(baseline["image_id"]),
                ]
            )
            != baseline["image_id"]
            or probe(
                [
                    "docker",
                    "image",
                    "inspect",
                    "--format",
                    "{{.Architecture}}",
                    str(baseline["image_id"]),
                ]
            )
            != arch
        ):
            raise ValueError("baseline image identity or architecture differs")
        fixture_public = {**fixture_public, **baseline}
        write_json(dest / "fixture.json", fixture_public)
        tiff_dir = scratch / "accepted-tiff"
        try:
            extract_tiff(tiff_zip, tiff_dir, arch)
            statuses["tiff_extract"] = {"exit": 0, "state": "passed", "reason": "none"}
        except (OSError, ValueError, zipfile.BadZipFile):
            statuses["tiff_extract"] = {
                "exit": None,
                "state": "unavailable",
                "reason": "invalid_artifact",
            }
            return 1
        if not step(
            "tiff_verify",
            [
                sys.executable,
                str(repo / PILLOW / "verify-tiff-input.py"),
                str(tiff_dir),
                arch,
            ],
            600,
        ):
            return 1
        pillow_out = scratch / "pillow-build"
        pillow_out.mkdir(mode=0o700)
        if step(
            "pillow_build",
            ["bash", str(repo / PILLOW / "build.sh"), str(pillow_out), str(tiff_dir)],
        ):
            consumer_passed = step(
                "pillow_consumer",
                [
                    "bash",
                    str(repo / PILLOW / "test-installed.sh"),
                    str(pillow_out),
                    str(baseline["image_id"]),
                ],
            )
            diagnostic = pillow_consumer_diagnostic(
                pillow_out, statuses["pillow_consumer"]
            )
            result["pillow_consumer_diagnostic"] = diagnostic
            write_json(dest / "summary.json", result)
            if consumer_passed and diagnostic["state"] == "reported":
                pillow_candidate = scratch / "pillow-public-candidate"
                if step(
                    "pillow_export",
                    [
                        sys.executable,
                        str(repo / PILLOW / "ci-evidence.py"),
                        str(pillow_out),
                        str(pillow_candidate),
                        "--repo",
                        str(repo),
                        "--revision",
                        revision,
                        "--tree",
                        identity_data["tree"],
                        "--arch",
                        arch,
                    ],
                    900,
                ):
                    try:
                        publish_subset(pillow_candidate, dest / "pillow")
                        statuses["pillow_publish"] = {
                            "exit": 0,
                            "state": "passed",
                            "reason": "none",
                        }
                    except (OSError, ValueError):
                        statuses["pillow_publish"] = {
                            "exit": None,
                            "state": "unavailable",
                            "reason": "publication_error",
                        }
        psyc_out = scratch / "psyc-build"
        if step(
            "psyc_prepare",
            ["bash", str(repo / PSYC / "prepare-native.sh"), str(psyc_out)],
        ):
            if step(
                "psyc_build",
                ["bash", str(repo / PSYC / "accept-native.sh"), str(psyc_out)],
            ):
                step(
                    "psyc_consumer",
                    [
                        "bash",
                        str(repo / PSYC / "run-synthetic-db.sh"),
                        str(psyc_out),
                        str(baseline["image_id"]),
                    ],
                )
            if (
                statuses["psyc_build"]["exit"] is not None
                and statuses["psyc_consumer"]["exit"] is not None
            ):
                psyc_candidate = scratch / "psyc-public-candidate"
                if step(
                    "psyc_export",
                    [
                        sys.executable,
                        str(repo / PSYC / "ci-evidence.py"),
                        "--source",
                        str(psyc_out),
                        "--dest",
                        str(psyc_candidate),
                        "--repo",
                        str(repo),
                        "--arch",
                        arch,
                        "--revision",
                        revision,
                        "--tree",
                        identity_data["tree"],
                        "--build-exit",
                        str(statuses["psyc_build"]["exit"]),
                        "--consumer-exit",
                        str(statuses["psyc_consumer"]["exit"]),
                    ],
                    900,
                ):
                    try:
                        publish_subset(psyc_candidate, dest / "psycopg2")
                        statuses["psyc_publish"] = {
                            "exit": 0,
                            "state": "passed",
                            "reason": "none",
                        }
                    except (OSError, ValueError):
                        statuses["psyc_publish"] = {
                            "exit": None,
                            "state": "unavailable",
                            "reason": "publication_error",
                        }
        required = (
            "fixture_test_prepare",
            "fixture_test_native",
            "source_export",
            "archive_control",
            "base_pull",
            "fixture_native",
            "tiff_extract",
            "tiff_verify",
            "pillow_build",
            "pillow_consumer",
            "pillow_export",
            "pillow_publish",
            "psyc_prepare",
            "psyc_build",
            "psyc_consumer",
            "psyc_export",
            "psyc_publish",
        )
        if all(passed(statuses[name]) for name in required):
            result["disposition"] = "component_evidence_complete"
            return 0
        return 1
    except (
        ValueError,
        TypeError,
        KeyError,
        OSError,
        subprocess.SubprocessError,
        json.JSONDecodeError,
    ):
        result["disposition"] = "refused"
        return 1
    finally:
        write_json(dest / "summary.json", result)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--arch", choices=sorted(ZIP_SHA256), required=True)
    parser.add_argument("--expected-ref", required=True)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--tiff-zip", type=Path, required=True)
    parser.add_argument("--dest", type=Path, required=True)
    args = parser.parse_args()
    try:
        return execute(
            args.repo,
            args.arch,
            args.expected_ref,
            args.scratch,
            args.tiff_zip,
            args.dest,
        )
    except (ValueError, OSError, subprocess.SubprocessError, zipfile.BadZipFile):
        print(
            "native Python component evidence refused; see private scratch and bounded status",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
