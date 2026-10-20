#!/usr/bin/env python3
"""Produce and replay exact Pillow build/installed proof from retained bytes."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

from strict_json import boolean, exact_object, integer, load, loads

HEX = re.compile(r"[a-f0-9]{64}")
IMAGE = re.compile(r"sha256:[a-f0-9]{64}")
CONSUMERS = {
    "src/education/remediation/pdf_remediator.py": "84200c417654c5bc481a96dc679c6826436825f7eaf48968c5314ba13c1bc8ac",
    "src/education/handwritten_math_suitability.py": "310e239e7837e12e22a3ee14a831f38cf163360959675f7ae0ad149c4a5ddf50",
}
FEATURES = (
    "jpg",
    "zlib",
    "libtiff",
    "webp",
    "jpg_2000",
    "freetype2",
    "littlecms2",
    "raqm",
    "avif",
    "xcb",
)
FORMATS = ("PNG", "JPEG", "WEBP", "JPEG2000", "AVIF", "TIFF")
MALFORMED = ("PNG", "JPEG", "TIFF", "random")
TIFF_HASH = {
    "arm64": "63e0add472fc1c99cbcfadcf58df8dc11d8da6516f224ecb57ff12e06029f384",
    "amd64": "f45a58ee6e6d76dd51da58b0d721b3cf4d49d3a434f224340a7f30646b21d340",
}


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def regular(path: Path) -> Path:
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("nonregular proof input")
    return path


def scalar(root: Path, name: str, pattern: re.Pattern[str]) -> str:
    path = regular(root / name)
    if path.stat().st_size > 256:
        raise ValueError("oversized proof scalar")
    value = path.read_text().strip()
    if not pattern.fullmatch(value):
        raise ValueError("invalid proof scalar")
    return value


def write(root: Path, name: str, payload: object) -> None:
    path = root / name
    if path.exists():
        raise ValueError("proof already exists")
    temporary = root / f".{name}.tmp"
    temporary.write_text(
        json.dumps(payload, allow_nan=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    temporary.replace(path)


def strict_equal(actual: object, expected: object) -> None:
    if type(actual) is not type(expected):
        raise ValueError("proof type mismatch")
    if type(expected) is dict:
        if set(actual) != set(expected):
            raise ValueError("proof field mismatch")
        for key in expected:
            strict_equal(actual[key], expected[key])
    elif type(expected) is list:
        if len(actual) != len(expected):
            raise ValueError("proof list mismatch")
        for left, right in zip(actual, expected):
            strict_equal(left, right)
    elif actual != expected:
        raise ValueError("proof value mismatch")


def inventory(root: Path) -> dict[str, object]:
    entries: list[dict[str, object]] = []
    total = 0
    for directory in ("input", "provenance"):
        base = root / directory
        if not stat.S_ISDIR(base.lstat().st_mode):
            raise ValueError("missing proof input directory")
        for current, dirs, files in os.walk(base, followlinks=False):
            for name in dirs:
                if stat.S_ISLNK((Path(current) / name).lstat().st_mode):
                    raise ValueError("symlink input directory")
            for name in files:
                path = regular(Path(current) / name)
                relative = path.relative_to(root).as_posix()
                size = path.stat().st_size
                total += size
                entries.append({"path": relative, "bytes": size, "sha256": sha(path)})
    entries.sort(key=lambda item: str(item["path"]))
    if not entries or len(entries) > 500 or total > 2 * 1024 * 1024 * 1024:
        raise ValueError("input inventory bounds failed")
    return {
        "schema": "pillow-input-inventory-v1",
        "files": entries,
        "count": len(entries),
        "bytes": total,
    }


def check_inventory(root: Path) -> dict[str, object]:
    recorded = exact_object(
        load(regular(root / "input-inventory.json")),
        {"schema", "files", "count", "bytes"},
    )
    strict_equal(recorded, inventory(root))
    return recorded


def capture_inventory(root: Path) -> None:
    write(root, "input-inventory.json", inventory(root))


def native(root: Path, arch: str) -> tuple[str, str]:
    if arch not in ("arm64", "amd64"):
        raise ValueError("unsupported architecture")
    host = scalar(root, "host-machine.txt", re.compile(r"aarch64|x86_64"))
    daemon = scalar(
        root, "daemon-architecture.txt", re.compile(r"aarch64|arm64|x86_64|amd64")
    )
    if (host, daemon, arch) not in (
        ("aarch64", "aarch64", "arm64"),
        ("aarch64", "arm64", "arm64"),
        ("x86_64", "x86_64", "amd64"),
        ("x86_64", "amd64", "amd64"),
    ):
        raise ValueError("host/daemon architecture mismatch")
    return host, daemon


def run_chain(root: Path, arch: str) -> tuple[str, str]:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    text = subprocess.check_output(
        [
            sys.executable,
            "-B",
            str(root / "recipe/verify-frozen-inputs.py"),
            str(root / "input"),
            str(root / "provenance"),
            arch,
        ],
        text=True,
        env=env,
    )
    lines = text.splitlines()
    packages = len(list((root / "input/apt-debs").glob("*.deb")))
    releases = len(list((root / "input/apt-lists").glob("*_InRelease")))
    expected = (
        f"accepted_tiff_input=pass architecture={arch} packages=3",
        f"frozen_input_chain=pass architecture={arch} signed_packages={packages} "
        f"signed_releases={releases} pypi_inputs=4 tiff_packages=3",
    )
    if tuple(lines) != expected or releases != 4 or packages < 1:
        raise ValueError("incomplete authenticated input-chain result")
    return expected


def run_wheel(root: Path, wheel: Path, arch: str) -> str:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    text = subprocess.check_output(
        [sys.executable, "-B", str(root / "recipe/verify-wheel.py"), str(wheel), arch],
        text=True,
        env=env,
    )
    expected = (
        f"wheel_record=pass entries=119 arch={arch} elf_modules=5 "
        "libtiff_needed=libtiff.so.6"
    )
    if text.splitlines() != [expected]:
        raise ValueError("incomplete wheel RECORD/ELF result")
    return expected


def build_data(root: Path, arch: str) -> dict[str, object]:
    host, daemon = native(root, arch)
    check_inventory(root)
    if scalar(root, "build-exit.txt", re.compile(r"[0-9]{1,3}")) != "0":
        raise ValueError("build command did not exit zero")
    builder = scalar(root, "provenance/frozen-builder-id.txt", IMAGE)
    recipe_sha = sha(regular(root / "recipe/manifest.json"))
    chain = run_chain(root, arch)
    wheels = []
    for run in "ab":
        folder = root / f"build-{run}/wheels"
        found = list(folder.glob("pillow-12.3.0-*.whl"))
        if len(found) != 1 or len(list(folder.iterdir())) != 1:
            raise ValueError("wheel directory incomplete")
        wheel = regular(found[0])
        wheel_result = run_wheel(root, wheel, arch)
        if regular(
            root / f"build-{run}/input-chain.log"
        ).read_text().splitlines() != list(chain):
            raise ValueError("producer authenticated-chain log mismatch")
        if regular(
            root / f"build-{run}/wheel-verification.log"
        ).read_text().splitlines() != [wheel_result]:
            raise ValueError("producer wheel RECORD/ELF log mismatch")
        wheels.append(
            {
                "run": run,
                "path": wheel.relative_to(root).as_posix(),
                "sha256": sha(wheel),
                "bytes": wheel.stat().st_size,
                "record_entries": 119,
                "elf_modules": 5,
                "libtiff_needed": "libtiff.so.6",
                "input_chain_log_sha256": sha(root / f"build-{run}/input-chain.log"),
                "wheel_log_sha256": sha(root / f"build-{run}/wheel-verification.log"),
                "packages_sha256": sha(root / f"build-{run}/build-packages.tsv"),
                "python_packages_sha256": sha(
                    root / f"build-{run}/build-python-packages.txt"
                ),
            }
        )
    if (
        wheels[0]["sha256"] != wheels[1]["sha256"]
        or wheels[0]["bytes"] != wheels[1]["bytes"]
    ):
        raise ValueError("wheel pair differs")
    return {
        "schema": "pillow-build-proof-v1",
        "architecture": arch,
        "host_machine": host,
        "daemon_architecture": daemon,
        "builder_image": builder,
        "recipe_manifest_sha256": recipe_sha,
        "input_inventory_sha256": sha(root / "input-inventory.json"),
        "authenticated_chain": {
            "signed_packages": len(list((root / "input/apt-debs").glob("*.deb"))),
            "signed_releases": 4,
            "pypi_inputs": 4,
            "tiff_packages": 3,
        },
        "wheels": wheels,
        "build_exit": 0,
    }


def capture_build(root: Path, arch: str) -> None:
    write(root, "build-proof.json", build_data(root, arch))


def check_build(root: Path, arch: str) -> dict[str, object]:
    expected = build_data(root, arch)
    recorded = exact_object(load(regular(root / "build-proof.json")), set(expected))
    strict_equal(recorded, expected)
    return recorded


def semantic_result(raw: object, arch: str, wheel_sha: str) -> dict[str, object]:
    keys = {
        "schema",
        "record_entries",
        "wheel_payload_entries",
        "wheel_sha256",
        "features_enabled",
        "features_disabled",
        "roundtrip_bytes",
        "tiff_pages",
        "tiff_pixels",
        "loaded_tiff_sha256",
        "font_colour",
        "malformed_refused",
        "aelira_pdf",
        "aelira_suitability",
        "private_directory_absent",
    }
    data = exact_object(raw, keys)
    if data["schema"] != "pillow-installed-semantics-v1":
        raise ValueError("semantic schema mismatch")
    if (
        integer(data["record_entries"]) != 122
        or integer(data["wheel_payload_entries"]) != 119
    ):
        raise ValueError("installed RECORD/wheel payload counts mismatch")
    if (
        data["wheel_sha256"] != wheel_sha
        or data["loaded_tiff_sha256"] != TIFF_HASH[arch]
    ):
        raise ValueError("installed wheel/TIFF identity mismatch")
    enabled = exact_object(data["features_enabled"], set(FEATURES))
    for value in enabled.values():
        boolean(value)
    if enabled != {x: True for x in FEATURES}:
        raise ValueError("enabled media features incomplete")
    disabled = exact_object(data["features_disabled"], {"libimagequant", "tkinter"})
    for value in disabled.values():
        boolean(value)
    if disabled != {"libimagequant": False, "tkinter": False}:
        raise ValueError("disabled feature result mismatch")
    roundtrips = exact_object(data["roundtrip_bytes"], set(FORMATS))
    for count in roundtrips.values():
        integer(count, 1)
    if integer(data["tiff_pages"]) != 2 or data["tiff_pixels"] != [
        [255, 0, 0],
        [0, 255, 0],
    ]:
        raise ValueError("TIFF semantic result mismatch")
    for pixel in data["tiff_pixels"]:
        if type(pixel) is not list or len(pixel) != 3:
            raise ValueError("TIFF pixel schema mismatch")
        for channel in pixel:
            integer(channel)
    malformed = exact_object(data["malformed_refused"], set(MALFORMED))
    for value in malformed.values():
        boolean(value)
    if malformed != {x: True for x in MALFORMED}:
        raise ValueError("malformed image refusals incomplete")
    for field in (
        "font_colour",
        "aelira_pdf",
        "aelira_suitability",
        "private_directory_absent",
    ):
        if boolean(data[field]) is not True:
            raise ValueError("consumer semantic result incomplete")
    return data


def consumer_sources(root: Path) -> dict[str, str]:
    lines = regular(root / "consumer-source-sha256.tsv").read_text().splitlines()
    actual: dict[str, str] = {}
    for line in lines:
        parts = line.split("\t")
        if len(parts) != 2 or parts[0] in actual or not HEX.fullmatch(parts[1]):
            raise ValueError("candidate consumer source receipt invalid")
        actual[parts[0]] = parts[1]
    if actual != CONSUMERS:
        raise ValueError("candidate consumer sources differ")
    return actual


def installed_data(root: Path, arch: str) -> dict[str, object]:
    build = check_build(root, arch)
    if scalar(root, "consumer-exit.txt", re.compile(r"[0-9]{1,3}")) != "0":
        raise ValueError("installed command did not exit zero")
    baseline = scalar(root, "baseline-image-id.txt", IMAGE)
    candidate = scalar(root, "installed-image-id.txt", IMAGE)
    sources = consumer_sources(root)
    log = regular(root / "installed-consumer.log")
    lines = log.read_text().splitlines()
    markers = [
        line.removeprefix("pillow_installed_receipt=")
        for line in lines
        if line.startswith("pillow_installed_receipt=")
    ]
    if len(markers) != 1 or not lines[-1].startswith("pillow_installed_receipt="):
        raise ValueError("installed semantic receipt missing/duplicate")
    semantics = semantic_result(
        loads(markers[0]), arch, str(build["wheels"][0]["sha256"])
    )
    return {
        "schema": "pillow-installed-proof-v1",
        "architecture": arch,
        "build_proof_sha256": sha(root / "build-proof.json"),
        "baseline_image": baseline,
        "candidate_image": candidate,
        "consumer_sources": sources,
        "consumer_source_receipt_sha256": sha(root / "consumer-source-sha256.tsv"),
        "consumer_log_sha256": sha(log),
        "semantics": semantics,
        "build_exit": 0,
        "consumer_exit": 0,
    }


def capture_installed(root: Path, arch: str) -> None:
    write(root, "installed-proof.json", installed_data(root, arch))


def check_installed(root: Path, arch: str) -> dict[str, object]:
    expected = installed_data(root, arch)
    recorded = exact_object(load(regular(root / "installed-proof.json")), set(expected))
    strict_equal(recorded, expected)
    return recorded


def public_replay(root: Path, arch: str) -> None:
    """Recheck all exported source/authentication and wheel bytes without the archive."""
    chain = run_chain(root, arch)
    build = exact_object(
        load(regular(root / "build-proof.json")), set(build_data_keys())
    )
    if build["schema"] != "pillow-build-proof-v1" or build["architecture"] != arch:
        raise ValueError("copied build proof schema/architecture mismatch")
    if integer(build["build_exit"]) != 0 or not IMAGE.fullmatch(build["builder_image"]):
        raise ValueError("copied build proof outcome/identity mismatch")
    if build["recipe_manifest_sha256"] != sha(root / "recipe/manifest.json"):
        raise ValueError("copied recipe manifest mismatch")
    chain_counts = exact_object(
        build["authenticated_chain"],
        {"signed_packages", "signed_releases", "pypi_inputs", "tiff_packages"},
    )
    expected_counts = {
        "signed_packages": len(list((root / "input/apt-debs").glob("*.deb"))),
        "signed_releases": 4,
        "pypi_inputs": 4,
        "tiff_packages": 3,
    }
    strict_equal(chain_counts, expected_counts)
    wheels = build["wheels"]
    if type(wheels) is not list or len(wheels) != 2:
        raise ValueError("copied wheel proof incomplete")
    seen_hashes = []
    for run, item in zip("ab", wheels):
        exact_object(
            item,
            {
                "run",
                "path",
                "sha256",
                "bytes",
                "record_entries",
                "elf_modules",
                "libtiff_needed",
                "input_chain_log_sha256",
                "wheel_log_sha256",
                "packages_sha256",
                "python_packages_sha256",
            },
        )
        if item["run"] != run or item["path"] != (
            f"build-{run}/wheels/pillow-12.3.0-cp314-cp314-linux_"
            f"{'aarch64' if arch == 'arm64' else 'x86_64'}.whl"
        ):
            raise ValueError("copied wheel path mismatch")
        if (
            integer(item["bytes"], 1) > 128 * 1024 * 1024
            or integer(item["record_entries"]) != 119
            or integer(item["elf_modules"]) != 5
            or item["libtiff_needed"] != "libtiff.so.6"
        ):
            raise ValueError("copied wheel RECORD/ELF proof incomplete")
        if not HEX.fullmatch(item["sha256"]):
            raise ValueError("copied wheel hash malformed")
        wheel = regular(root / item["path"])
        if sha(wheel) != item["sha256"] or wheel.stat().st_size != item["bytes"]:
            raise ValueError("copied wheel differs from producer proof")
        run_wheel(root, wheel, arch)
        seen_hashes.append(item["sha256"])
    if seen_hashes[0] != seen_hashes[1]:
        raise ValueError("copied wheel pair differs")
    installed = exact_object(
        load(regular(root / "installed-proof.json")),
        {
            "schema",
            "architecture",
            "build_proof_sha256",
            "baseline_image",
            "candidate_image",
            "consumer_sources",
            "consumer_source_receipt_sha256",
            "consumer_log_sha256",
            "semantics",
            "build_exit",
            "consumer_exit",
        },
    )
    if (
        installed["schema"] != "pillow-installed-proof-v1"
        or installed["architecture"] != arch
        or installed["build_proof_sha256"] != sha(root / "build-proof.json")
        or installed["consumer_sources"] != CONSUMERS
    ):
        raise ValueError("copied installed proof binding mismatch")
    if (
        integer(installed["build_exit"]) != 0
        or integer(installed["consumer_exit"]) != 0
    ):
        raise ValueError("copied installed outcome mismatch")
    if not IMAGE.fullmatch(installed["baseline_image"]) or not IMAGE.fullmatch(
        installed["candidate_image"]
    ):
        raise ValueError("copied image ID malformed")
    semantic_result(installed["semantics"], arch, seen_hashes[0])
    if len(chain) != 2:
        raise ValueError("copied input proof incomplete")


def build_data_keys() -> tuple[str, ...]:
    return (
        "schema",
        "architecture",
        "host_machine",
        "daemon_architecture",
        "builder_image",
        "recipe_manifest_sha256",
        "input_inventory_sha256",
        "authenticated_chain",
        "wheels",
        "build_exit",
    )


if __name__ == "__main__":
    action, location, arch = sys.argv[1:4]
    root = Path(location)
    if action == "inventory":
        capture_inventory(root)
    elif action == "check-inventory":
        check_inventory(root)
    elif action == "build":
        capture_build(root, arch)
    elif action == "check-build":
        check_build(root, arch)
    elif action == "installed":
        capture_installed(root, arch)
    elif action == "check-installed":
        check_installed(root, arch)
    elif action == "public-replay":
        public_replay(root, arch)
    else:
        raise SystemExit("unknown Pillow proof action")
