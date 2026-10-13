#!/usr/bin/env python3
"""Refuse an incomplete, modified or shadowed installed LaTeXML source runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

from package_source import (
    BINDING,
    COMMIT,
    FILE_COUNT,
    LICENSE_SHA256,
    MANIFEST_SHA256,
    ORIGIN,
    ORIGINAL_SHA256,
    PATCHED_SHA256,
    PATCH_SHA256,
    TOOLS,
    TREE,
    GENERATED,
    MAKEFILE_SHA256,
    TEMPLATES,
    ARCHIVE_SHA256,
)


def refuse(message: str) -> None:
    raise ValueError(message)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    out = {}
    for key, value in pairs:
        if key in out:
            refuse("duplicate JSON key")
        out[key] = value
    return out


def no_alias(path: Path) -> None:
    if not path.is_absolute():
        refuse("path is not absolute")
    current = Path("/")
    for part in path.parts[1:]:
        current /= part
        if stat.S_ISLNK(current.lstat().st_mode):
            refuse("symlink in installed path")


def fail_walk(error: OSError) -> None:
    """An incomplete traversal cannot establish the installed file set."""
    raise error


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", required=True, type=Path)
    p.add_argument("--manifest", required=True, type=Path)
    p.add_argument("--manifest-sha256", required=True)
    p.add_argument("--runtime-manifest", required=True, type=Path)
    p.add_argument("--runtime-manifest-sha256", required=True)
    args = p.parse_args()
    no_alias(args.root)
    no_alias(args.manifest)
    no_alias(args.runtime_manifest)
    if (
        not args.root.is_dir()
        or not args.manifest.is_file()
        or not args.runtime_manifest.is_file()
    ):
        refuse("installed root or manifest missing")
    if args.manifest.stat().st_size > 300_000:
        refuse("manifest exceeds size bound")
    raw = args.manifest.read_bytes()
    if (
        args.manifest_sha256 != MANIFEST_SHA256
        or sha(raw) != MANIFEST_SHA256
        or len(raw) > 300_000
    ):
        refuse("manifest digest or size mismatch")
    manifest = json.loads(raw, object_pairs_hook=unique_object)
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
    constants = {
        "schema": "aelira-latexml-source-v1",
        "upstream_origin": ORIGIN,
        "upstream_commit": COMMIT,
        "upstream_tree": TREE,
        "adapter_sha256": PATCH_SHA256,
        "original_binding_sha256": ORIGINAL_SHA256,
        "patched_binding_sha256": PATCHED_SHA256,
        "license_sha256": LICENSE_SHA256,
    }
    if any(manifest[k] != v for k, v in constants.items()):
        refuse("manifest source identity mismatch")
    if type(manifest["source_epoch"]) is not int or manifest["source_epoch"] <= 0:
        refuse("source epoch invalid")
    files = manifest["files"]
    if type(files) is not dict or len(files) != FILE_COUNT:
        refuse("runtime inventory count mismatch")
    expected = set(files)
    if BINDING not in expected or "LICENSE" not in expected:
        refuse("binding or license missing")
    for name, meta in files.items():
        if type(name) is not str or (
            name != "LICENSE" and not name.startswith(("lib/", "bin/"))
        ):
            refuse("invalid runtime filename")
        if any(part in ("", ".", "..") or "\\" in part for part in name.split("/")):
            refuse("unsafe runtime filename")
        if type(meta) is not dict or set(meta) != {"sha256", "size", "mode"}:
            refuse("file metadata schema mismatch")
        if (
            type(meta["sha256"]) is not str
            or len(meta["sha256"]) != 64
            or any(c not in "0123456789abcdef" for c in meta["sha256"])
        ):
            refuse("invalid file digest")
        if type(meta["size"]) is not int or not 0 <= meta["size"] <= 50_000_000:
            refuse("invalid file size")
        if type(meta["mode"]) is not int or meta["mode"] not in (0o644, 0o755):
            refuse("invalid file mode")
    if (
        files[BINDING]["sha256"] != PATCHED_SHA256
        or files["LICENSE"]["sha256"] != LICENSE_SHA256
    ):
        refuse("binding or license inventory mismatch")
    if args.runtime_manifest.stat().st_size > 20_000:
        refuse("runtime manifest exceeds size bound")
    runtime_raw = args.runtime_manifest.read_bytes()
    if sha(runtime_raw) != args.runtime_manifest_sha256:
        refuse("runtime manifest digest mismatch")
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
    if (
        any(
            runtime.get(key) != value
            for key, value in {
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
            }.items()
        )
        or type(runtime["source_file_count"]) is not int
        or type(runtime["runtime_file_count"]) is not int
    ):
        refuse("runtime source identity mismatch")
    generator = runtime["generator"]
    if (
        type(generator) is not dict
        or set(generator)
        != {"image_id", "architecture", "perl_sha256", "recdescent_sha256"}
        or generator["architecture"] not in ("amd64", "arm64")
        or type(generator["image_id"]) is not str
        or not generator["image_id"].startswith("sha256:")
        or len(generator["image_id"]) != 71
        or any(c not in "0123456789abcdef" for c in generator["image_id"][7:])
        or any(
            type(generator[k]) is not str
            or len(generator[k]) != 64
            or any(c not in "0123456789abcdef" for c in generator[k])
            for k in ("perl_sha256", "recdescent_sha256")
        )
    ):
        refuse("runtime generator identity malformed")
    generated = runtime["generated"]
    if type(generated) is not dict or set(generated) != set(GENERATED):
        refuse("generated runtime membership mismatch")
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
            or name in files
        ):
            refuse("generated runtime metadata mismatch")
    for field, path in (
        ("perl_sha256", Path("/usr/bin/perl")),
        ("recdescent_sha256", Path("/usr/share/perl5/Parse/RecDescent.pm")),
    ):
        if not path.is_file() or sha(path.read_bytes()) != generator[field]:
            refuse("installed generator tool bytes mismatch")
    actual = set()
    for directory, dirs, names in os.walk(
        args.root, followlinks=False, onerror=fail_walk
    ):
        for name in dirs + names:
            item = Path(directory) / name
            mode = item.lstat().st_mode
            if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                refuse("alias or special file in installed runtime")
            if stat.S_ISREG(mode):
                relative = item.relative_to(args.root).as_posix()
                if relative not in ("source-manifest.json", "runtime-manifest.json"):
                    actual.add(relative)
    if actual != expected | set(GENERATED):
        refuse("installed file set has missing or extra members")
    if args.manifest.resolve() != (args.root / "source-manifest.json").resolve():
        refuse("manifest is outside installed root")
    if (
        args.runtime_manifest.resolve()
        != (args.root / "runtime-manifest.json").resolve()
    ):
        refuse("runtime manifest is outside installed root")
    for name, meta in {**files, **generated}.items():
        path = args.root / name
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != meta["mode"]
            or info.st_size != meta["size"]
        ):
            refuse("installed file mode or size mismatch: " + name)
        if sha(path.read_bytes()) != meta["sha256"]:
            refuse("installed file bytes mismatch: " + name)
    commands = manifest["commands"]
    if type(commands) is not dict or set(commands) != set(TOOLS):
        refuse("command inventory mismatch")
    observed = {}
    for tool in TOOLS:
        target = str(args.root / "bin" / tool)
        if (
            commands[tool] != "/opt/latexml-source/bin/" + tool
            or str(args.root) != "/opt/latexml-source"
        ):
            refuse("installed command target mismatch")
        found = shutil.which(tool)
        if found is None or Path(found).resolve(strict=True) != Path(target):
            refuse("command missing or shadowed: " + tool)
        observed[tool] = found
    if str(args.root) != "/opt/latexml-source":
        refuse("loaded runtime root mismatch")
    if os.environ.get("PERL5LIB") != str(args.root / "lib") or os.environ.get(
        "PERL5OPT"
    ):
        refuse("Perl module search path is shadowed")
    loaded = subprocess.run(
        [
            "/usr/bin/perl",
            "-MLaTeXML::Version",
            "-MLaTeXML::MathGrammar",
            "-MParse::RecDescent",
            "-e",
            "print \"$INC{'LaTeXML/Version.pm'}\\n$INC{'LaTeXML/MathGrammar.pm'}\\n$INC{'Parse/RecDescent.pm'}\\n\"",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
        check=False,
        env={"PATH": "/usr/bin:/bin", "PERL5LIB": str(args.root / "lib")},
    )
    if loaded.returncode != 0 or loaded.stdout.decode().splitlines() != [
        str(args.root / "lib/LaTeXML/Version.pm"),
        str(args.root / "lib/LaTeXML/MathGrammar.pm"),
        "/usr/share/perl5/Parse/RecDescent.pm",
    ]:
        refuse("generated modules did not load from accepted runtime root")
    print(
        json.dumps(
            {
                "schema": "aelira-latexml-installed-v2",
                "source_manifest_sha256": sha(raw),
                "runtime_manifest_sha256": sha(runtime_raw),
                "runtime_file_count": len(files) + len(generated),
                "commands": observed,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print("latexml install refused: " + str(error), file=sys.stderr)
        sys.exit(2)
