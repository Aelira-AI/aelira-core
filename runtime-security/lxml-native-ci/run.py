#!/usr/bin/env python3
"""Run the reviewed lxml recipe on a native host and publish bounded evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import stat
import subprocess
import sys
from pathlib import Path, PurePosixPath

ORIGIN = "https://github.com/Aelira-AI/aelira-core.git"
ORIGINS = {ORIGIN, "https://github.com/Aelira-AI/aelira-core"}
RECIPE = Path("runtime-security/lxml")
RUNNER = Path("runtime-security/lxml-native-ci")
RECIPE_FILES = frozenset(
    "Dockerfile.verifier README.md accept-native.sh build-offline-container.sh "
    "compatibility.py consumer-container.sh consumer.py fetch-native.sh "
    "fetch-consumer-wheels.py input-gate.py inputs.sha256 prepare-native.sh "
    "preflight-container.sh run-consumer.sh verify-consumer-wheels.py "
    "verify-apt-closure.py verify-frozen.py verify-installed.py".split()
)
RUNNER_FILES = frozenset(
    "README.md run.py test-run.py evidence.py test-evidence.py evidence-readme.md".split()
)
SOURCE_FILES = frozenset(
    "requirements.txt src/education/docx_processor.py "
    "src/education/pptx_processor.py src/education/xlsx_processor.py "
    "src/education/remediation/pptx_reading_order.py".split()
)
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
NOT_RUN = {"exit_code": 125, "state": "not_run", "reason": "dependency_not_passed"}
STAGES = (
    "prepare-native",
    "frozen-check",
    "accept-native",
    "run-consumer",
    "evidence-export",
    "evidence-check",
    "evidence-publish",
)
MAX_PUBLIC_FILES = 230
MAX_PUBLIC_FILE = 80 * 1024 * 1024
MAX_PUBLIC_TOTAL = 450 * 1024 * 1024
RECEIPTS = frozenset(
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
        "apt-selections.json",
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


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def regular(path: Path, limit: int) -> None:
    item = path.lstat()
    if (
        not stat.S_ISREG(item.st_mode)
        or item.st_nlink != 1
        or not 0 < item.st_size <= limit
    ):
        raise ValueError("required file is absent, unsafe or out of bounds")


def strict_json(path: Path, limit: int = 2 * 1024 * 1024) -> object:
    regular(path, limit)

    def pairs(rows: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for name, value in rows:
            if name in result:
                raise ValueError("duplicate JSON key")
            result[name] = value
        return result

    def reject(_: str) -> object:
        raise ValueError("nonstandard JSON value")

    return json.loads(
        path.read_text(encoding="utf-8"), object_pairs_hook=pairs, parse_constant=reject
    )


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


def command(args: list[str], *, timeout: int = 30, cwd: Path | None = None) -> str:
    result = subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=True
    )
    return result.stdout.strip()


def git(repo: Path, *args: str) -> str:
    return command(["/usr/bin/git", "-C", str(repo), *args])


def new_output(path: Path) -> None:
    if (
        not path.is_absolute()
        or path.exists()
        or path.is_symlink()
        or path.resolve() != path
    ):
        raise ValueError("output must be new canonical absolute path")
    parent = path.parent
    if (
        parent.resolve(strict=True) != parent
        or not parent.is_dir()
        or parent.stat().st_uid != os.getuid()
    ):
        raise ValueError("output parent must be canonical and owned")


def outputs(repo: Path, scratch: Path, dest: Path) -> None:
    if not repo.is_absolute() or repo.resolve(strict=True) != repo:
        raise ValueError("repository must be canonical absolute path")
    new_output(scratch)
    new_output(dest)
    for a, b in ((repo, scratch), (repo, dest), (scratch, dest)):
        if a == b or a in b.parents or b in a.parents:
            raise ValueError("repository and output roots overlap")
    if scratch.parent.stat().st_dev != dest.parent.stat().st_dev:
        raise ValueError("private and public output roots must share a filesystem")


def verify_committed_file(repo: Path, name: Path) -> None:
    path = repo / name
    regular(path, 4 * 1024 * 1024)
    if (
        git(repo, "ls-files", "--error-unmatch", "--", name.as_posix())
        != name.as_posix()
    ):
        raise ValueError("required source is not tracked")
    if git(repo, "hash-object", "--", str(path)) != git(
        repo, "rev-parse", f"HEAD:{name.as_posix()}"
    ):
        raise ValueError("required source bytes differ from Git")


def recipe_digest(repo: Path) -> str:
    # Match the reviewed recipe's recipe-id grammar using independently checked Git bytes.
    rows = [
        f"{digest(repo / RECIPE / name)}  {name}\n" for name in sorted(RECIPE_FILES)
    ]
    return hashlib.sha256("".join(rows).encode("ascii")).hexdigest()


def identity(repo: Path, arch: str, commit: str, tree: str) -> dict[str, str]:
    if (
        arch not in {"amd64", "arm64"}
        or not HEX40.fullmatch(commit)
        or not HEX40.fullmatch(tree)
    ):
        raise ValueError("invalid architecture or externally selected Git identities")
    if git(repo, "rev-parse", "--show-toplevel") != str(repo):
        raise ValueError("repository root differs")
    observed = git(repo, "remote", "get-url", "origin")
    if observed not in ORIGINS:
        raise ValueError("public origin differs")
    if (
        git(repo, "rev-parse", "HEAD") != commit
        or git(repo, "rev-parse", "HEAD^{tree}") != tree
    ):
        raise ValueError("Git HEAD or tree differs")
    if git(repo, "status", "--porcelain=v1", "--untracked-files=all"):
        raise ValueError("public checkout is dirty")
    recipe_names = set(git(repo, "ls-files", "--", RECIPE.as_posix()).splitlines())
    runner_names = set(git(repo, "ls-files", "--", RUNNER.as_posix()).splitlines())
    if recipe_names != {f"{RECIPE}/{name}" for name in RECIPE_FILES}:
        raise ValueError("committed recipe file set differs")
    if runner_names != {f"{RUNNER}/{name}" for name in RUNNER_FILES}:
        raise ValueError("committed runner file set differs")
    selected_files = {Path(item) for item in SOURCE_FILES}
    selected_files.update(RECIPE / item for item in RECIPE_FILES)
    selected_files.update(RUNNER / item for item in RUNNER_FILES)
    for name in sorted(selected_files):
        verify_committed_file(repo, name)
    return {
        "commit": commit,
        "tree": tree,
        "observed_origin": observed,
        "recipe_sha256": recipe_digest(repo),
    }


def native_preflight(arch: str) -> None:
    if os.getuid() == 0 or platform.system() != "Linux":
        raise ValueError("nonroot native Linux host required")
    machine = platform.machine()
    aliases = {"amd64": {"x86_64", "amd64"}, "arm64": {"aarch64", "arm64"}}
    if machine not in aliases[arch]:
        raise ValueError("host architecture differs")
    daemon = command(["docker", "info", "--format", "{{.Architecture}}"])
    if daemon not in aliases[arch]:
        raise ValueError("Docker daemon architecture differs")


def normalize_origin(repo: Path, observed: str) -> str:
    if observed == ORIGIN:
        return "already_canonical"
    if observed != "https://github.com/Aelira-AI/aelira-core":
        raise ValueError("untrusted origin")
    subprocess.run(
        ["/usr/bin/git", "-C", str(repo), "remote", "set-url", "origin", ORIGIN],
        check=True,
        capture_output=True,
    )
    if git(repo, "remote", "get-url", "origin") != ORIGIN:
        raise ValueError("origin normalization failed")
    return "normalized_trusted_https"


def stage(
    name: str, args: list[str], scratch: Path, timeout: int
) -> tuple[dict[str, object], dict[str, object]]:
    log = scratch / "logs" / f"{name}.log"
    with log.open("xb") as output:
        try:
            result = subprocess.run(
                args,
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                check=False,
            )
            code = result.returncode
            if code == 0:
                public = {"exit_code": 0, "state": "passed", "reason": "none"}
            elif code > 0:
                public = {
                    "exit_code": code,
                    "state": "failed",
                    "reason": "process_nonzero",
                }
            else:
                public = {
                    "exit_code": None,
                    "state": "unavailable",
                    "reason": "terminated_by_signal",
                }
        except FileNotFoundError:
            public = {
                "exit_code": None,
                "state": "unavailable",
                "reason": "command_missing",
            }
        except subprocess.TimeoutExpired:
            public = {"exit_code": None, "state": "unavailable", "reason": "timeout"}
        except OSError:
            public = {
                "exit_code": None,
                "state": "unavailable",
                "reason": "process_error",
            }
    log_hash = digest(log)
    private = {
        "exit_code": public["exit_code"],
        "log_path": str(log),
        "log_sha256": log_hash,
    }
    public["log_sha256"] = log_hash
    return public, private


def passed(value: dict[str, object]) -> bool:
    return (
        value["state"] == "passed"
        and type(value["exit_code"]) is int
        and value["exit_code"] == 0
    )


def frozen_sha(scratch: Path) -> str:
    path = scratch / "native" / "frozen" / "frozen.sha256"
    regular(path, 2 * 1024 * 1024)
    return digest(path)


def wheel_sha(scratch: Path, arch: str) -> str:
    native = scratch / "native"
    machine = "x86_64" if arch == "amd64" else "aarch64"
    expected_name = re.compile(rf"lxml-6\.1\.3-cp314-cp314-linux_{machine}\.whl\Z")
    wheels: list[Path] = []
    for build in ("build-a", "build-b"):
        directory = native / build
        files = list(directory.glob("lxml-6.1.3-*.whl"))
        if len(files) != 1 or not expected_name.fullmatch(files[0].name):
            raise ValueError("native wheel filename differs")
        wheel = files[0]
        regular(wheel, MAX_PUBLIC_FILE)
        record = directory / "wheel.sha256"
        regular(record, 512)
        if record.read_bytes() != f"{digest(wheel)}  /out/{wheel.name}\n".encode(
            "ascii"
        ):
            raise ValueError("original wheel hash record differs")
        wheels.append(wheel)
    if wheels[0].name != wheels[1].name or wheels[0].samefile(wheels[1]):
        raise ValueError("wheel builds are not distinct")
    if wheels[0].stat().st_size != wheels[1].stat().st_size or digest(
        wheels[0]
    ) != digest(wheels[1]):
        raise ValueError("native wheels differ")
    return digest(wheels[0])


def safe_public_bundle(
    bundle: Path,
    repo: Path,
    expected: dict[str, str],
    original: dict[str, object],
) -> dict[str, object]:
    if bundle.is_symlink() or not bundle.is_dir():
        raise ValueError("public evidence directory absent or unsafe")
    # The exporter has already checked Git. Repeat that binding immediately
    # before publication, including the runner files that selected this export.
    selected = identity(
        repo, expected["architecture"], expected["commit"], expected["tree"]
    )
    if selected["recipe_sha256"] != expected["recipe_sha256"]:
        raise ValueError("committed recipe changed before publication")
    manifest = strict_json(bundle / "bundle.json")
    if type(manifest) is not dict or set(manifest) != {
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
    }:
        raise ValueError("bounded bundle schema differs")
    if (
        type(manifest["schema"]) is not int
        or manifest["schema"] != 1
        or type(manifest["scope"]) is not str
        or manifest["scope"] != "public-commit"
        or type(manifest["origin"]) is not str
        or manifest["origin"] != ORIGIN
    ):
        raise ValueError("bounded bundle source scope differs")
    for field, value in expected.items():
        if manifest.get(field) != value:
            raise ValueError("bounded bundle identity differs")
    files = manifest["files"]
    if type(files) is not dict or not 1 <= len(files) <= MAX_PUBLIC_FILES:
        raise ValueError("bounded bundle inventory size differs")
    frozen_manifest = bundle / "frozen" / "frozen.sha256"
    regular(frozen_manifest, 2 * 1024 * 1024)
    if digest(frozen_manifest) != expected["frozen_sha256"]:
        raise ValueError("bounded frozen manifest differs")
    listed: dict[str, str] = {}
    for line in frozen_manifest.read_text(encoding="ascii").splitlines(keepends=True):
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9._/+%~-]+)\n", line)
        if match is None:
            raise ValueError("frozen manifest grammar differs")
        name = match.group(2)
        if name in listed:
            raise ValueError("duplicate frozen manifest path")
        listed[name] = match.group(1)
    if (
        not listed
        or list(listed) != sorted(listed)
        or "verifier/image.tar" not in listed
    ):
        raise ValueError("frozen manifest inventory differs")
    machine = "x86_64" if expected["architecture"] == "amd64" else "aarch64"
    wheels = {
        f"wheels/{area}/lxml-6.1.3-cp314-cp314-linux_{machine}.whl"
        for area in ("build-a", "build-b")
    }
    approved_files = (
        {f"frozen/{name}" for name in listed if name != "verifier/image.tar"}
        | {"frozen/frozen.sha256"}
        | {f"receipts/{name}" for name in RECEIPTS}
        | wheels
    )
    if set(files) != approved_files:
        raise ValueError("bounded evidence whitelist differs")
    commands = manifest["original_commands"]
    if type(commands) is not dict or set(commands) != {
        "prepare-native",
        "accept-native",
        "run-consumer",
    }:
        raise ValueError("bounded bundle original status differs")
    for row in commands.values():
        if (
            type(row) is not dict
            or set(row) != {"exit_code", "log_sha256"}
            or type(row["exit_code"]) is not int
            or row["exit_code"] != 0
            or not isinstance(row["log_sha256"], str)
            or not HEX64.fullmatch(row["log_sha256"])
        ):
            raise ValueError("bounded bundle original exit differs")
    if commands != {
        name: {"exit_code": row["exit_code"], "log_sha256": row["log_sha256"]}
        for name, row in original.items()
    }:
        raise ValueError("bounded bundle original log hashes differ")
    if manifest["excluded"] != ["verifier/image.tar", "raw logs", "installer-venv.tar"]:
        raise ValueError("bounded bundle exclusion list differs")
    actual: set[str] = set()
    total = 0
    for path in bundle.rglob("*"):
        relative = path.relative_to(bundle).as_posix()
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise ValueError("symlink in public bundle")
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode) or path.stat().st_nlink != 1:
            raise ValueError("unsafe public bundle entry")
        actual.add(relative)
    if actual != set(files) | {"bundle.json"}:
        raise ValueError("public evidence file set differs")
    for name, row in files.items():
        if (
            type(name) is not str
            or not name.isascii()
            or name.startswith("/")
            or "\\" in name
            or "//" in name
            or str(PurePosixPath(name)) != name
            or any(part in {"", ".", ".."} for part in PurePosixPath(name).parts)
        ):
            raise ValueError("unsafe evidence path")
        if not name.startswith(("frozen/", "receipts/", "wheels/")) or name.endswith(
            ("verifier/image.tar", "installer-venv.tar", ".log")
        ):
            raise ValueError("unapproved evidence file")
        if (
            type(row) is not dict
            or set(row) != {"sha256", "size"}
            or type(row["size"]) is not int
            or not 0 < row["size"] <= MAX_PUBLIC_FILE
            or type(row["sha256"]) is not str
            or not HEX64.fullmatch(row["sha256"])
        ):
            raise ValueError("invalid evidence file metadata")
        path = bundle / name
        regular(path, MAX_PUBLIC_FILE)
        if path.stat().st_size != row["size"] or digest(path) != row["sha256"]:
            raise ValueError("bounded evidence hash differs")
        total += row["size"]
    if total > MAX_PUBLIC_TOTAL:
        raise ValueError("bounded public bytes exceeded")
    for name, sha256 in listed.items():
        if name != "verifier/image.tar" and files[f"frozen/{name}"]["sha256"] != sha256:
            raise ValueError("frozen manifest and bundle hash differ")
    for name in RECIPE_FILES:
        if digest(bundle / "frozen" / "recipe" / name) != digest(repo / RECIPE / name):
            raise ValueError("frozen recipe differs from committed source")
    for name in SOURCE_FILES:
        if digest(bundle / "frozen" / "source" / name) != digest(repo / name):
            raise ValueError("frozen application source differs from committed source")
    if (
        (bundle / "frozen" / "architecture.txt").read_bytes()
        != (expected["architecture"] + "\n").encode("ascii")
        or (bundle / "frozen" / "source-commit.txt").read_bytes()
        != (expected["commit"] + "\n").encode("ascii")
        or (bundle / "frozen" / "source-tree.txt").read_bytes()
        != (expected["tree"] + "\n").encode("ascii")
    ):
        raise ValueError("frozen source labels differ")
    return {
        "files": len(files),
        "bytes": total,
        "bundle_sha256": digest(bundle / "bundle.json"),
    }


def execute(
    repo: Path, arch: str, commit: str, tree: str, scratch: Path, dest: Path
) -> int:
    outputs(repo, scratch, dest)
    scratch.mkdir(mode=0o700)
    (scratch / "logs").mkdir(mode=0o700)
    dest.mkdir(mode=0o700)
    statuses: dict[str, dict[str, object]] = {name: dict(NOT_RUN) for name in STAGES}
    summary: dict[str, object] = {
        "schema": 1,
        "scope": "lxml_component_native_ci",
        "source_bound": False,
        "disposition": "incomplete",
        "architecture": arch if arch in {"amd64", "arm64"} else None,
        "origin_normalization": "not_run",
        "full_runtime_cleared": False,
        "all_copy_cleared": False,
        "release_security_cleared": False,
        "advisory_cleared": False,
        "stages": statuses,
    }
    write_json(dest / "summary.json", summary)
    private_status: dict[str, object] = {"schema": 1, "commands": {}}

    def run(name: str, argv: list[str], timeout: int) -> bool:
        public, private = stage(name, argv, scratch, timeout)
        statuses[name] = public
        if name in {"prepare-native", "accept-native", "run-consumer"}:
            private_status["commands"][name] = private
            write_json(scratch / "original-status.json", private_status)
        write_json(dest / "summary.json", summary)
        return passed(public)

    try:
        selected = identity(repo, arch, commit, tree)
        native_preflight(arch)
        normalization = normalize_origin(repo, selected["observed_origin"])
        if (
            git(repo, "rev-parse", "HEAD") != commit
            or git(repo, "rev-parse", "HEAD^{tree}") != tree
            or git(repo, "status", "--porcelain=v1", "--untracked-files=all")
        ):
            raise ValueError("checkout changed after origin normalization")
        summary.update(
            {
                "source_bound": True,
                "commit": commit,
                "tree": tree,
                "origin": ORIGIN,
                "recipe_sha256": selected["recipe_sha256"],
                "origin_normalization": normalization,
            }
        )
        write_json(dest / "summary.json", summary)
        native = scratch / "native"
        args = [str(native), str(repo), commit, tree, selected["recipe_sha256"]]
        recipe = repo / RECIPE
        if not run(
            "prepare-native", ["bash", str(recipe / "prepare-native.sh"), *args], 7200
        ):
            return 1
        frozen = frozen_sha(scratch)
        # A successful shell exit is insufficient: recheck the exact frozen bytes
        # through the committed recipe before giving their hash to acceptance.
        if not run(
            "frozen-check",
            [
                sys.executable,
                "-I",
                str(recipe / "input-gate.py"),
                "check",
                str(native / "frozen"),
                str(recipe),
                str(repo),
                commit,
                tree,
                selected["recipe_sha256"],
                frozen,
            ],
            900,
        ):
            return 1
        summary["frozen_sha256"] = frozen
        write_json(dest / "summary.json", summary)
        if not run(
            "accept-native",
            ["bash", str(recipe / "accept-native.sh"), *args, frozen],
            7200,
        ):
            return 1
        wheel = wheel_sha(scratch, arch)
        summary["wheel_sha256"] = wheel
        write_json(dest / "summary.json", summary)
        if not run(
            "run-consumer",
            ["bash", str(recipe / "run-consumer.sh"), *args, frozen, wheel],
            3600,
        ):
            return 1
        exporter = repo / RUNNER / "evidence.py"
        candidate = scratch / "public-candidate"
        export_args = [
            sys.executable,
            "-I",
            str(exporter),
            "export",
            "--scratch",
            str(native),
            "--repo",
            str(repo),
            "--recipe",
            str(recipe),
            "--status",
            str(scratch / "original-status.json"),
            "--out",
            str(candidate),
            "--scope",
            "public-commit",
            "--commit",
            commit,
            "--tree",
            tree,
            "--recipe-sha256",
            selected["recipe_sha256"],
            "--frozen-sha256",
            frozen,
            "--wheel-sha256",
            wheel,
        ]
        if not run("evidence-export", export_args, 900):
            return 1
        check_args = [
            sys.executable,
            "-I",
            str(exporter),
            "check",
            "--bundle",
            str(candidate),
            "--repo",
            str(repo),
            "--commit",
            commit,
            "--tree",
            tree,
            "--recipe-sha256",
            selected["recipe_sha256"],
            "--frozen-sha256",
            frozen,
            "--wheel-sha256",
            wheel,
        ]
        if not run("evidence-check", check_args, 900):
            return 1
        try:
            bound = safe_public_bundle(
                candidate,
                repo,
                {
                    "commit": commit,
                    "tree": tree,
                    "recipe_sha256": selected["recipe_sha256"],
                    "frozen_sha256": frozen,
                    "wheel_sha256": wheel,
                    "architecture": arch,
                },
                private_status["commands"],
            )
        except (OSError, ValueError, TypeError, KeyError, UnicodeError):
            statuses["evidence-publish"] = {
                "exit_code": None,
                "state": "unavailable",
                "reason": "validation_error",
            }
            summary["disposition"] = "refused"
            return 1
        try:
            candidate.rename(dest / "evidence")
        except OSError:
            statuses["evidence-publish"] = {
                "exit_code": None,
                "state": "unavailable",
                "reason": "publication_error",
            }
            summary["disposition"] = "refused"
            return 1
        statuses["evidence-publish"] = {
            "exit_code": 0,
            "state": "passed",
            "reason": "none",
        }
        summary["bundle"] = bound
        summary["disposition"] = "component_evidence_complete"
        return 0
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        subprocess.SubprocessError,
        json.JSONDecodeError,
        UnicodeError,
    ):
        summary["disposition"] = "refused"
        return 1
    finally:
        write_json(dest / "summary.json", summary)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--expected-ref", required=True)
    parser.add_argument("--expected-tree", required=True)
    parser.add_argument("--arch", choices=("amd64", "arm64"), required=True)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--dest", type=Path, required=True)
    args = parser.parse_args()
    try:
        return execute(
            args.repo,
            args.arch,
            args.expected_ref,
            args.expected_tree,
            args.scratch,
            args.dest,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        print(
            "lxml native CI refused; inspect private scratch and bounded status",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
