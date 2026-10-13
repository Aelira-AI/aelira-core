#!/usr/bin/env python3
"""Build a complete, deterministic LaTeXML runtime archive from verified Git objects.

The caller supplies an existing official Git repository. No network or package
manager is used. Each invocation exports and patches independently.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile

COMMIT = "0db6f47ea859391f9099e6e25e7405afad8467bf"
TREE = "2693c0c65c86f6b36370af18496d8283cf4f73ea"
ORIGIN = "https://github.com/brucemiller/LaTeXML.git"
PATCH_SHA256 = "8bea4050c667438c49e5a175dbaa52bb75415fd7eb4ec0a6508eda20ddf4795b"
LICENSE_SHA256 = "f856925cca60fce156b93545442dc874de55ac40edeeb3a1005521abb13c44b3"
ORIGINAL_SHA256 = "5b4741bc6ac3ee77f7e419e14b7bd50aa1115a57dcec7d707cfff8e29a9ecacc"
PATCHED_SHA256 = "cbcb285c38233641d1cab2170fad611e0d39b95ce325f5a405aba8c2895968d2"
BINDING = "lib/LaTeXML/Package/siunitx.sty.ltxml"
TOOLS = ("latexml", "latexmlc", "latexmlfind", "latexmlmath", "latexmlpost")
FILE_COUNT = 760
MANIFEST_SHA256 = "4d31f365789d6e66ec02542d34de947b10c14961ad1ba4d7a0ec836d92a47760"
ARCHIVE_SHA256 = "1330d4e8cc55608eeb49a44fadc89b04699c928c258de954aebdb25904359d76"
MAKEFILE_SHA256 = "dd354e2809bb4de91f0a28fc22f50edcd5ef2b4653b961b156284cf355002d72"
TEMPLATES = {
    "lib/LaTeXML/Version.in": "467b5dad2bdf9d15561d7faf53722b1748a63cc62c53ff21e1cd60227726f103",
    "lib/LaTeXML/MathGrammar": "4e04d9b04296951dc84031bdc6619a6e4b319eaa63419d28592562ae9fea507b",
}
GENERATED = {
    "lib/LaTeXML/Version.pm": "bb1b7ff039b0563eacfd2541a2b8725ec33e46309e508be5491fd8a062304e6e",
    "lib/LaTeXML/MathGrammar.pm": "94018fc222d72df04b2cd1e0bd1bc1f614d733ca6c53a891c98bd77037ccf58e",
}


def fail(reason: str) -> None:
    raise ValueError(reason)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run(*args: str, cwd: Path | None = None) -> bytes:
    return subprocess.run(
        args,
        cwd=cwd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
    ).stdout


def safe_path(name: str) -> bool:
    parts = name.split("/")
    return (
        bool(name)
        and not name.startswith("/")
        and all(part not in ("", ".", "..") and "\\" not in part for part in parts)
        and (name == "LICENSE" or name.startswith(("lib/", "bin/")))
    )


def no_alias(path: Path) -> None:
    if not path.is_absolute():
        fail("input path is not absolute")
    current = Path("/")
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            fail("aliased input path")


def git_blob(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--git", required=True, type=Path)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--patch", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--docker", required=True, type=Path)
    parser.add_argument("--generator-image-id", required=True)
    parser.add_argument("--architecture", required=True, choices=("arm64", "amd64"))
    parser.add_argument("--perl-sha256", required=True)
    parser.add_argument("--recdescent-sha256", required=True)
    a = parser.parse_args()
    generator_uid, generator_gid = os.getuid(), os.getgid()
    if generator_uid == 0:
        fail("native generator requires a nonroot host owner")
    for path in (a.git, a.repo, a.patch, a.out.parent, a.docker):
        no_alias(path)
    if not a.docker.is_file() or not os.access(a.docker, os.X_OK):
        fail("Docker must be an absolute executable")
    if (
        not a.generator_image_id.startswith("sha256:")
        or len(a.generator_image_id) != 71
        or any(c not in "0123456789abcdef" for c in a.generator_image_id[7:])
    ):
        fail("generator image must be an immutable ID")
    for tool_hash in (a.perl_sha256, a.recdescent_sha256):
        if len(tool_hash) != 64 or any(c not in "0123456789abcdef" for c in tool_hash):
            fail("generator tool hash malformed")
    if not a.git.is_absolute() or not a.git.is_file() or not os.access(a.git, os.X_OK):
        fail("git must be an existing absolute executable")
    if not a.repo.is_absolute() or a.repo.is_symlink() or not a.repo.is_dir():
        fail("repo must be an existing absolute non-symlink directory")
    if not a.patch.is_absolute() or a.patch.is_symlink() or not a.patch.is_file():
        fail("patch must be an existing absolute non-symlink file")
    if not a.out.is_absolute() or a.out.exists() or a.out.is_symlink():
        fail("out must be a new absolute directory")
    if not a.out.parent.is_dir() or a.out.parent.is_symlink():
        fail("out parent must be an existing non-symlink directory")
    if a.patch.stat().st_size > 100_000:
        fail("adapter patch exceeds size bound")
    patch = a.patch.read_bytes()
    if digest(patch) != PATCH_SHA256:
        fail("adapter patch hash mismatch")
    git = str(a.git)
    repo = str(a.repo)
    if run(git, "-C", repo, "remote", "get-url", "origin").decode().strip() != ORIGIN:
        fail("upstream origin mismatch")
    if run(git, "-C", repo, "rev-parse", "HEAD").decode().strip() != COMMIT:
        fail("upstream HEAD mismatch")
    if run(git, "-C", repo, "rev-parse", "HEAD^{tree}").decode().strip() != TREE:
        fail("upstream tree mismatch")
    commit_text = run(git, "-C", repo, "cat-file", "-p", COMMIT).decode()
    if not commit_text.startswith("tree " + TREE + "\n"):
        fail("commit object tree mismatch")
    makefile = run(git, "-C", repo, "show", COMMIT + ":Makefile.PL")
    if (
        digest(makefile) != MAKEFILE_SHA256
        or b"pure_all :: $(REVISION_FILE)" not in makefile
        or b"pure_all :: $(INST_LIBDIR)/LaTeXML/MathGrammar.pm" not in makefile
    ):
        fail("official generation rule mismatch")
    epoch = int(run(git, "-C", repo, "show", "-s", "--format=%ct", COMMIT).strip())
    listed = run(
        git, "-C", repo, "ls-tree", "-rz", COMMIT, "--", "lib", "bin", "LICENSE"
    )
    expected: dict[str, tuple[int, str]] = {}
    for item in listed.rstrip(b"\0").split(b"\0"):
        meta, raw_name = item.split(b"\t", 1)
        mode, typ, blob = meta.decode("ascii").split(" ")
        name = raw_name.decode("utf-8")
        if (
            not safe_path(name)
            or name in expected
            or typ != "blob"
            or mode not in ("100644", "100755")
        ):
            fail("unsafe or duplicate Git runtime entry")
        expected[name] = (int(mode, 8), blob)
    if len(expected) != FILE_COUNT or set(f"bin/{t}" for t in TOOLS) - expected.keys():
        fail("upstream runtime inventory incomplete")
    archive = run(
        git, "-C", repo, "archive", "--format=tar", COMMIT, "lib", "bin", "LICENSE"
    )
    original: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as source:
        for member in source:
            if member.isdir():
                continue
            name = member.name
            if name not in expected or name in original or not member.isfile():
                fail("unsafe, extra or duplicate exported runtime entry")
            data = source.extractfile(member).read()
            # Git's tar writer adds group-write (0664/0775); the package below
            # records the actual tracked Git mode (0644/0755).
            if git_blob(data) != expected[name][1] or member.mode != (
                (expected[name][0] & 0o777) | 0o020
            ):
                fail("exported bytes or mode differ from Git object")
            original[name] = data
    if original.keys() != expected.keys():
        fail("export is missing Git runtime entries")
    if (
        digest(original["LICENSE"]) != LICENSE_SHA256
        or digest(original[BINDING]) != ORIGINAL_SHA256
    ):
        fail("original license or binding mismatch")

    with tempfile.TemporaryDirectory(prefix="latexml-package-") as scratch:
        stage = Path(scratch)
        for name, data in original.items():
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            path.chmod(expected[name][0] & 0o777)
        run(git, "apply", "--check", str(a.patch), cwd=stage)
        run(git, "apply", str(a.patch), cwd=stage)
        files = {}
        for name in sorted(expected):
            path = stage / name
            if path.is_symlink() or not path.is_file():
                fail("patched runtime entry missing or aliased")
            data = path.read_bytes()
            if name != BINDING and data != original[name]:
                fail("patch changed a file outside the binding")
            files[name] = {
                "sha256": digest(data),
                "size": len(data),
                "mode": expected[name][0] & 0o777,
            }
        if digest((stage / BINDING).read_bytes()) != PATCHED_SHA256:
            fail("patched binding mismatch")
        if any(
            digest((stage / name).read_bytes()) != expected_hash
            for name, expected_hash in TEMPLATES.items()
        ):
            fail("upstream generation template mismatch")
        actual = {
            p.relative_to(stage).as_posix() for p in stage.rglob("*") if p.is_file()
        }
        if actual != set(expected):
            fail("patch added or removed runtime entries")
        manifest = {
            "schema": "aelira-latexml-source-v1",
            "upstream_origin": ORIGIN,
            "upstream_commit": COMMIT,
            "upstream_tree": TREE,
            "source_epoch": epoch,
            "adapter_sha256": PATCH_SHA256,
            "original_binding_sha256": ORIGINAL_SHA256,
            "patched_binding_sha256": PATCHED_SHA256,
            "license_sha256": LICENSE_SHA256,
            "files": files,
            "commands": {tool: f"/opt/latexml-source/bin/{tool}" for tool in TOOLS},
        }
        manifest_bytes = (
            json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
        if digest(manifest_bytes) != MANIFEST_SHA256:
            fail("complete source manifest differs from verified Git-derived inventory")
        with tempfile.TemporaryDirectory(
            prefix="latexml-output-", dir=a.out.parent
        ) as temp_out:
            out = Path(temp_out)
            (out / "source-manifest.json").write_bytes(manifest_bytes)
            with tarfile.open(
                out / "latexml-source.tar", "w", format=tarfile.PAX_FORMAT
            ) as tar:
                for name in sorted(expected):
                    data = (stage / name).read_bytes()
                    info = tarfile.TarInfo(name)
                    info.size = len(data)
                    info.mode = expected[name][0] & 0o777
                    info.mtime = epoch
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    tar.addfile(info, io.BytesIO(data))
            receipt = {
                "schema": "aelira-latexml-package-build-v1",
                "manifest_sha256": digest(manifest_bytes),
                "archive_sha256": digest((out / "latexml-source.tar").read_bytes()),
                "archive_size": (out / "latexml-source.tar").stat().st_size,
                "file_count": len(files),
                "upstream_commit": COMMIT,
                "upstream_tree": TREE,
            }
            if receipt["archive_sha256"] != ARCHIVE_SHA256:
                fail("deterministic source archive differs from verified output")
            inspect = subprocess.run(
                [str(a.docker), "image", "inspect", a.generator_image_id],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
                check=True,
            )
            image = json.loads(inspect.stdout)[0]
            if (
                image.get("Id") != a.generator_image_id
                or image.get("Architecture") != a.architecture
                or image.get("Os") != "linux"
            ):
                fail("native generator image identity mismatch")
            guard = [
                str(a.docker),
                "run",
                "--rm",
                "--user",
                f"{generator_uid}:{generator_gid}",
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
            ]
            tool_probe = subprocess.run(
                guard
                + [
                    "--entrypoint",
                    "/usr/bin/sha256sum",
                    a.generator_image_id,
                    "/usr/bin/perl",
                    "/usr/share/perl5/Parse/RecDescent.pm",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=60,
                check=True,
            )
            observed = [
                line.split()[0].decode() for line in tool_probe.stdout.splitlines()
            ]
            if observed != [a.perl_sha256, a.recdescent_sha256]:
                fail("native Perl generator identity mismatch")
            with tempfile.TemporaryDirectory(
                prefix="latexml-generated-"
            ) as temp_generated:
                generated_dir = Path(temp_generated)
                script = "set -eu; umask 022; perl -pe 's@__REVISION__@0db6f47@' /source/lib/LaTeXML/Version.in > /generated/Version.pm; cd /generated; perl -MParse::RecDescent - /source/lib/LaTeXML/MathGrammar LaTeXML::MathGrammar Parse::RecDescent; test -f MathGrammar.pm"
                subprocess.run(
                    guard
                    + [
                        "-v",
                        str(stage) + ":/source:ro",
                        "-v",
                        str(generated_dir) + ":/generated:rw",
                        "--workdir",
                        "/generated",
                        "--entrypoint",
                        "/bin/sh",
                        a.generator_image_id,
                        "-c",
                        script,
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=240,
                    check=True,
                )
                if {p.name for p in generated_dir.iterdir()} != {
                    "Version.pm",
                    "MathGrammar.pm",
                }:
                    fail("generated module set mismatch")
                generated_meta = {}
                for name, expected_hash in GENERATED.items():
                    module = generated_dir / Path(name).name
                    if (
                        module.is_symlink()
                        or not module.is_file()
                        or digest(module.read_bytes()) != expected_hash
                    ):
                        fail("generated module bytes mismatch")
                    data = module.read_bytes()
                    generated_meta[name] = {
                        "sha256": digest(data),
                        "size": len(data),
                        "mode": 0o644,
                    }
                runtime = {
                    "schema": "aelira-latexml-runtime-v2",
                    "upstream_origin": ORIGIN,
                    "upstream_commit": COMMIT,
                    "upstream_tree": TREE,
                    "makefile_sha256": MAKEFILE_SHA256,
                    "source_manifest_sha256": MANIFEST_SHA256,
                    "source_archive_sha256": ARCHIVE_SHA256,
                    "revision": COMMIT[:7],
                    "templates": TEMPLATES,
                    "generator": {
                        "image_id": a.generator_image_id,
                        "architecture": a.architecture,
                        "perl_sha256": a.perl_sha256,
                        "recdescent_sha256": a.recdescent_sha256,
                    },
                    "generated": generated_meta,
                    "source_file_count": FILE_COUNT,
                    "runtime_file_count": FILE_COUNT + len(GENERATED),
                }
                runtime_bytes = (
                    json.dumps(runtime, sort_keys=True, separators=(",", ":")) + "\n"
                ).encode()
                (out / "runtime-manifest.json").write_bytes(runtime_bytes)
                with tarfile.open(
                    out / "latexml-runtime.tar", "w", format=tarfile.PAX_FORMAT
                ) as tar:
                    for name in sorted(set(expected) | set(GENERATED)):
                        data = (
                            (generated_dir / Path(name).name).read_bytes()
                            if name in GENERATED
                            else (stage / name).read_bytes()
                        )
                        info = tarfile.TarInfo(name)
                        info.size = len(data)
                        info.mode = (
                            0o644 if name in GENERATED else expected[name][0] & 0o777
                        )
                        info.mtime = epoch
                        info.uid = info.gid = 0
                        info.uname = info.gname = ""
                        tar.addfile(info, io.BytesIO(data))
                receipt.update(
                    {
                        "schema": "aelira-latexml-package-build-v2",
                        "runtime_manifest_sha256": digest(runtime_bytes),
                        "runtime_archive_sha256": digest(
                            (out / "latexml-runtime.tar").read_bytes()
                        ),
                        "runtime_archive_size": (out / "latexml-runtime.tar")
                        .stat()
                        .st_size,
                        "runtime_file_count": FILE_COUNT + len(GENERATED),
                    }
                )
            (out / "build-receipt.json").write_text(
                json.dumps(receipt, sort_keys=True, indent=2) + "\n"
            )
            os.rename(out, a.out)
    print(json.dumps(receipt, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (
        ValueError,
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        tarfile.TarError,
    ) as exc:
        print(f"latexml package refused: {exc}", file=sys.stderr)
        sys.exit(2)
