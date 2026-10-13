#!/usr/bin/env python3
"""Build and inspect a disposable probe from two equal offline source builds.

The base is an operator-supplied immutable local Docker image ID. This harness
does not authenticate the base's package dependencies or make a release claim.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

from package_source import (
    ARCHIVE_SHA256,
    BINDING,
    COMMIT,
    FILE_COUNT,
    LICENSE_SHA256,
    MANIFEST_SHA256,
    ORIGIN,
    no_alias,
    PATCHED_SHA256,
    PATCH_SHA256,
    TOOLS,
    TREE,
    GENERATED,
    MAKEFILE_SHA256,
    TEMPLATES,
)
from verify_install import unique_object


def refuse(reason: str) -> None:
    raise ValueError(reason)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run(
    args: list[str],
    *,
    timeout: int = 240,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        args,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
        env=env,
    )


def read_package(path: Path) -> tuple[bytes, bytes, dict, bytes, bytes, dict]:
    no_alias(path)
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        refuse("package must be an absolute non-symlink directory")
    names = {p.name for p in path.iterdir()}
    if names != {
        "source-manifest.json",
        "latexml-source.tar",
        "runtime-manifest.json",
        "latexml-runtime.tar",
        "build-receipt.json",
    }:
        refuse("package member set mismatch")
    for name in names:
        p = path / name
        if p.is_symlink() or not p.is_file():
            refuse("package member is aliased or not a file")
    if (
        (path / "source-manifest.json").stat().st_size > 300_000
        or (path / "latexml-source.tar").stat().st_size > 200_000_000
        or (path / "runtime-manifest.json").stat().st_size > 20_000
        or (path / "latexml-runtime.tar").stat().st_size > 200_000_000
        or (path / "build-receipt.json").stat().st_size > 10_000
    ):
        refuse("package exceeds size bound")
    manifest_raw = (path / "source-manifest.json").read_bytes()
    archive_raw = (path / "latexml-source.tar").read_bytes()
    runtime_raw = (path / "runtime-manifest.json").read_bytes()
    runtime_archive_raw = (path / "latexml-runtime.tar").read_bytes()
    if sha(manifest_raw) != MANIFEST_SHA256 or sha(archive_raw) != ARCHIVE_SHA256:
        refuse("package differs from verified Git-derived source identity")
    manifest = json.loads(manifest_raw, object_pairs_hook=unique_object)
    receipt = json.loads(
        (path / "build-receipt.json").read_bytes(), object_pairs_hook=unique_object
    )
    if type(manifest) is not dict or set(manifest) != {
        "schema",
        "upstream_origin",
        "upstream_commit",
        "upstream_tree",
        "source_epoch",
        "adapter_sha256",
        "original_binding_sha256",
        "patched_binding_sha256",
        "license_sha256",
        "files",
        "commands",
    }:
        refuse("manifest schema mismatch")
    fixed = {
        "schema": "aelira-latexml-source-v1",
        "upstream_origin": ORIGIN,
        "upstream_commit": COMMIT,
        "upstream_tree": TREE,
        "adapter_sha256": PATCH_SHA256,
        "patched_binding_sha256": PATCHED_SHA256,
        "license_sha256": LICENSE_SHA256,
    }
    if any(manifest[key] != value for key, value in fixed.items()):
        refuse("source identity mismatch")
    files = manifest["files"]
    epoch = manifest["source_epoch"]
    if (
        type(files) is not dict
        or len(files) != FILE_COUNT
        or type(epoch) is not int
        or epoch <= 0
    ):
        refuse("inventory count or epoch mismatch")
    if type(manifest["commands"]) is not dict or manifest["commands"] != {
        tool: "/opt/latexml-source/bin/" + tool for tool in TOOLS
    }:
        refuse("tool path manifest mismatch")
    if (
        files[BINDING]["sha256"] != PATCHED_SHA256
        or files["LICENSE"]["sha256"] != LICENSE_SHA256
    ):
        refuse("binding or license manifest mismatch")
    if (
        type(receipt) is not dict
        or set(receipt)
        != {
            "schema",
            "manifest_sha256",
            "archive_sha256",
            "archive_size",
            "file_count",
            "upstream_commit",
            "upstream_tree",
            "runtime_manifest_sha256",
            "runtime_archive_sha256",
            "runtime_archive_size",
            "runtime_file_count",
        }
        or receipt["schema"] != "aelira-latexml-package-build-v2"
    ):
        refuse("build receipt schema mismatch")
    if receipt.get("manifest_sha256") != sha(manifest_raw) or receipt.get(
        "archive_sha256"
    ) != sha(archive_raw):
        refuse("build receipt hash mismatch")
    if (
        type(receipt["archive_size"]) is not int
        or type(receipt["file_count"]) is not int
        or receipt["archive_size"] != len(archive_raw)
        or receipt["file_count"] != FILE_COUNT
    ):
        refuse("build receipt size/count mismatch")
    if receipt.get("upstream_commit") != COMMIT or receipt.get("upstream_tree") != TREE:
        refuse("build receipt source identity mismatch")
    runtime = json.loads(runtime_raw, object_pairs_hook=unique_object)
    if type(runtime) is not dict or set(runtime) != {
        "schema",
        "upstream_origin",
        "upstream_commit",
        "upstream_tree",
        "makefile_sha256",
        "source_manifest_sha256",
        "source_archive_sha256",
        "revision",
        "templates",
        "generator",
        "generated",
        "source_file_count",
        "runtime_file_count",
    }:
        refuse("runtime manifest schema mismatch")
    fixed_runtime = {
        "schema": "aelira-latexml-runtime-v2",
        "upstream_origin": ORIGIN,
        "upstream_commit": COMMIT,
        "upstream_tree": TREE,
        "makefile_sha256": MAKEFILE_SHA256,
        "source_manifest_sha256": MANIFEST_SHA256,
        "source_archive_sha256": ARCHIVE_SHA256,
        "revision": COMMIT[:7],
        "templates": TEMPLATES,
        "source_file_count": FILE_COUNT,
        "runtime_file_count": FILE_COUNT + len(GENERATED),
    }
    if (
        any(runtime.get(k) != v for k, v in fixed_runtime.items())
        or type(runtime["source_file_count"]) is not int
        or type(runtime["runtime_file_count"]) is not int
    ):
        refuse("runtime source identity mismatch")
    generated = runtime["generated"]
    if type(generated) is not dict or set(generated) != set(GENERATED):
        refuse("generated membership mismatch")
    for name, expected_hash in GENERATED.items():
        meta = generated[name]
        if (
            type(meta) is not dict
            or set(meta) != {"sha256", "size", "mode"}
            or meta["sha256"] != expected_hash
            or type(meta["size"]) is not int
            or meta["size"] <= 0
            or type(meta["mode"]) is not int
            or meta["mode"] != 0o644
        ):
            refuse("generated metadata mismatch")
    generator = runtime["generator"]
    if (
        type(generator) is not dict
        or set(generator)
        != {"image_id", "architecture", "perl_sha256", "recdescent_sha256"}
        or type(generator["image_id"]) is not str
        or len(generator["image_id"]) != 71
        or not generator["image_id"].startswith("sha256:")
        or any(c not in "0123456789abcdef" for c in generator["image_id"][7:])
        or generator["architecture"] not in ("arm64", "amd64")
        or any(
            type(generator[k]) is not str
            or len(generator[k]) != 64
            or any(c not in "0123456789abcdef" for c in generator[k])
            for k in ("perl_sha256", "recdescent_sha256")
        )
    ):
        refuse("generator identity malformed")
    if (
        type(receipt["runtime_archive_size"]) is not int
        or type(receipt["runtime_file_count"]) is not int
        or receipt["runtime_manifest_sha256"] != sha(runtime_raw)
        or receipt["runtime_archive_sha256"] != sha(runtime_archive_raw)
        or receipt["runtime_archive_size"] != len(runtime_archive_raw)
        or receipt["runtime_file_count"] != FILE_COUNT + len(GENERATED)
    ):
        refuse("runtime receipt mismatch")
    return (
        manifest_raw,
        archive_raw,
        manifest,
        runtime_raw,
        runtime_archive_raw,
        runtime,
    )


def extract_verified(
    archive_path: Path, target: Path, manifest: dict, runtime: dict | None = None
) -> None:
    files = (
        manifest["files"]
        if runtime is None
        else {**manifest["files"], **runtime["generated"]}
    )
    seen: set[str] = set()
    with tarfile.open(archive_path, "r:") as tar:
        for member in tar:
            name = member.name
            if name in seen or name not in files or not member.isfile():
                refuse("archive has unsafe, extra or duplicate member")
            if (
                member.uid != 0
                or member.gid != 0
                or member.mtime != manifest["source_epoch"]
            ):
                refuse("archive source metadata mismatch")
            meta = files[name]
            if (
                type(meta) is not dict
                or set(meta) != {"sha256", "size", "mode"}
                or type(meta["size"]) is not int
                or type(meta["mode"]) is not int
                or type(meta["sha256"]) is not str
                or not 0 <= meta["size"] <= 50_000_000
                or meta["mode"] not in (0o644, 0o755)
                or member.size != meta["size"]
                or member.mode != meta["mode"]
            ):
                refuse("archive file metadata mismatch")
            if any(part in ("", ".", "..") or "\\" in part for part in name.split("/")):
                refuse("unsafe archive path")
            if name != "LICENSE" and not name.startswith(("lib/", "bin/")):
                refuse("archive path outside runtime tree")
            data = tar.extractfile(member).read()
            if sha(data) != meta["sha256"]:
                refuse("archive file content mismatch")
            dest = target / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            dest.chmod(meta["mode"])
            seen.add(name)
    if seen != set(files):
        refuse("archive missing runtime members")


def inspect(docker: str, image: str) -> dict:
    proc = run([docker, "image", "inspect", image], timeout=30)
    if proc.returncode != 0:
        refuse("Docker image inspection failed")
    value = json.loads(proc.stdout)
    if type(value) is not list or len(value) != 1 or type(value[0]) is not dict:
        refuse("Docker image inspection malformed")
    return value[0]


def measure_generator(docker: str, image: str) -> dict[str, str]:
    """Measure the declared tools in the immutable dependency image before building."""
    paths = ("/usr/bin/perl", "/usr/share/perl5/Parse/RecDescent.pm")
    proc = run(
        [
            docker,
            "run",
            "--rm",
            "--user",
            "1000:1000",
            "--read-only",
            "--network",
            "none",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--cpus",
            "2",
            "--memory",
            "3g",
            "--pids-limit",
            "128",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=768m",
            "--entrypoint",
            "/usr/bin/sha256sum",
            image,
            *paths,
        ],
        timeout=90,
    )
    if proc.returncode != 0 or proc.stderr:
        refuse("native generator tool measurement failed")
    try:
        lines = proc.stdout.decode("ascii").splitlines()
    except UnicodeDecodeError:
        refuse("native generator tool measurement malformed")
    if len(lines) != len(paths):
        refuse("native generator tool measurement malformed")
    measured = {}
    for field, path, line in zip(("perl_sha256", "recdescent_sha256"), paths, lines):
        parts = line.split()
        if (
            len(parts) != 2
            or parts[1] != path
            or re.fullmatch(r"[0-9a-f]{64}", parts[0]) is None
        ):
            refuse("native generator tool measurement malformed")
        measured[field] = parts[0]
    return measured


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--docker", required=True, type=Path)
    p.add_argument("--package-a", required=True, type=Path)
    p.add_argument("--package-b", required=True, type=Path)
    p.add_argument("--base-image-id", required=True)
    p.add_argument("--architecture", required=True, choices=("arm64", "amd64"))
    p.add_argument("--out", required=True, type=Path)
    args = p.parse_args()
    for path in (args.docker, args.package_a, args.package_b, args.out.parent):
        no_alias(path)
    if (
        not args.docker.is_absolute()
        or not args.docker.is_file()
        or not os.access(args.docker, os.X_OK)
    ):
        refuse("Docker path must be an absolute executable")
    if args.package_a.resolve() == args.package_b.resolve():
        refuse("two independent package directories are required")
    if not args.out.is_absolute() or args.out.exists() or not args.out.parent.is_dir():
        refuse("output must be a new absolute directory")
    if (
        not args.base_image_id.startswith("sha256:")
        or len(args.base_image_id) != 71
        or any(c not in "0123456789abcdef" for c in args.base_image_id[7:])
    ):
        refuse("base must be a full immutable Docker image ID")
    a_manifest, a_archive, manifest, a_runtime, a_runtime_archive, runtime = (
        read_package(args.package_a)
    )
    b_manifest, b_archive, _, b_runtime, b_runtime_archive, _ = read_package(
        args.package_b
    )
    if (
        a_manifest != b_manifest
        or a_archive != b_archive
        or a_runtime != b_runtime
        or a_runtime_archive != b_runtime_archive
        or (args.package_a / "build-receipt.json").read_bytes()
        != (args.package_b / "build-receipt.json").read_bytes()
    ):
        refuse("independent source and generated runtime builds differ")
    if (
        runtime["generator"]["image_id"] != args.base_image_id
        or runtime["generator"]["architecture"] != args.architecture
    ):
        refuse("generator and package base identity mismatch")
    base = inspect(str(args.docker), args.base_image_id)
    if (
        base.get("Id") != args.base_image_id
        or base.get("Architecture") != args.architecture
        or base.get("Os") != "linux"
    ):
        refuse("base image ID or native architecture mismatch")
    measured_generator = measure_generator(str(args.docker), args.base_image_id)
    if any(
        runtime["generator"][key] != value for key, value in measured_generator.items()
    ):
        refuse("native generator tool bytes mismatch")
    dockerfile = Path(__file__).with_name("Dockerfile.probe")
    source_py = Path(__file__).with_name("package_source.py")
    verify_py = Path(__file__).with_name("verify_install.py")
    for file in (dockerfile, source_py, verify_py):
        if file.is_symlink() or not file.is_file():
            refuse("packaging source missing or aliased")
    args.out.mkdir()
    with tempfile.TemporaryDirectory(prefix="latexml-context-", dir=args.out) as temp:
        context = Path(temp)
        (context / "source").mkdir()
        archive_path = args.package_a / "latexml-runtime.tar"
        extract_verified(archive_path, context / "source", manifest, runtime)
        (context / "source" / "source-manifest.json").write_bytes(a_manifest)
        (context / "source" / "runtime-manifest.json").write_bytes(a_runtime)
        for file in (dockerfile, source_py, verify_py):
            shutil.copyfile(file, context / file.name)
        iid = args.out / "image-id.txt"
        build = run(
            [
                str(args.docker),
                "build",
                "--network",
                "none",
                "--pull=false",
                "--no-cache",
                "--build-arg",
                "BASE_IMAGE=" + args.base_image_id,
                "--build-arg",
                "MANIFEST_SHA256=" + sha(a_manifest),
                "--build-arg",
                "RUNTIME_MANIFEST_SHA256=" + sha(a_runtime),
                "--iidfile",
                str(iid),
                "-f",
                str(context / dockerfile.name),
                str(context),
            ],
            timeout=600,
            env={**os.environ, "DOCKER_BUILDKIT": "0"},
        )
        (args.out / "build.stdout").write_bytes(build.stdout)
        (args.out / "build.stderr").write_bytes(build.stderr)
        if build.returncode != 0 or not iid.is_file():
            refuse("disposable Docker build failed; private logs retained")
    image_id = iid.read_text().strip()
    if not image_id.startswith("sha256:") or len(image_id) != 71:
        refuse("Docker build did not return an immutable image ID")
    image = inspect(str(args.docker), image_id)
    labels = image.get("Config", {}).get("Labels") or {}
    if (
        image.get("Id") != image_id
        or image.get("Architecture") != args.architecture
        or image.get("Os") != "linux"
        or labels.get("org.aelira.latexml.source-manifest-sha256") != sha(a_manifest)
        or labels.get("org.aelira.latexml.runtime-manifest-sha256") != sha(a_runtime)
        or labels.get("org.aelira.latexml.base-image-id") != args.base_image_id
    ):
        refuse("installed image identity or labels mismatch")
    base_layers = base.get("RootFS", {}).get("Layers")
    child_layers = image.get("RootFS", {}).get("Layers")
    if (
        type(base_layers) is not list
        or type(child_layers) is not list
        or child_layers[: len(base_layers)] != base_layers
    ):
        refuse("installed image does not retain exact base layer prefix")
    check = run(
        [
            str(args.docker),
            "run",
            "--rm",
            "--user",
            "1000:1000",
            "--read-only",
            "--network",
            "none",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--cpus",
            "2",
            "--memory",
            "3g",
            "--pids-limit",
            "128",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=768m",
            "--entrypoint",
            "python3",
            image_id,
            "/usr/local/libexec/latexml/verify_install.py",
            "--root",
            "/opt/latexml-source",
            "--manifest",
            "/opt/latexml-source/source-manifest.json",
            "--manifest-sha256",
            sha(a_manifest),
            "--runtime-manifest",
            "/opt/latexml-source/runtime-manifest.json",
            "--runtime-manifest-sha256",
            sha(a_runtime),
        ],
        timeout=180,
    )
    (args.out / "install.stdout").write_bytes(check.stdout)
    (args.out / "install.stderr").write_bytes(check.stderr)
    receipt = {
        "schema": "aelira-latexml-immutable-image-v2",
        "source_builds_equal": True,
        "manifest_sha256": sha(a_manifest),
        "archive_sha256": sha(a_archive),
        "runtime_manifest_sha256": sha(a_runtime),
        "runtime_archive_sha256": sha(a_runtime_archive),
        "generator_tool_sha256": measured_generator,
        "base_image_id": args.base_image_id,
        "image_id": image_id,
        "architecture": args.architecture,
        "base_layer_count": len(base_layers),
        "image_layer_count": len(child_layers),
        "base_layer_prefix_match": True,
        "build_exit": build.returncode,
        "installed_verify_exit": check.returncode,
        "installed_verify_stdout_sha256": sha(check.stdout),
        "installed_verify_stderr_sha256": sha(check.stderr),
    }
    (args.out / "acceptance.json").write_text(
        json.dumps(receipt, sort_keys=True, indent=2) + "\n"
    )
    if check.returncode != 0:
        refuse("installed runtime verification failed; private logs retained")
    print(json.dumps(receipt, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (
        ValueError,
        OSError,
        subprocess.TimeoutExpired,
        json.JSONDecodeError,
        tarfile.TarError,
    ) as error:
        print("latexml image acceptance refused: " + str(error), file=sys.stderr)
        sys.exit(2)
